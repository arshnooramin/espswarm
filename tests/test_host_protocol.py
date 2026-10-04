import json

import pytest
import virtual_esp_board.limits as firmware_limits
import virtual_esp_board.protocol as firmware_protocol

from virtual_esp import errors
from virtual_esp.protocol import (
    MAX_PAYLOAD,
    TOPIC_PREFIX,
    VERSION,
    Topics,
    decode_response,
    decode_status,
    encode_request,
    new_request_id,
)


def test_constants_match_firmware():
    assert VERSION == firmware_protocol.VERSION
    assert MAX_PAYLOAD == firmware_limits.MAX_PAYLOAD
    assert TOPIC_PREFIX == firmware_protocol.TOPIC_PREFIX


def test_topics_match_firmware():
    firmware = firmware_protocol.Protocol("workbench", "boot1", [])
    topics = Topics.for_board("workbench")
    assert topics.request == firmware.request_topic.decode()
    assert topics.response == firmware.response_topic.decode()
    assert topics.status == firmware.status_topic.decode()
    assert topics.gpio_events == firmware.event_topic.decode()


@pytest.mark.parametrize("board_id", ["", "a" * 33, "board/1", "board#", "+"])
def test_invalid_board_ids_are_rejected(board_id):
    with pytest.raises(ValueError):
        Topics.for_board(board_id)


def test_request_ids_are_unique_and_accepted_by_the_board():
    ids = {new_request_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(
        firmware_protocol.is_token(value, firmware_limits.MAX_REQUEST_ID_LENGTH)
        for value in ids
    )


def test_encode_request_is_compact_and_bounded():
    payload = encode_request("r1", "boot1", "gpio.write", {"pin": 2, "level": 1})
    assert payload == (
        b'{"v":1,"id":"r1","session":"boot1","op":"gpio.write",'
        b'"args":{"pin":2,"level":1}}'
    )
    with pytest.raises(ValueError):
        encode_request("r1", "boot1", "x", {"data": "x" * MAX_PAYLOAD})
    with pytest.raises(ValueError):
        encode_request("r1", "boot1", "x", {"value": float("nan")})


def test_decode_success_and_error_responses():
    ok = decode_response(
        b'{"v":1,"id":"r1","session":"s","ok":true,"result":{"level":1}}'
    )
    assert (ok.request_id, ok.session, ok.ok, ok.result) == (
        "r1",
        "s",
        True,
        {"level": 1},
    )
    failed = decode_response(
        b'{"v":1,"id":null,"session":"s","ok":false,'
        b'"error":{"code":"invalid_request","message":"Invalid JSON"}}'
    )
    assert failed.request_id is None
    assert (failed.error_code, failed.error_message) == (
        "invalid_request",
        "Invalid JSON",
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff",
        b"[]",
        b'{"v":2,"id":"r1","session":"s","ok":true,"result":{}}',
        b'{"v":1,"id":"r1","ok":true,"result":{}}',
        b'{"v":1,"id":1,"session":"s","ok":true,"result":{}}',
        b'{"v":1,"id":"r1","session":"s","ok":1,"result":{}}',
        b'{"v":1,"id":"r1","session":"s","ok":true}',
        b'{"v":1,"id":"r1","session":"s","ok":false,"error":{}}',
    ],
)
def test_malformed_responses_raise_protocol_error(payload):
    with pytest.raises(errors.ProtocolError):
        decode_response(payload)


def test_decode_firmware_status():
    firmware = firmware_protocol.Protocol("workbench", "boot1", [])
    status = decode_status(firmware.status(True))
    assert status.session == "boot1"
    assert status.online
    assert status.info["board_id"] == "workbench"
    assert status.info["max_payload"] == MAX_PAYLOAD


@pytest.mark.parametrize(
    "payload",
    [
        b'{"v":1,"session":"s"}',
        b'{"v":1,"session":"s","online":1}',
        b'{"v":1,"session":"s","online":true,"capabilities":"gpio"}',
    ],
)
def test_malformed_status_raises_protocol_error(payload):
    with pytest.raises(errors.ProtocolError):
        decode_status(payload)


@pytest.mark.parametrize(
    "code, error_class",
    [
        ("invalid_args", errors.InvalidArguments),
        ("invalid_state", errors.InvalidState),
        ("busy", errors.Busy),
        ("unsupported_operation", errors.UnsupportedOperation),
        ("hardware_error", errors.HardwareError),
        ("unknown_outcome", errors.UnknownOutcome),
        ("invalid_request", errors.InvalidRequest),
    ],
)
def test_error_codes_map_to_exceptions(code, error_class):
    error = errors.board_error(code, "message")
    assert type(error) is error_class
    assert error.code == code
    assert str(error) == "message"


def test_unknown_error_codes_stay_generic():
    error = errors.board_error("new_code", "message")
    assert type(error) is errors.BoardError
    assert error.code == "new_code"


def test_request_timeout_is_a_timeout_error():
    assert issubclass(errors.RequestTimeout, TimeoutError)


def test_discovery_request_has_null_session():
    assert json.loads(encode_request("r1", None, "board.info", {}))["session"] is None
