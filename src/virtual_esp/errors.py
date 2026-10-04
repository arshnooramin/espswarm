"""Exceptions raised by the Virtual ESP host library."""


class VirtualESPError(Exception):
    """Base class for all library errors."""


class ConnectionFailed(VirtualESPError):
    """The MQTT broker could not be reached or refused the connection."""


class NotConnected(VirtualESPError):
    """The board was used before connect() or after close()."""


class BoardOffline(VirtualESPError):
    """The board is not connected to the broker."""


class BoardRestarted(VirtualESPError):
    """The board rebooted, so its pin configuration was lost.

    Later calls use the new session; configure pins again before using them.
    """


class RequestTimeout(VirtualESPError, TimeoutError):
    """No response arrived in time; the operation may or may not have run."""


class ProtocolError(VirtualESPError):
    """A message from the board did not follow the protocol."""


class BoardError(VirtualESPError):
    """The board rejected or failed a request.

    `code` is the protocol error code, such as "invalid_args".
    """

    code = "board_error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class InvalidRequest(BoardError):
    code = "invalid_request"


class InvalidArguments(BoardError):
    code = "invalid_args"


class InvalidState(BoardError):
    code = "invalid_state"


class Busy(BoardError):
    code = "busy"


class UnsupportedOperation(BoardError):
    code = "unsupported_operation"


class HardwareError(BoardError):
    code = "hardware_error"


class UnknownOutcome(BoardError):
    """The board accepted the request but could not report its result."""

    code = "unknown_outcome"


_ERRORS_BY_CODE = {
    error.code: error
    for error in (
        InvalidRequest,
        InvalidArguments,
        InvalidState,
        Busy,
        UnsupportedOperation,
        HardwareError,
        UnknownOutcome,
    )
}


def board_error(code: str, message: str) -> BoardError:
    """Build the exception for an error response; unknown codes stay generic."""
    error = _ERRORS_BY_CODE.get(code)
    if error is None:
        return BoardError(message, code)
    return error(message)
