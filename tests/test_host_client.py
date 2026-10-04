import queue
import threading

import pytest
from host_fakes import FakeBroker

from espswarm import BoardOffline, Client, NotConnected


@pytest.fixture
def broker():
    broker = FakeBroker()
    broker.add_board("bench-1")
    broker.add_board("bench-2")
    broker.add_board("bench-3", online=False)
    return broker


@pytest.fixture
def client(broker):
    with Client(transport=broker, timeout=0.2) as client:
        yield client


def next_item(items):
    return items.get(timeout=1)


def test_discovers_boards_from_retained_status(client):
    assert [board.board_id for board in client.boards()] == [
        "bench-1",
        "bench-2",
        "bench-3",
    ]
    assert [board.board_id for board in client.boards(online=True)] == [
        "bench-1",
        "bench-2",
    ]
    assert [board.board_id for board in client.boards(online=False)] == ["bench-3"]


def test_boards_share_one_connection_and_stay_independent(client, broker):
    one, two = client.board("bench-1"), client.board("bench-2")
    one.call("gpio.configure", {"pin": 2, "mode": "output", "initial": 1})
    two.call("gpio.configure", {"pin": 2, "mode": "output"})
    assert broker.boards["bench-1"].machine.pins[2].value() == 1
    assert broker.boards["bench-2"].machine.pins[2].value() == 0
    assert client.board("bench-1") is one


def test_one_board_restarting_does_not_affect_others(client, broker):
    one, two = client.board("bench-1"), client.board("bench-2")
    one.info()
    broker.boards["bench-2"].reboot("boot2")
    assert one.info()["board_id"] == "bench-1"
    assert two.session == "boot2"


def test_offline_board_handle_is_returned_and_waits_for_online(client, broker):
    board = client.board("bench-3")
    assert not board.online
    with pytest.raises(BoardOffline):
        board.call("board.info")
    with pytest.raises(BoardOffline):
        board.wait_until_online(timeout=0.01)
    broker.boards["bench-3"].go_online()
    board.wait_until_online()
    assert board.info()["board_id"] == "bench-3"


def test_unknown_board_raises_after_timeout(client):
    with pytest.raises(BoardOffline, match="is it running"):
        client.board("missing", timeout=0.01)


def test_board_that_appears_later_is_found(client, broker):
    late = broker.add_board("late", online=None)
    timer = threading.Timer(0.02, late.go_online)
    timer.start()
    assert client.board("late", timeout=1).online
    timer.join()


def test_status_callbacks_report_presence_changes(broker):
    events = queue.Queue()
    client = Client(transport=broker)
    client.on_status(lambda board, status: events.put((board.board_id, status.online)))
    with client.connect():
        discovered = {next_item(events) for _ in range(3)}
        assert discovered == {
            ("bench-1", True),
            ("bench-2", True),
            ("bench-3", False),
        }
        broker.boards["bench-1"].go_offline()
        assert next_item(events) == ("bench-1", False)
        broker.boards["bench-1"].go_online()
        assert next_item(events) == ("bench-1", True)
        broker.boards["bench-2"].reboot("boot2")
        assert next_item(events) == ("bench-2", True)
        # Repeating an unchanged status is not a change.
        broker.boards["bench-2"].go_online()
        assert events.empty()


def test_callbacks_run_off_the_network_thread_and_may_call_boards(client, broker):
    results = queue.Queue()

    def on_status(board, status):
        if status.online:
            results.put((threading.current_thread().name, board.info()["board_id"]))

    client.on_status(on_status)
    broker.boards["bench-3"].go_online()
    thread_name, board_id = next_item(results)
    assert thread_name == "espswarm-callbacks"
    assert board_id == "bench-3"


def test_failing_callback_does_not_stop_later_callbacks(client, broker, caplog):
    results = queue.Queue()

    def broken(board, status):
        raise RuntimeError("callback bug")

    client.on_status(broken)
    client.on_status(lambda board, status: results.put(board.board_id))
    broker.boards["bench-3"].go_online()
    assert next_item(results) == "bench-3"
    assert "callback bug" in caplog.text


def test_foreign_topics_and_malformed_status_are_ignored(client, broker):
    broker.subscriptions.append("#")
    broker.deliver("other/app/status", b"{}")
    broker.deliver("espswarm/v1/bad id/status", b"{}")
    broker.deliver("espswarm/v1/bench-9/status", b"not json")
    assert [board.board_id for board in client.boards()] == [
        "bench-1",
        "bench-2",
        "bench-3",
    ]


def test_board_requires_connected_client(broker):
    client = Client(transport=broker)
    with pytest.raises(NotConnected):
        client.board("bench-1")


def test_client_can_reconnect_after_close(broker):
    client = Client(transport=broker, timeout=0.2)
    with client:
        client.board("bench-1").info()
    with client:
        assert client.board("bench-1").info()["board_id"] == "bench-1"
