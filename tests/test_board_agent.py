import json
from types import SimpleNamespace

import pytest
from board_fakes import Machine, Timer, request
from espswarm_agent.agent import (
    EVENTS_PER_STEP,
    INITIAL_BACKOFF_MS,
    STABLE_CONNECTION_MS,
    Agent,
    ConnectionSupervisor,
)
from espswarm_agent.gpio import GPIO
from espswarm_agent.protocol import Protocol
from espswarm_agent.settings import Settings


class Client:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.publications = []
        self.closed = False

    def connect(self, client_id, will_topic, will_payload, **kwargs):
        self.will = (will_topic, json.loads(will_payload))

    def subscribe(self, topic):
        self.subscription = topic

    def publish(self, topic, payload, qos=1, retain=False):
        self.publications.append((topic, json.loads(payload), qos, retain))

    def poll(self):
        return self.messages.pop(0) if self.messages else None

    def close(self):
        self.closed = True


def settings(**changes):
    return Settings(wifi_ssid="network", mqtt_host="broker", **changes)


@pytest.fixture
def machine():
    return Machine()


@pytest.fixture
def hardware(machine):
    return GPIO(machine)


@pytest.fixture
def protocol(hardware):
    return Protocol("workbench", "boot1", [hardware])


@pytest.fixture
def agent(protocol, hardware):
    return Agent(protocol, hardware.events, settings())


def test_start_announces_online_after_subscribing_and_sets_offline_will(
    agent, protocol
):
    client = Client()
    agent.start(client)
    assert client.subscription == protocol.request_topic
    assert not client.will[1]["online"]
    topic, status, qos, retain = client.publications[0]
    assert topic == protocol.status_topic
    assert (qos, retain) == (1, True)
    assert status["online"]


def test_retained_commands_and_other_board_topics_have_no_effect(
    agent, protocol, machine
):
    payload = request(op="gpio.configure", args={"pin": 2, "mode": "output"})
    client = Client(
        [
            (b"espswarm/v1/other/request", payload, False),
            (protocol.request_topic, payload, True),
        ]
    )
    agent.start(client)
    agent.step()
    agent.step()
    assert not machine.constructions
    assert client.publications[-1][1]["error"]["code"] == "retained_request"


def test_request_result_and_irq_events_use_separate_topics(
    agent, protocol, hardware, machine
):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})
    client = Client()
    agent.start(client)
    machine.pins[5].fire(1)
    client.messages.append((protocol.request_topic, request(), False))
    agent.step()
    response, event = client.publications[-2:]
    assert response[0] == protocol.response_topic
    assert response[1]["result"] == {"level": 1}
    assert event[0] == protocol.event_topic
    assert event[1]["pin"] == 5
    assert event[1]["level"] == 1
    assert event[1]["session"] == "boot1"
    assert event[2:] == (0, False)


def test_each_step_publishes_a_bounded_number_of_events(protocol):
    class IRQStorm:
        def pop(self):
            return 5, 1, 0

        def clear(self):
            pass

    agent = Agent(protocol, IRQStorm(), settings())
    client = Client()
    agent.start(client)
    agent.step()
    assert len(client.publications) == 1 + EVENTS_PER_STEP
    agent.step()
    sequences = [event["sequence"] for _, event, _, _ in client.publications[1:]]
    assert sequences == list(range(1, 2 * EVENTS_PER_STEP + 1))


def test_reconnect_preserves_cached_results_and_discards_offline_edges(
    agent, protocol, hardware, machine
):
    client = Client()
    agent.start(client)
    payload = request(op="gpio.configure", args={"pin": 2, "mode": "output"})
    client.messages.append((protocol.request_topic, payload, False))
    agent.step()
    hardware.events.put(5, 1)
    agent.close()
    assert client.closed
    assert hardware.events.pop() is None
    replacement = Client([(protocol.request_topic, payload, False)])
    agent.start(replacement)
    agent.step()
    assert machine.constructions == [2]
    assert replacement.publications[-1][1] == client.publications[-1][1]


def test_close_failure_still_discards_client_and_events(agent, hardware):
    class BrokenClient(Client):
        def close(self):
            raise OSError("already disconnected")

    agent.start(BrokenClient())
    hardware.events.put(5, 1)
    agent.close()
    assert agent.client is None
    assert hardware.events.pop() is None
    agent.close()


class Wlan:
    def __init__(self, connected=True):
        self.connected = connected
        self.disconnected = False

    def active(self, enabled):
        pass

    def isconnected(self):
        return self.connected

    def connect(self, ssid, password):
        pass

    def disconnect(self):
        self.disconnected = True


def test_wifi_attempt_has_deadline_and_disconnects_on_timeout():
    timer = Timer()
    wlan = Wlan(connected=False)
    supervisor = ConnectionSupervisor(
        settings(wifi_timeout=1), wlan, agent=None, timer=timer
    )
    with pytest.raises(OSError):
        supervisor.connect_wifi()
    assert timer.now == 1000
    assert wlan.disconnected


def test_supervisor_retries_with_bounded_backoff_and_closes_each_attempt():
    delays = []
    attempts = []
    closes = []

    class RetryTimer(Timer):
        def sleep_ms(self, delay):
            delays.append(delay)
            if len(delays) == 7:
                raise KeyboardInterrupt

    def unavailable(settings):
        attempts.append(settings)
        raise OSError("offline")

    agent = SimpleNamespace(
        start=lambda client: None, close=lambda: closes.append(True)
    )
    supervisor = ConnectionSupervisor(
        settings(), Wlan(), agent, unavailable, RetryTimer()
    )
    with pytest.raises(KeyboardInterrupt):
        supervisor.run()
    assert delays == [1000, 2000, 4000, 8000, 16000, 30000, 30000]
    assert len(attempts) == 7
    assert len(closes) == 8


def test_stable_connection_resets_backoff_and_wifi_loss_ends_session():
    timer = Timer()
    wlan = Wlan()
    steps = []

    def step():
        steps.append(timer.now)
        timer.now += STABLE_CONNECTION_MS // 2
        if len(steps) == 3:
            wlan.connected = False

    agent = SimpleNamespace(start=lambda client: None, step=step)
    supervisor = ConnectionSupervisor(
        settings(), wlan, agent, lambda settings: object(), timer
    )
    supervisor.backoff = 16000
    with pytest.raises(OSError, match="Wi-Fi disconnected"):
        supervisor._run_session()
    assert len(steps) == 3
    assert supervisor.backoff == INITIAL_BACKOFF_MS


def test_short_connection_keeps_backoff():
    timer = Timer()
    wlan = Wlan()

    def step():
        wlan.connected = False

    agent = SimpleNamespace(start=lambda client: None, step=step)
    supervisor = ConnectionSupervisor(
        settings(), wlan, agent, lambda settings: object(), timer
    )
    supervisor.backoff = 16000
    with pytest.raises(OSError):
        supervisor._run_session()
    assert supervisor.backoff == 16000


def test_tls_session_syncs_clock_before_connecting(monkeypatch):
    order = []
    monkeypatch.setattr(
        ConnectionSupervisor, "_sync_clock", lambda self: order.append("ntp")
    )
    wlan = Wlan()

    def connector(settings):
        order.append("mqtt")
        wlan.connected = False
        return object()

    agent = SimpleNamespace(start=lambda client: None)
    supervisor = ConnectionSupervisor(
        settings(mqtt_tls=True), wlan, agent, connector, Timer()
    )
    with pytest.raises(OSError):
        supervisor._run_session()
    assert order == ["ntp", "mqtt"]
