"""Client: one broker connection shared by every board it talks to."""

import logging
import ssl
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Self

from .board import Board
from .dispatcher import CallbackDispatcher
from .errors import BoardOffline, NotConnected, ProtocolError
from .protocol import (
    ALL_STATUS_TOPICS,
    Status,
    decode_response,
    decode_status,
    parse_topic,
)
from .transport import MQTTTransport, Transport

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 5.0

StatusCallback = Callable[[Board, Status], None]


class Client:
    """Connects to an MQTT broker, discovers boards, and hands out Board handles.

    Use as a context manager, or call connect() and close() yourself.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        *,
        username: str | None = None,
        password: str | None = None,
        tls: bool | ssl.SSLContext = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Transport | None = None,
    ) -> None:
        self.timeout = timeout
        self._transport = transport or MQTTTransport(
            host, port, username=username, password=password, tls=tls
        )
        # Guards all client and board state. Never held while waiting on the
        # network, because the network thread needs it to deliver messages.
        self._state = threading.Condition()
        # Serializes response subscriptions without blocking the network thread.
        self._subscribe_lock = threading.Lock()
        self._boards: dict[str, Board] = {}
        self._status_callbacks: list[StatusCallback] = []
        self._dispatcher = CallbackDispatcher()
        self._connected = False

    def connect(self) -> Self:
        """Connect and start discovering boards from their status messages.

        Does nothing if already connected, so `with client.connect():` works.
        """
        with self._state:
            if self._connected:
                return self
        self._dispatcher.start()
        try:
            self._transport.connect(self._on_message)
            with self._state:
                self._connected = True
            self._transport.subscribe(ALL_STATUS_TOPICS)
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        with self._state:
            was_connected, self._connected = self._connected, False
            for board in self._boards.values():
                board._fail_pending(lambda: NotConnected("Client was closed"))
                board._responses_subscribed = False
        self._dispatcher.stop()
        if was_connected:
            self._transport.close()

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def boards(self, *, online: bool | None = None) -> list[Board]:
        """Boards that have published status, optionally filtered by presence.

        Retained statuses arrive just after connecting, so call this a moment
        later, or use board() or on_status() to wait for specific boards.
        """
        with self._state:
            return sorted(
                (
                    board
                    for board in self._boards.values()
                    if board._status is not None
                    and (online is None or board._status.online == online)
                ),
                key=lambda board: board.board_id,
            )

    def board(self, board_id: str, *, timeout: float | None = None) -> Board:
        """The handle for one board, after waiting for its first status.

        Returns the handle even if the board is offline. Raises BoardOffline if
        the board has never published status within `timeout`.
        """
        timeout = self.timeout if timeout is None else timeout
        with self._state:
            if not self._connected:
                raise NotConnected("Call connect() first")
            board = self._board(board_id)
            if not self._state.wait_for(lambda: board._status is not None, timeout):
                raise BoardOffline(f"No status from board {board_id!r}; is it running?")
            return board

    def on_status(self, callback: StatusCallback) -> None:
        """Call `callback(board, status)` when a board appears, goes online or
        offline, or restarts.

        Callbacks run in order on a dedicated thread, so they may call boards.
        """
        with self._state:
            self._status_callbacks.append(callback)

    # Used by Board.

    def _board(self, board_id: str) -> Board:
        board = self._boards.get(board_id)
        if board is None:
            board = self._boards[board_id] = Board(self, board_id)
        return board

    def _publish(self, topic: str, payload: bytes) -> None:
        self._transport.publish(topic, payload)

    def _subscribe_responses(self, board: Board) -> None:
        with self._subscribe_lock:
            with self._state:
                if board._responses_subscribed:
                    return
            self._transport.subscribe(board.topics.response)
            with self._state:
                board._responses_subscribed = True

    # Network thread.

    def _on_message(self, topic: str, payload: bytes) -> None:
        parsed = parse_topic(topic)
        if parsed is None:
            return
        board_id, suffix = parsed
        if suffix == "status":
            self._on_status(board_id, payload)
        elif suffix == "response":
            self._on_response(board_id, payload)

    def _on_status(self, board_id: str, payload: bytes) -> None:
        try:
            status = decode_status(payload)
        except ProtocolError:
            logger.warning("Ignoring malformed status from %s", board_id)
            return
        with self._state:
            board = self._board(board_id)
            changed = board._apply_status(status)
            self._state.notify_all()
            callbacks = list(self._status_callbacks) if changed else []
        for callback in callbacks:
            self._dispatcher.submit(callback, board, status)

    def _on_response(self, board_id: str, payload: bytes) -> None:
        try:
            response = decode_response(payload)
        except ProtocolError:
            logger.debug("Ignoring malformed response from %s", board_id)
            return
        with self._state:
            board = self._boards.get(board_id)
            if board is not None:
                board._apply_response(response)
