import sys
from types import SimpleNamespace

import pytest
from board_fakes import Poller, Stream, Timer, publication
from espswarm_agent.mqtt import MAX_PENDING, MQTT, MQTTError, open_connection
from espswarm_agent.settings import Settings


def connection(incoming=b"", chunk_size=4096, timeout_ms=5000):
    timer = Timer()
    stream = Stream(incoming, chunk_size, timer)
    client = MQTT(
        stream, timeout_ms=timeout_ms, poller=Poller(stream, timer), timer=timer
    )
    return client, stream, timer


def test_connect_uses_clean_session_and_retained_qos1_will():
    client, stream, _ = connection(b" \x02\x00\x00")
    client.connect(b"board", b"status", b"offline")
    assert (
        bytes(stream.outgoing) == b'\x10"\x00\x04MQTT\x04.\x00\x1e'
        b"\x00\x05board\x00\x06status\x00\x07offline"
    )


def test_connect_sends_credentials():
    client, stream, _ = connection(b" \x02\x00\x00")
    client.connect(b"board", b"status", b"offline", username="user", password="pw")
    assert stream.outgoing[9] & 0xC0 == 0xC0
    assert bytes(stream.outgoing).endswith(b"\x00\x04user\x00\x02pw")


@pytest.mark.parametrize("packet", [b" \x02\x00\x05", b" \x02\x01\x00", b" \x01\x00"])
def test_connect_refusal_and_invalid_ack_fail(packet):
    client, _, _ = connection(packet)
    with pytest.raises(MQTTError):
        client.connect(b"board", b"status", b"offline")


def test_subscribe_waits_for_matching_suback():
    client, stream, _ = connection(b"\x90\x03\x00\x01\x01")
    client.subscribe(b"request")
    assert bytes(stream.outgoing) == b"\x82\x0c\x00\x01\x00\x07request\x01"


def test_refused_subscription_fails():
    client, _, _ = connection(b"\x90\x03\x00\x01\x80")
    with pytest.raises(MQTTError):
        client.subscribe(b"request")


def test_publish_waits_for_puback_and_handles_partial_io():
    client, stream, _ = connection(b"@\x02\x00\x01", chunk_size=1)
    client.publish(b"response", b"ok")
    assert bytes(stream.outgoing) == b"2\x0e\x00\x08response\x00\x01ok"


def test_messages_received_during_ack_wait_are_deferred():
    message = publication(b"request", b"command", retained=True)
    client, stream, _ = connection(message + b"@\x02\x00\x01")
    client.publish(b"response", b"ok")
    assert client.poll() == (b"request", b"command", True)
    assert bytes(stream.outgoing).endswith(b"@\x02\x00\x07")


def test_incoming_queue_overflow_fails_connection():
    client, _, _ = connection(publication(b"request", b"command") * (MAX_PENDING + 1))
    with pytest.raises(MQTTError):
        client.publish(b"response", b"ok")
    assert len(client.pending) == MAX_PENDING


def test_fragmented_publication_is_reassembled():
    client, _, _ = connection(publication(b"request", b"x" * 500), chunk_size=1)
    assert client.poll() == (b"request", b"x" * 500, False)


def test_oversized_packets_fail_before_reading_the_body():
    client, stream, _ = connection(b"0\xff\x7f" + b"unread")
    with pytest.raises(MQTTError):
        client.poll()
    assert stream.incoming == b"unread"


@pytest.mark.parametrize(
    "packet",
    [
        b"0\x80\x00",
        b"0\x80\x80\x80\x80",
        b"0\x02\x00\x00",
        b"2\x03\x00\x01a",
        b"6\x03\x00\x01a",
        b"0\x03\x00\x01#",
        b"@\x01\x00",
        b"\xe0\x00",
    ],
)
def test_invalid_frames_fail(packet):
    client, _, _ = connection(packet)
    with pytest.raises(MQTTError):
        client.poll()


def test_slow_fragment_stream_obeys_total_deadline():
    client, _, _ = connection(
        publication(b"request", b"x" * 500), chunk_size=1, timeout_ms=20
    )
    with pytest.raises(MQTTError):
        client.poll()


def test_keepalive_and_timeout():
    client, stream, timer = connection()
    timer.now = 15000
    client.poll()
    assert bytes(stream.outgoing) == b"\xc0\x00"
    stream.incoming.extend(b"\xd0\x00")
    client.poll()
    assert client.ping_started is None
    timer.now += 15000
    client.poll()
    timer.now += 5000
    with pytest.raises(MQTTError):
        client.poll()


def test_disconnect_during_packet_fails():
    client, _, _ = connection(b"0\x03\x00")
    with pytest.raises(MQTTError):
        client.poll()


def test_close_clears_queued_commands():
    client, stream, _ = connection()
    client.pending.append((b"request", b"command", False))
    client.close()
    assert stream.closed
    assert not client.pending


def test_zero_puback_identifier_is_rejected_before_a_valid_ack():
    timer = Timer()
    stream = Stream(b"\x40\x02\x00\x00\x40\x02\x00\x01")
    client = MQTT(stream, poller=Poller(stream, timer), timer=timer)
    with pytest.raises(MQTTError, match="Invalid acknowledgment identifier"):
        client.publish(b"response", b"ok")
    assert stream.incoming == b"\x40\x02\x00\x01"


class FakeSocket(Stream):
    def __init__(self, *address, fail=False):
        super().__init__()
        self.fail = fail
        self.options = {}

    def setsockopt(self, level, option, value):
        self.options[(level, option)] = value

    def connect(self, address):
        if self.fail:
            raise OSError("refused")
        self.address = address


@pytest.fixture
def fake_network(monkeypatch):
    sockets = []
    wrapped = {}

    def make_socket(*address):
        sockets.append(FakeSocket(*address, fail=wrapped.get("fail", False)))
        return sockets[-1]

    socket = SimpleNamespace(
        SOCK_STREAM=1,
        IPPROTO_TCP=6,
        TCP_NODELAY=1,
        getaddrinfo=lambda host, port, *_: wrapped.get(
            "addresses", [(2, 1, 0, "", (host, port))]
        ),
        socket=make_socket,
    )

    class Context:
        def __init__(self, protocol):
            pass

        def load_verify_locations(self, cafile):
            wrapped["cafile"] = cafile

        def wrap_socket(self, stream, server_hostname):
            wrapped["verify_mode"] = self.verify_mode
            wrapped["hostname"] = server_hostname
            return stream

    ssl = SimpleNamespace(PROTOCOL_TLS_CLIENT=0, CERT_REQUIRED=2, SSLContext=Context)
    monkeypatch.setitem(sys.modules, "socket", socket)
    monkeypatch.setitem(sys.modules, "ssl", ssl)
    monkeypatch.setattr(
        "espswarm_agent.mqtt.select",
        SimpleNamespace(poll=lambda: SimpleNamespace(register=lambda *a: None)),
    )
    return sockets, wrapped


def test_open_connection_verifies_tls_certificate_and_hostname(fake_network):
    sockets, wrapped = fake_network
    settings = Settings(
        wifi_ssid="network", mqtt_host="broker", mqtt_port=8883, mqtt_tls=True
    )
    client = open_connection(settings)
    assert sockets[0].address == ("broker", 8883)
    assert sockets[0].options == {(6, 1): 1}
    assert wrapped == {
        "cafile": "broker-ca.pem",
        "verify_mode": 2,
        "hostname": "broker",
    }
    assert client.timeout_ms == 5000


def test_open_connection_closes_socket_on_failure(fake_network):
    sockets, wrapped = fake_network
    wrapped["fail"] = True
    with pytest.raises(OSError):
        open_connection(Settings(wifi_ssid="network", mqtt_host="broker"))
    assert sockets[0].closed


def test_open_connection_rejects_unresolved_host(fake_network):
    _, wrapped = fake_network
    wrapped["addresses"] = []
    with pytest.raises(OSError, match="did not resolve"):
        open_connection(Settings(wifi_ssid="network", mqtt_host="broker"))
