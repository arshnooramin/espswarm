import json
import threading

import pytest
from host_fakes import LoopbackTransport

from virtual_esp import (
    Board,
    BoardOffline,
    BoardRestarted,
    InvalidState,
    NotConnected,
    RequestTimeout,
    UnsupportedOperation,
)


@pytest.fixture
def transport():
    return LoopbackTransport()


@pytest.fixture
def board(transport):
    with Board("workbench", transport=transport, timeout=0.5) as board:
        yield board


def configure_output(board, pin=2):
    return board.call("gpio.configure", {"pin": pin, "mode": "output"})


def test_connect_reads_session_and_capabilities_from_status(board, transport):
    assert board.online
    assert board.session == "boot1"
    assert board.capabilities == ("gpio", "gpio.events")
    assert transport.subscriptions == [transport.response_topic, transport.status_topic]


def test_info_returns_board_details(board):
    info = board.info()
    assert info["board_id"] == "workbench"
    assert info["capabilities"] == ["gpio", "gpio.events"]


def test_call_runs_operation_on_the_board(board, transport):
    configure_output(board)
    assert board.call("gpio.write", {"pin": 2, "level": 1}) == {}
    assert transport.machine.pins[2].value() == 1


def test_each_call_uses_a_new_request_id(board, transport):
    configure_output(board)
    for level in (1, 0, 1, 0):
        board.call("gpio.write", {"pin": 2, "level": level})
    ids = [json.loads(payload)["id"] for payload in transport.requests]
    assert len(set(ids)) == len(ids)
    assert transport.machine.pins[2].value() == 0


def test_board_errors_raise_matching_exceptions(board):
    with pytest.raises(InvalidState) as caught:
        board.call("gpio.write", {"pin": 2, "level": 1})
    assert caught.value.code == "invalid_state"
    with pytest.raises(UnsupportedOperation):
        board.call("spi.transfer")


def test_lost_request_times_out_with_unknown_outcome(board, transport):
    transport.drop_requests = 1
    with pytest.raises(RequestTimeout, match="may or may not have run"):
        board.call("board.info", timeout=0.05)


def test_retry_after_lost_response_runs_the_operation_once(board, transport):
    transport.drop_responses = 1
    result = board.call(
        "gpio.configure", {"pin": 2, "mode": "output"}, timeout=0.05, retries=1
    )
    assert result == {}
    assert transport.requests[0] == transport.requests[1]
    assert transport.machine.constructions == [2]


def test_negative_retries_are_rejected(board):
    with pytest.raises(ValueError):
        board.call("board.info", retries=-1)


def test_responses_for_other_requests_or_sessions_are_ignored(board, transport):
    transport.drop_requests = 1
    stray = b'{"v":1,"id":"someone-else","session":"boot1","ok":true,"result":{}}'
    transport.deliver(transport.response_topic, stray)
    transport.deliver(transport.response_topic, b"not json")
    with pytest.raises(RequestTimeout):
        board.call("board.info", timeout=0.05)


def test_restart_is_reported_once_then_new_session_is_used(board, transport):
    configure_output(board)
    transport.reboot("boot2")
    with pytest.raises(BoardRestarted):
        board.call("gpio.write", {"pin": 2, "level": 1})
    assert board.session == "boot2"
    with pytest.raises(InvalidState):
        board.call("gpio.write", {"pin": 2, "level": 1})
    configure_output(board)
    board.call("gpio.write", {"pin": 2, "level": 1})


def test_stale_session_response_is_reported_as_restart(board, transport):
    # The board rebooted, but its new status has not reached us yet.
    transport._boot("boot2")
    with pytest.raises(BoardRestarted):
        board.call("board.info")


def test_offline_board_fails_fast(board, transport):
    transport.go_offline()
    assert not board.online
    with pytest.raises(BoardOffline):
        board.call("board.info")


def run_in_thread(function):
    outcome = {}

    def target():
        try:
            outcome["result"] = function()
        except Exception as error:
            outcome["error"] = error

    thread = threading.Thread(target=target)
    thread.start()
    return thread, outcome


def wait_for_request(transport, count=1):
    for _ in range(1000):
        if len(transport.requests) >= count:
            return
        threading.Event().wait(0.001)
    raise AssertionError("request was not sent")


@pytest.mark.parametrize(
    "event, error",
    [
        (lambda transport: transport.go_offline(), BoardOffline),
        (lambda transport: transport.reboot("boot2"), BoardRestarted),
    ],
)
def test_waiting_call_is_woken_when_board_goes_away(board, transport, event, error):
    transport.drop_requests = 1
    thread, outcome = run_in_thread(lambda: board.call("board.info", timeout=5))
    wait_for_request(transport)
    event(transport)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert isinstance(outcome["error"], error)


def test_concurrent_calls_get_their_own_responses(board, transport):
    for pin in (2, 4, 5):
        configure_output(board, pin)
    threads = [
        run_in_thread(
            lambda pin=pin: board.call("gpio.write", {"pin": pin, "level": 1})
        )
        for pin in (2, 4, 5) * 10
    ]
    for thread, _ in threads:
        thread.join(timeout=2)
    assert all(outcome == {"result": {}} for _, outcome in threads)
    assert all(transport.machine.pins[pin].value() == 1 for pin in (2, 4, 5))


def test_connect_fails_when_board_never_published_status():
    transport = LoopbackTransport(online=None)
    with pytest.raises(BoardOffline, match="is it running"):
        Board("workbench", transport=transport, timeout=0.05).connect()
    assert transport.closed


def test_connect_fails_when_board_is_offline():
    transport = LoopbackTransport(online=False)
    with pytest.raises(BoardOffline, match="offline"):
        Board("workbench", transport=transport, timeout=0.05).connect()
    assert transport.closed


def test_calls_require_connection(transport):
    board = Board("workbench", transport=transport)
    with pytest.raises(NotConnected):
        board.call("board.info")
    board.connect()
    board.close()
    assert transport.closed
    with pytest.raises(NotConnected):
        board.call("board.info")
    board.close()
