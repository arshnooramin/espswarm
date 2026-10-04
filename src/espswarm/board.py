"""Board: a handle for one ESP32 on a Client's shared connection."""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .errors import (
    BoardOffline,
    BoardRestarted,
    NotConnected,
    RequestTimeout,
    SwarmError,
    board_error,
)
from .protocol import Response, Status, Topics, encode_request, new_request_id

if TYPE_CHECKING:
    from .client import Client


class _PendingRequest:
    def __init__(self, session: str) -> None:
        self.session = session
        self.done = threading.Event()
        self.response: Response | None = None
        self.error: SwarmError | None = None


class Board:
    """One board, reached through a Client. Get it from `client.board(id)`.

    Calls are thread-safe. State is guarded by the client's lock.
    """

    def __init__(self, client: "Client", board_id: str) -> None:
        self.board_id = board_id
        self.topics = Topics.for_board(board_id)
        self._client = client
        self._state = client._state
        self._pending: dict[str, _PendingRequest] = {}
        self._status: Status | None = None
        # The session the caller is working in. It changes only after the
        # caller has been told about a restart.
        self._session: str | None = None
        self._responses_subscribed = False

    def __repr__(self) -> str:
        return f"<Board {self.board_id!r} online={self.online}>"

    @property
    def status(self) -> Status | None:
        """The latest status message, or None if none has arrived."""
        with self._state:
            return self._status

    @property
    def online(self) -> bool:
        with self._state:
            return self._status is not None and self._status.online

    @property
    def session(self) -> str | None:
        with self._state:
            return self._status.session if self._status else None

    @property
    def capabilities(self) -> tuple[str, ...]:
        with self._state:
            return self._status.capabilities if self._status else ()

    def wait_until_online(self, timeout: float | None = None) -> None:
        """Block until the board reports online; raise BoardOffline on timeout."""
        timeout = self._client.timeout if timeout is None else timeout
        with self._state:
            if not self._state.wait_for(lambda: self.online, timeout):
                raise BoardOffline(f"Board {self.board_id!r} did not come online")

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
        timeout = self._client.timeout if timeout is None else timeout
        with self._state:
            session = self._session_for_call()
        self._client._subscribe_responses(self)
        request_id = new_request_id()
        payload = encode_request(request_id, session, operation, args or {})
        pending = _PendingRequest(session)
        with self._state:
            self._pending[request_id] = pending
        try:
            for _ in range(retries + 1):
                self._client._publish(self.topics.request, payload)
                if pending.done.wait(timeout):
                    break
            else:
                raise RequestTimeout(
                    f"{operation} on {self.board_id!r} got no response within "
                    f"{timeout} s; it may or may not have run"
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

    # The methods below run with the client's lock held.

    def _session_for_call(self) -> str:
        if not self._client._connected:
            raise NotConnected("Client is not connected")
        if self._status is None or not self._status.online:
            raise BoardOffline(f"Board {self.board_id!r} is offline")
        if self._session is None:
            self._session = self._status.session
        elif self._status.session != self._session:
            self._session = self._status.session
            raise BoardRestarted(
                f"Board {self.board_id!r} restarted; configure pins again"
            )
        return self._session

    def _fail_pending(
        self,
        make_error: Callable[[], SwarmError],
        *,
        except_session: str | None = None,
    ) -> None:
        for pending in self._pending.values():
            if pending.session != except_session and not pending.done.is_set():
                pending.error = make_error()
                pending.done.set()

    def _apply_status(self, status: Status) -> bool:
        """Record a status message; return True if presence or session changed."""
        previous = self._status
        self._status = status
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
        return (
            previous is None
            or previous.online != status.online
            or previous.session != status.session
        )

    def _apply_response(self, response: Response) -> None:
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
