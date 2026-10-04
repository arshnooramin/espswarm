"""Protocol v1 messages: topics, request encoding, and response decoding.

See docs/protocol.md. This module does no I/O.
"""

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .errors import ProtocolError

VERSION = 1
MAX_PAYLOAD = 1024
TOPIC_PREFIX = f"espswarm/v{VERSION}/"
BOARD_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")
# Every board's status; subscribing to it discovers the fleet.
ALL_STATUS_TOPICS = TOPIC_PREFIX + "+/status"


def parse_topic(topic: str) -> tuple[str, str] | None:
    """Split a protocol topic into (board_id, suffix), or None if foreign."""
    if not topic.startswith(TOPIC_PREFIX):
        return None
    board_id, _, suffix = topic[len(TOPIC_PREFIX) :].partition("/")
    if not suffix or not BOARD_ID_PATTERN.fullmatch(board_id):
        return None
    return board_id, suffix


@dataclass(frozen=True)
class Topics:
    request: str
    response: str
    status: str
    gpio_events: str

    @classmethod
    def for_board(cls, board_id: str) -> "Topics":
        if not BOARD_ID_PATTERN.fullmatch(board_id):
            raise ValueError("board_id must be 1-32 ASCII letters, digits, '_' or '-'")
        base = TOPIC_PREFIX + board_id + "/"
        return cls(
            request=base + "request",
            response=base + "response",
            status=base + "status",
            gpio_events=base + "events/gpio",
        )


@dataclass(frozen=True)
class Status:
    """A board's retained status message."""

    session: str
    online: bool
    board_id: str
    target: str
    runtime: str
    agent_version: str
    capabilities: tuple[str, ...]
    max_payload: int
    response_cache_size: int


_STATUS_TEXT_FIELDS = ("board_id", "target", "runtime", "agent_version")
_STATUS_INTEGER_FIELDS = ("max_payload", "response_cache_size")


@dataclass(frozen=True)
class Response:
    request_id: str | None
    session: str
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


def new_request_id() -> str:
    """A unique request ID; reuse it only to retry the same request."""
    return uuid.uuid4().hex


def encode_request(
    request_id: str, session: str | None, operation: str, args: dict[str, Any]
) -> bytes:
    payload = json.dumps(
        {
            "v": VERSION,
            "id": request_id,
            "session": session,
            "op": operation,
            "args": args,
        },
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"Request exceeds {MAX_PAYLOAD} bytes")
    return payload


def _decode_object(payload: bytes) -> dict[str, Any]:
    try:
        message = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolError("Message is not valid JSON") from error
    if not isinstance(message, dict) or message.get("v") != VERSION:
        raise ProtocolError(f"Expected a protocol v{VERSION} object")
    if not isinstance(message.get("session"), str):
        raise ProtocolError("Message has no session")
    return message


def decode_status(payload: bytes) -> Status:
    message = _decode_object(payload)
    capabilities = message.get("capabilities")
    if (
        type(message.get("online")) is not bool
        or not isinstance(capabilities, list)
        or not all(isinstance(name, str) for name in capabilities)
        or not all(isinstance(message.get(key), str) for key in _STATUS_TEXT_FIELDS)
        or not all(type(message.get(key)) is int for key in _STATUS_INTEGER_FIELDS)
    ):
        raise ProtocolError("Invalid status message")
    # Unknown keys are ignored so newer agents stay readable.
    fields = {key: message[key] for key in _STATUS_TEXT_FIELDS + _STATUS_INTEGER_FIELDS}
    return Status(
        session=message["session"],
        online=message["online"],
        capabilities=tuple(capabilities),
        **fields,
    )


def decode_response(payload: bytes) -> Response:
    message = _decode_object(payload)
    request_id = message.get("id")
    ok = message.get("ok")
    if (request_id is not None and not isinstance(request_id, str)) or type(
        ok
    ) is not bool:
        raise ProtocolError("Invalid response message")
    if ok:
        result = message.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("Response has no result")
        return Response(request_id, message["session"], True, result=result)
    error = message.get("error")
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        raise ProtocolError("Response has no error code")
    return Response(
        request_id,
        message["session"],
        False,
        error_code=error["code"],
        error_message=str(error.get("message", "")),
    )
