"""Control a fleet of ESP32 boards from Python through an MQTT broker."""

from .board import Board
from .client import Client
from .errors import (
    BoardError,
    BoardOffline,
    BoardRestarted,
    BrokerDisconnected,
    Busy,
    ConnectionFailed,
    HardwareError,
    InvalidArguments,
    InvalidRequest,
    InvalidState,
    NotConnected,
    ProtocolError,
    RequestTimeout,
    SwarmError,
    UnknownOutcome,
    UnsupportedOperation,
)
from .protocol import Status
from .transport import MQTTTransport, Transport

__all__ = [
    "Board",
    "BoardError",
    "BoardOffline",
    "BoardRestarted",
    "BrokerDisconnected",
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
    "SwarmError",
]
