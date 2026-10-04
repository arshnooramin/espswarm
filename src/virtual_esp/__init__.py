"""Control a fleet of ESP32 boards from Python through an MQTT broker."""

from .board import Board
from .client import Client
from .errors import (
    BoardError,
    BoardOffline,
    BoardRestarted,
    Busy,
    ConnectionFailed,
    HardwareError,
    InvalidArguments,
    InvalidRequest,
    InvalidState,
    NotConnected,
    ProtocolError,
    RequestTimeout,
    UnknownOutcome,
    UnsupportedOperation,
    VirtualESPError,
)
from .protocol import Status
from .transport import MQTTTransport, Transport

__all__ = [
    "Board",
    "BoardError",
    "BoardOffline",
    "BoardRestarted",
    "Busy",
    "Client",
    "ConnectionFailed",
    "HardwareError",
    "InvalidArguments",
    "InvalidRequest",
    "InvalidState",
    "MQTTTransport",
    "NotConnected",
    "ProtocolError",
    "RequestTimeout",
    "Status",
    "Transport",
    "UnknownOutcome",
    "UnsupportedOperation",
    "VirtualESPError",
]
