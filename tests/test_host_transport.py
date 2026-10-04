import ssl
import threading
from types import SimpleNamespace

import paho.mqtt.client as mqtt
import pytest

from virtual_esp import BoardOffline, ConnectionFailed, MQTTTransport

SUCCESS = SimpleNamespace(is_failure=False)
FAILURE = SimpleNamespace(is_failure=True)


class FakeClient:
    """Stands in for paho's Client."""

    def __init__(self, connack=SUCCESS, suback=SUCCESS, unreachable=False):
        self.connack = connack
        self.suback = suback
        self.unreachable = unreachable
        self.connected = False
        self.subscriptions = []
        self.published = []
        self.credentials = None
        self.tls_context = None
        self.stopped = False
        self.mid = 0

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def tls_set_context(self, context):
        self.tls_context = context

    def reconnect_delay_set(self, minimum, maximum):
        pass

    def connect(self, host, port, keepalive):
        if self.unreachable:
            raise ConnectionRefusedError()
        self.address = (host, port, keepalive)

    def loop_start(self):
        self.reconnect()

    def reconnect(self):
        self.connected = not self.connack.is_failure
        self.on_connect(self, None, {}, self.connack, None)

    def subscribe(self, topic, qos):
        if not self.connected:
            return mqtt.MQTT_ERR_NO_CONN, None
        self.mid += 1
        self.subscriptions.append((topic, qos))
        if self.suback is not None:
            # paho delivers SUBACKs on its network thread, never inline.
            threading.Thread(
                target=self.on_subscribe,
                args=(self, None, self.mid, [self.suback], None),
            ).start()
        return mqtt.MQTT_ERR_SUCCESS, self.mid

    def publish(self, topic, payload, qos):
        self.published.append((topic, payload, qos))
        return SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)

    def is_connected(self):
        return self.connected

    def deliver(self, topic, payload):
        self.on_message(self, None, SimpleNamespace(topic=topic, payload=payload))

    def disconnect(self):
        self.connected = False

    def loop_stop(self):
        self.stopped = True


def connected_transport(client=None, **options):
    client = client or FakeClient()
    transport = MQTTTransport("broker", client=client, timeout=0.05, **options)
    messages = []
    transport.connect(lambda topic, payload: messages.append((topic, payload)))
    return transport, client, messages


def test_connect_subscribe_publish_and_receive():
    transport, client, messages = connected_transport()
    assert client.address == ("broker", 1883, 30)
    transport.subscribe("a/response")
    transport.publish("a/request", b"{}")
    client.deliver("a/response", b"reply")
    assert client.subscriptions == [("a/response", 1)]
    assert client.published == [("a/request", b"{}", 1)]
    assert messages == [("a/response", b"reply")]


def test_reconnect_restores_subscriptions():
    transport, client, _ = connected_transport()
    transport.subscribe("a/response")
    transport.subscribe("a/status")
    client.subscriptions.clear()
    client.reconnect()
    assert client.subscriptions == [("a/response", 1), ("a/status", 1)]


def test_credentials_and_tls_are_configured():
    client = FakeClient()
    context = ssl.create_default_context()
    connected_transport(client, username="user", password="secret", tls=context)
    assert client.credentials == ("user", "secret")
    assert client.tls_context is context
    other = FakeClient()
    connected_transport(other, tls=True)
    assert isinstance(other.tls_context, ssl.SSLContext)


def test_password_without_username_is_rejected():
    with pytest.raises(ValueError):
        MQTTTransport("broker", password="secret", client=FakeClient())


@pytest.mark.parametrize(
    "client, message",
    [
        (FakeClient(unreachable=True), "Could not reach"),
        (FakeClient(connack=FAILURE), "refused the connection"),
    ],
)
def test_connection_failures(client, message):
    with pytest.raises(ConnectionFailed, match=message):
        connected_transport(client)


def test_unanswered_connect_times_out():
    client = FakeClient()
    client.loop_start = lambda: None
    with pytest.raises(ConnectionFailed, match="did not answer"):
        connected_transport(client)
    assert client.stopped


@pytest.mark.parametrize(
    "suback, message", [(FAILURE, "refused subscription"), (None, "did not confirm")]
)
def test_subscription_failures(suback, message):
    transport, _, _ = connected_transport(FakeClient(suback=suback))
    with pytest.raises(ConnectionFailed, match=message):
        transport.subscribe("a/response")


def test_publish_while_disconnected_fails():
    transport, client, _ = connected_transport()
    client.connected = False
    with pytest.raises(BoardOffline):
        transport.publish("a/request", b"{}")
    with pytest.raises(BoardOffline):
        transport.subscribe("a/response")


def test_close_stops_network_thread():
    transport, client, _ = connected_transport()
    transport.close()
    assert client.stopped
    assert not client.connected
