"""Runs user callbacks off the MQTT network thread."""

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

STOP_TIMEOUT_SECONDS = 5.0


class CallbackDispatcher:
    """One worker thread that runs callbacks in the order they were submitted.

    Callbacks must not run on the network thread: one that waits for a board
    response would block the thread that delivers it.
    """

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[tuple[Callable[..., Any], tuple] | None]
        self._queue = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="virtual-esp-callbacks", daemon=True
        )
        self._thread.start()

    def submit(self, function: Callable[..., Any], *args: Any) -> None:
        self._queue.put((function, args))

    def stop(self) -> None:
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._queue.put(None)
        # A callback may close the client; it cannot wait for itself.
        if thread is not threading.current_thread():
            thread.join(STOP_TIMEOUT_SECONDS)

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            function, args = item
            try:
                function(*args)
            except Exception:
                logger.exception("Callback %r raised", function)
