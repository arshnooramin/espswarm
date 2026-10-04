import json
import threading

import pytest
from host_fakes import FakeBroker

from virtual_esp import (
    BoardOffline,
    BoardRestarted,
    Client,
    InvalidState,
    NotConnected,
    RequestTimeout,
    UnsupportedOperation,
)


@pytest.fixture
def broker():
    broker = FakeBroker()
    broker.add_board("workbench")
    return broker


@pytest.fixture
def fake(broker):
    return broker.boards["workbench"]


@pytest.fixture
def client(broker):
    with Client(transport=broker, timeout=0.5) as client:
        yield client


@pytest.fixture
def board(client):
    return client.board("workbench")


def configure_output(board, pin=2):
    return board.call("gpio.configure", {"pin": pin, "mode": "output"})


def test_board_reports_status(board):
    assert board.online
    assert board.session == "boot1"
    assert board.capabilities == ("gpio", "gpio.events")
    assert board.status.info["board_id"] == "workbench"


def test_info_returns_board_details(board):
    info = board.info()
    assert info["board_id"] == "workbench"
    assert info["capabilities"] == ["gpio", "gpio.events"]


def test_call_runs_operation_on_the_board(board, fake):
    configure_output(board)
    assert board.call("gpio.write", {"pin": 2, "level": 1}) == {}
    assert fake.machine.pins[2].value() == 1


def test_response_topic_is_subscribed_on_first_call(board, broker, fake):
    assert fake.response_topic not in broker.subscriptions
    board.info()
    board.info()
    assert broker.subscriptions.count(fake.response_topic) == 1


def test_each_call_uses_a_new_request_id(board, fake):
    configure_output(board)
    for level in (1, 0, 1, 0):
        board.call("gpio.write", {"pin": 2, "level": level})
    ids = [json.loads(payload)["id"] for payload in fake.requests]
    assert len(set(ids)) == len(ids)
    assert fake.machine.pins[2].value() == 0


def test_board_errors_raise_matching_exceptions(board):
    with pytest.raises(InvalidState) as caught:
        board.call("gpio.write", {"pin": 2, "level": 1})
    assert caught.value.code == "invalid_state"
    with pytest.raises(UnsupportedOperation):
        board.call("spi.transfer")


def test_lost_request_times_out_with_unknown_outcome(board, fake):
    fake.drop_requests = 1
    with pytest.raises(RequestTimeout, match="may or may not have run"):
        board.call("board.info", timeout=0.05)


def test_retry_after_lost_response_runs_the_operation_once(board, fake):
    fake.drop_responses = 1
    result = board.call(
        "gpio.configure", {"pin": 2, "mode": "output"}, timeout=0.05, retries=1
    )
    assert result == {}
    assert fake.requests[0] == fake.requests[1]
    assert fake.machine.constructions == [2]


def test_negative_retries_are_rejected(board):
    with pytest.raises(ValueError):
        board.call("board.info", retries=-1)


def test_responses_for_other_requests_are_ignored(board, broker, fake):
    board.info()
    fake.drop_requests = 1
    stray = b'{"v":1,"id":"someone-else","session":"boot1","ok":true,"result":{}}'
    broker.deliver(fake.response_topic, stray)
    broker.deliver(fake.response_topic, b"not json")
    with pytest.raises(RequestTimeout):
        board.call("board.info", timeout=0.05)


def test_restart_is_reported_once_then_new_session_is_used(board, fake):
    configure_output(board)
    fake.reboot("boot2")
    with pytest.raises(BoardRestarted):
        board.call("gpio.write", {"pin": 2, "level": 1})
    assert board.session == "boot2"
    with pytest.raises(InvalidState):
        board.call("gpio.write", {"pin": 2, "level": 1})
    configure_output(board)
    board.call("gpio.write", {"pin": 2, "level": 1})


def test_stale_session_response_is_reported_as_restart(board, fake):
    board.info()
    # The board rebooted, but its new status has not reached us yet.
    fake._boot("boot2")
    with pytest.raises(BoardRestarted):
        board.call("board.info")


def test_offline_board_fails_fast_and_recovers(board, fake):
    fake.go_offline()
    assert not board.online
    with pytest.raises(BoardOffline):
        board.call("board.info")
    fake.go_online()
    board.wait_until_online(timeout=0.05)
    assert board.info()["board_id"] == "workbench"


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


def wait_for_request(fake, count=1):
    for _ in range(1000):
        if len(fake.requests) >= count:
            return
        threading.Event().wait(0.001)
    raise AssertionError("request was not sent")


@pytest.mark.parametrize(
    "event, error",
    [
        (lambda fake: fake.go_offline(), BoardOffline),
        (lambda fake: fake.reboot("boot2"), BoardRestarted),
    ],
)
def test_waiting_call_is_woken_when_board_goes_away(board, fake, event, error):
    fake.drop_requests = 1
    thread, outcome = run_in_thread(lambda: board.call("board.info", timeout=5))
    wait_for_request(fake)
    event(fake)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert isinstance(outcome["error"], error)


def test_concurrent_calls_get_their_own_responses(board, fake):
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
    assert all(fake.machine.pins[pin].value() == 1 for pin in (2, 4, 5))


def test_closing_the_client_disables_boards(broker):
    client = Client(transport=broker).connect()
    board = client.board("workbench")
    client.close()
    assert broker.closed
    with pytest.raises(NotConnected):
        board.call("board.info")
    client.close()
