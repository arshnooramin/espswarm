import json

import pytest
from board_fakes import request
from espswarm_agent.json_codec import loads
from espswarm_agent.peripheral import Peripheral
from espswarm_agent.protocol import (
    CACHE_SIZE,
    EVENT_SEQUENCE_MODULUS,
    Protocol,
)
from espswarm_agent.validation import CommandError


class Hardware(Peripheral):
    capabilities = ("gpio",)
    operations = {
        "gpio.read": ("read", ("pin",), ()),
        "gpio.write": ("write", ("pin", "level"), ()),
    }

    def __init__(self):
        self.calls = []
        self.failure = None

    def _record(self, operation):
        self.calls.append(operation)
        if self.failure:
            raise self.failure
        return {"level": 1}

    def read(self, pin):
        return self._record("gpio.read")

    def write(self, pin, level):
        return self._record("gpio.write")


@pytest.fixture
def hardware():
    return Hardware()


@pytest.fixture
def protocol(hardware):
    return Protocol("workbench", "boot1", [hardware])


def reply(protocol, payload, **kwargs):
    return json.loads(protocol.handle(payload, **kwargs))


def test_result_is_correlated_with_request_and_boot(protocol):
    assert reply(protocol, request()) == {
        "v": 1,
        "id": "req1",
        "session": "boot1",
        "ok": True,
        "result": {"level": 1},
    }


def test_identical_retries_execute_once(protocol, hardware):
    payload = request(op="gpio.write", args={"pin": 2, "level": 1})
    response = protocol.handle(payload)
    for _ in range(5):
        assert protocol.handle(payload) == response
    assert len(hardware.calls) == 1


def test_reusing_id_for_different_command_has_no_effect(protocol, hardware):
    reply(protocol, request())
    response = reply(protocol, request(args={"pin": 12}))
    assert response["error"]["code"] == "id_conflict"
    assert len(hardware.calls) == 1


def test_cache_is_bounded_and_oldest_entry_is_evicted(protocol, hardware):
    for index in range(CACHE_SIZE + 1):
        reply(protocol, request(identifier="request" + str(index)))
    reply(protocol, request(identifier="request1"))
    assert len(hardware.calls) == CACHE_SIZE + 1
    reply(protocol, request(identifier="request0"))
    assert len(hardware.calls) == CACHE_SIZE + 2


@pytest.mark.parametrize(
    "payload, kwargs, code",
    [
        (request(), {"retained": True}, "retained_request"),
        (request(session="previous-boot"), {}, "stale_session"),
    ],
)
def test_retained_and_stale_commands_do_not_execute(
    protocol, hardware, payload, kwargs, code
):
    assert reply(protocol, payload, **kwargs)["error"]["code"] == code
    assert not hardware.calls


def test_info_can_discover_session_without_a_prior_session(protocol, hardware):
    response = reply(protocol, request(op="board.info", session=None, args={}))
    assert response["session"] == "boot1"
    assert response["result"]["capabilities"] == ["gpio"]
    assert not hardware.calls


def test_unknown_operation_is_unsupported(protocol, hardware):
    response = reply(protocol, request(op="spi.transfer", args={}))
    assert response["error"]["code"] == "unsupported_operation"
    assert not hardware.calls


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"[]",
        b"null",
        b"{}",
        b"{",
        b"x" * 1025,
        request(v=True),
        request(v=2),
        request(id="bad/id"),
        request(id="a" * 65),
        request(op=1),
        request(args=[]),
        request(unknown=1),
        request(session=None),
    ],
)
def test_invalid_envelopes_do_not_execute(protocol, hardware, payload):
    assert not reply(protocol, payload)["ok"]
    assert not hardware.calls


@pytest.mark.parametrize(
    "payload",
    [
        request() + b"{}",
        request() + b"\x00",
        request().replace(b'"v": 1', b'"v": 1, "v": 1'),
        request().replace(b'"pin": 5', b'"pin": 5, "pin": 6'),
        request().replace(b'"id": "req1"', b'"id": "req1\\u0000other"'),
        request().replace(b'"pin": 5', b'"pin": NaN'),
        request().replace(b'"pin": 5', b'"pin": 1e999'),
        b'{"\xff":1}',
    ],
)
def test_malformed_json_and_duplicate_keys_do_not_execute(protocol, hardware, payload):
    assert reply(protocol, payload)["error"]["code"] == "invalid_request"
    assert not hardware.calls


@pytest.mark.parametrize(
    "failure, code",
    [
        (CommandError("invalid_args", "Invalid pin"), "invalid_args"),
        (OSError("private detail"), "hardware_error"),
        (RuntimeError("private detail"), "internal_error"),
    ],
)
def test_hardware_failure_is_cached(protocol, hardware, failure, code):
    hardware.failure = failure
    payload = request()
    response = protocol.handle(payload)
    assert json.loads(response)["error"]["code"] == code
    assert b"private detail" not in response
    assert protocol.handle(payload) == response
    assert len(hardware.calls) == 1


@pytest.mark.parametrize(
    "result, code",
    [
        ({"data": "x" * 1024}, "response_too_large"),
        ({"data": object()}, "internal_error"),
    ],
)
def test_unencodable_results_are_cached_as_errors(
    protocol, hardware, monkeypatch, result, code
):
    monkeypatch.setattr(
        hardware, "read", lambda pin: hardware.calls.append(1) or result
    )
    first = protocol.handle(request())
    assert json.loads(first)["error"]["code"] == code
    assert protocol.handle(request()) == first
    assert hardware.calls == [1]


def test_topics_and_status_are_board_specific(protocol, hardware):
    other = Protocol("other", "boot2", [hardware])
    assert protocol.request_topic == b"espswarm/v1/workbench/request"
    assert protocol.request_topic != other.request_topic
    status = json.loads(protocol.status(False))
    assert not status["online"]
    assert status["session"] == "boot1"


def test_event_sequence_increments_and_wraps(protocol):
    first = json.loads(protocol.event(5, 1, 0))
    assert first == {
        "v": 1,
        "session": "boot1",
        "event": "gpio.edge",
        "pin": 5,
        "level": 1,
        "sequence": 1,
        "dropped": 0,
    }
    protocol._sequence = EVENT_SEQUENCE_MODULUS - 1
    assert json.loads(protocol.event(5, 0, 0))["sequence"] == 0


def test_duplicate_operations_across_peripherals_are_rejected(hardware):
    with pytest.raises(ValueError):
        Protocol("workbench", "boot1", [hardware, Hardware()])


@pytest.mark.parametrize(
    "value",
    [
        {
            "escaped": 'quote" slash\\ newline\n',
            "unicode": "🌱",
            "values": [True, False, None, 0, -2, 1.25, 1e-05],
        },
        {},
        [],
        "text",
        42,
    ],
)
def test_json_standard_values_and_escapes(value):
    assert loads(json.dumps(value).encode()) == value


@pytest.mark.parametrize(
    "payload",
    [
        b"01",
        b"-01",
        b"1.",
        b"1e",
        b"1e+",
        b"+1",
        b"NaN",
        b"Infinity",
        b'"\\x01"',
        b'"a\nb"',
        b'"\\u12xz"',
        b'{"a":1,"\\u0061":2}',
        b'{"a" 1}',
        b"[1,]",
        b'{"a":1,}',
        b"[" * 10 + b"0" + b"]" * 10,
    ],
)
def test_json_rejects_invalid_or_ambiguous_input(payload):
    with pytest.raises(ValueError):
        loads(payload)
