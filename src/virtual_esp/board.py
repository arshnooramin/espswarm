"""Board: a connection to one ESP32 running the Virtual ESP agent."""

import logging
import ssl
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self

from .errors import (
    BoardOffline,
    BoardRestarted,
    NotConnected,
    ProtocolError,
    RequestTimeout,
    VirtualESPError,
    board_error,
)
from .protocol import (
    Response,
    Status,
    Topics,
    decode_response,
    decode_status,
    encode_request,
    new_request_id,
)
from .transport import MQTTTransport, Transport

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 5.0


class _PendingRequest:
    def __init__(self, session: str) -> None:
        self.session = session
        self.done = threading.Event()
        self.response: Response | None = None
        self.error: VirtualESPError | None = None


class Board:
    """Sends commands to one board and waits for its responses.

    Calls are thread-safe. Use as a context manager, or call connect() and
    close() yourself.
    """

    def __init__(
        self,
        board_id: str,
        host: str = "localhost",
        port: int = 1883,
        *,
        username: str | None = None,
        password: str | None = None,
        tls: bool | ssl.SSLContext = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Transport | None = None,
    ) -> None:
        self.board_id = board_id
        self.topics = Topics.for_board(board_id)
        self.timeout = timeout
        self._transport = transport or MQTTTransport(
            host, port, username=username, password=password, tls=tls
        )
        self._state = threading.Condition()
        self._pending: dict[str, _PendingRequest] = {}
        self._status: Status | None = None
        # The session the caller is working in; it changes only after the
        # caller has been told about a restart.
        self._session: str | None = None
        self._connected = False

    def connect(self) -> Self:
        """Connect to the broker and wait for the board's online status."""
        self._transport.connect(self._on_message)
        try:
            self._transport.subscribe(self.topics.response)
            self._transport.subscribe(self.topics.status)
            with self._state:
                if not self._state.wait_for(
                    lambda: self._status is not None, self.timeout
                ):
                    raise BoardOffline(
                        f"No status from board {self.board_id!r}; is it running?"
                    )
                assert self._status is not None
                if not self._status.online:
                    raise BoardOffline(f"Board {self.board_id!r} is offline")
                self._session = self._status.session
                self._connected = True
        except BaseException:
            self._transport.close()
            raise
        return self

    def close(self) -> None:
        with self._state:
            if not self._connected:
                return
            self._connected = False
            self._fail_pending(lambda: NotConnected("Board was closed"))
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

    @property
    def online(self) -> bool:
        with self._state:
            return self._status is not None and self._status.online

    @property
    def session(self) -> str | None:
        with self._state:
            return self._session

    @property
    def capabilities(self) -> tuple[str, ...]:
        with self._state:
            return self._status.capabilities if self._status else ()

    def info(self) -> dict[str, Any]:
        """Board ID, target, runtime, capabilities, and protocol limits."""
        return self.call("board.info")

    def call(
        self,
        operation: str,
        args: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        retries: int = 0,
    ) -> dict[str, Any]:
        """Run one operation on the board and return its result.

        Raises RequestTimeout if no response arrives; the operation may or may
        not have run. `retries` resends the identical request, which the board
        runs at most once while it remains in the board's response cache.
        """
        if retries < 0:
            raise ValueError("retries must not be negative")
        timeout = self.timeout if timeout is None else timeout
        with self._state:
            session = self._session_for_call()
        request_id = new_request_id()
        payload = encode_request(request_id, session, operation, args or {})
        pending = _PendingRequest(session)
        with self._state:
            self._pending[request_id] = pending
        try:
            for _ in range(retries + 1):
                self._transport.publish(self.topics.request, payload)
                if pending.done.wait(timeout):
                    break
            else:
                raise RequestTimeout(
                    f"{operation} got no response within {timeout} s; "
                    "it may or may not have run"
                )
        finally:
            with self._state:
                self._pending.pop(request_id, None)
        if pending.error is not None:
            raise pending.error
        response = pending.response
        assert response is not None
        if response.ok:
            assert response.result is not None
            return response.result
        if response.error_code == "stale_session":
            raise BoardRestarted(f"Board {self.board_id!r} restarted")
        raise board_error(response.error_code or "", response.error_message or "")

    def _session_for_call(self) -> str:
        if not self._connected or self._status is None or self._session is None:
            raise NotConnected("Call connect() first")
        if not self._status.online:
            raise BoardOffline(f"Board {self.board_id!r} is offline")
        if self._status.session != self._session:
            self._session = self._status.session
            raise BoardRestarted(
                f"Board {self.board_id!r} restarted; configure pins again"
            )
        return self._session

    def _fail_pending(
        self,
        make_error: Callable[[], VirtualESPError],
        *,
        except_session: str | None = None,
    ) -> None:
        """Wake waiting calls with an error; the caller holds the lock."""
        for pending in self._pending.values():
            if pending.session != except_session and not pending.done.is_set():
                pending.error = make_error()
                pending.done.set()

    def _on_message(self, topic: str, payload: bytes) -> None:
        # Runs on the transport's network thread; must never block.
        if topic == self.topics.response:
            self._on_response(payload)
        elif topic == self.topics.status:
            self._on_status(payload)

    def _on_response(self, payload: bytes) -> None:
        try:
            response = decode_response(payload)
        except ProtocolError:
            logger.debug("Ignoring malformed response from %s", self.board_id)
            return
        with self._state:
            pending = self._pending.get(response.request_id or "")
            # A stale_session error carries the board's new session, not ours.
            if (
                pending is not None
                and not pending.done.is_set()
                and (
                    pending.session == response.session
                    or response.error_code == "stale_session"
                )
            ):
                pending.response = response
                pending.done.set()

    def _on_status(self, payload: bytes) -> None:
        try:
            status = decode_status(payload)
        except ProtocolError:
            logger.warning("Ignoring malformed status from %s", self.board_id)
            return
        with self._state:
            self._status = status
            self._state.notify_all()
            if not status.online:
                self._fail_pending(
                    lambda: BoardOffline(f"Board {self.board_id!r} went offline")
                )
            else:
                # Requests sent to the previous boot can never be answered.
                self._fail_pending(
                    lambda: BoardRestarted(f"Board {self.board_id!r} restarted"),
                    except_session=status.session,
                )
