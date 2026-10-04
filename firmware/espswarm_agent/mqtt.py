"""Bounded MQTT 3.1.1 transport for the board's QoS 0/1 traffic.

There is one outstanding outbound operation at a time. Incoming publications
received while waiting for an acknowledgment enter a bounded queue; application
callbacks never execute inside the transport.
"""

import select

from . import clock
from .limits import MAX_PAYLOAD

PACKET_OVERHEAD_BUDGET = 256
MAX_PACKET = MAX_PAYLOAD + PACKET_OVERHEAD_BUDGET
MAX_TOPIC = 128
MAX_PENDING = 4
MAX_UINT16 = 65535
DEFAULT_TIMEOUT_MS = 5000
DEFAULT_KEEPALIVE_SECONDS = 30
DEFAULT_POLL_INTERVAL_MS = 100
MILLISECONDS_PER_SECOND = 1000
KEEPALIVE_PING_FRACTION = 2
MAX_LENGTH_BYTES = 4

CONNECT = 0x10
CONNACK = 0x20
PUBLISH = 0x30
PUBACK = 0x40
SUBSCRIBE = 0x82  # MQTT requires these low header bits for SUBSCRIBE.
SUBACK = 0x90
PINGREQ = 0xC0
PINGRESP = 0xD0
CONNECT_CLEAN_SESSION = 0x02
CONNECT_WILL = 0x04
CONNECT_WILL_QOS1 = 0x08
CONNECT_WILL_RETAIN = 0x20
CONNECT_USERNAME = 0x80
CONNECT_PASSWORD = 0x40
PROTOCOL_NAME_AND_LEVEL = b"\x00\x04MQTT\x04"
ACCEPTED = b"\x00\x00"
SUBACK_GRANTED_QOS = (0, 1)


class MQTTError(OSError):
    pass


def u16(value):
    return bytes((value >> 8, value & 255))


def read_u16(data, offset=0):
    return data[offset] << 8 | data[offset + 1]


def field(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    if not isinstance(value, bytes) or len(value) > MAX_UINT16:
        raise ValueError("Invalid MQTT field")
    return u16(len(value)) + value


def validate_topic(topic):
    if (
        not isinstance(topic, bytes)
        or not 1 <= len(topic) <= MAX_TOPIC
        or any(c in topic for c in (b"\x00", b"#", b"+"))
    ):
        raise ValueError("Invalid MQTT topic")


def topic_field(topic):
    validate_topic(topic)
    return field(topic)


def packet(header, body):
    if len(body) > MAX_PACKET:
        raise ValueError("MQTT packet exceeds limit")
    remaining = len(body)
    prefix = bytearray((header,))
    while True:
        digit = remaining & 127
        remaining >>= 7
        prefix.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes(prefix) + body


PINGREQ_PACKET = packet(PINGREQ, b"")


class MQTT:
    def __init__(
        self,
        stream,
        timeout_ms=DEFAULT_TIMEOUT_MS,
        keepalive=DEFAULT_KEEPALIVE_SECONDS,
        poller=None,
        timer=clock,
    ):
        self.stream = stream
        self.timeout_ms = timeout_ms
        self.keepalive = keepalive
        self.timer = timer
        self.read = stream.read if hasattr(stream, "read") else stream.recv
        self.write = stream.write if hasattr(stream, "write") else stream.send
        self.poller = poller or select.poll()
        self.poller.register(stream, getattr(select, "POLLIN", 1))
        self.identifier = 0
        self.pending = []
        self.last_activity = timer.ticks_ms()
        self.ping_started = None

    def close(self):
        self.pending.clear()
        self.stream.close()

    def connect(
        self, client_id, will_topic, will_payload, username=None, password=None
    ):
        flags = (
            CONNECT_CLEAN_SESSION
            | CONNECT_WILL
            | CONNECT_WILL_QOS1
            | CONNECT_WILL_RETAIN
        )
        payload = field(client_id) + topic_field(will_topic) + field(will_payload)
        if username is not None:
            flags |= CONNECT_USERNAME
            payload += field(username)
        if password is not None:
            if username is None:
                raise ValueError("Password requires username")
            flags |= CONNECT_PASSWORD
            payload += field(password)
        body = PROTOCOL_NAME_AND_LEVEL + bytes((flags,)) + u16(self.keepalive) + payload
        started = self.timer.ticks_ms()
        self._send(packet(CONNECT, body), started)
        self._wait(CONNACK, None, started)

    def subscribe(self, topic):
        identifier = self._next_id()
        body = u16(identifier) + topic_field(topic) + b"\x01"
        started = self.timer.ticks_ms()
        self._send(packet(SUBSCRIBE, body), started)
        self._wait(SUBACK, identifier, started)

    def publish(self, topic, payload, qos=1, retain=False):
        if (
            qos not in (0, 1)
            or not isinstance(payload, bytes)
            or len(payload) > MAX_PAYLOAD
        ):
            raise ValueError("Invalid publication")
        body = topic_field(topic)
        identifier = self._next_id() if qos else None
        if qos:
            body += u16(identifier)
        body += payload
        started = self.timer.ticks_ms()
        self._send(packet(PUBLISH | (qos << 1) | int(retain), body), started)
        if qos:
            self._wait(PUBACK, identifier, started)

    def poll(self, timeout_ms=DEFAULT_POLL_INTERVAL_MS):
        now = self.timer.ticks_ms()
        if (
            self.ping_started is not None
            and self.timer.ticks_diff(now, self.ping_started) >= self.timeout_ms
        ):
            raise MQTTError("Broker did not answer keepalive")
        if self.pending:
            return self.pending.pop(0)
        if (
            self.ping_started is None
            and self.timer.ticks_diff(now, self.last_activity)
            >= self.keepalive * MILLISECONDS_PER_SECOND // KEEPALIVE_PING_FRACTION
        ):
            self.ping_started = now
            self._send(PINGREQ_PACKET, now)
        if not self.poller.poll(timeout_ms):
            return None
        started = self.timer.ticks_ms()
        header, body = self._receive(started)
        if header & 0xF0 == PUBLISH:
            return self._publication(header, body, started)
        self._control(header, body)
        return None

    def _next_id(self):
        self.identifier = self.identifier % MAX_UINT16 + 1
        return self.identifier

    def _arm_timeout(self, started):
        """Apply the time left in this operation's deadline to the socket."""
        remaining = self.timeout_ms - self.timer.ticks_diff(
            self.timer.ticks_ms(), started
        )
        if remaining <= 0:
            raise MQTTError("MQTT operation timed out")
        if hasattr(self.stream, "settimeout"):
            self.stream.settimeout(remaining / MILLISECONDS_PER_SECOND)

    def _send(self, data, started):
        view = memoryview(data)
        position = 0
        while position < len(data):
            self._arm_timeout(started)
            written = self.write(view[position:])
            if not written:
                raise MQTTError("MQTT connection closed during write")
            position += written
        self.last_activity = self.timer.ticks_ms()

    def _exact(self, length, started):
        result = bytearray(length)
        position = 0
        while position < length:
            self._arm_timeout(started)
            chunk = self.read(length - position)
            if not chunk:
                raise MQTTError("MQTT connection closed during read")
            result[position : position + len(chunk)] = chunk
            position += len(chunk)
        return result

    def _receive(self, started):
        header = self._exact(1, started)[0]
        remaining = 0
        for index in range(MAX_LENGTH_BYTES):
            digit = self._exact(1, started)[0]
            remaining |= (digit & 127) << (index * 7)
            if remaining > MAX_PACKET:
                raise MQTTError("Incoming MQTT packet exceeds limit")
            if not digit & 128:
                if index and digit == 0:
                    raise MQTTError("Invalid remaining-length encoding")
                break
        else:
            raise MQTTError("Invalid remaining-length encoding")
        return header, self._exact(remaining, started)

    def _publication(self, header, body, started):
        qos = (header >> 1) & 3
        if qos not in (0, 1) or (qos == 0 and header & 8) or len(body) < 2:
            raise MQTTError("Invalid PUBLISH")
        offset = read_u16(body) + 2
        if offset > len(body):
            raise MQTTError("Invalid PUBLISH topic")
        topic = bytes(body[2:offset])
        try:
            validate_topic(topic)
        except ValueError as error:
            raise MQTTError("Invalid PUBLISH topic") from error
        if qos:
            identifier = bytes(body[offset : offset + 2])
            if len(identifier) != 2 or identifier == b"\x00\x00":
                raise MQTTError("Invalid PUBLISH identifier")
            self._send(packet(PUBACK, identifier), started)
            offset += 2
        payload = bytes(body[offset:])
        if len(payload) > MAX_PAYLOAD:
            raise MQTTError("Incoming payload exceeds limit")
        return topic, payload, bool(header & 1)

    def _control(self, header, body):
        if header == PINGRESP and not body:
            self.ping_started = None
            return
        # A late PUBACK is harmless; it never acknowledges a different request.
        if header == PUBACK and len(body) == 2 and read_u16(body):
            return
        raise MQTTError("Unexpected MQTT control packet")

    def _wait(self, expected, identifier, started):
        while True:
            header, body = self._receive(started)
            if header & 0xF0 == PUBLISH:
                if expected == CONNACK:
                    raise MQTTError("Publication arrived before CONNACK")
                if len(self.pending) >= MAX_PENDING:
                    raise MQTTError("Incoming command queue is full")
                self.pending.append(self._publication(header, body, started))
            elif header == expected:
                if self._acknowledged(expected, identifier, body):
                    return
            else:
                self._control(header, body)

    @staticmethod
    def _acknowledged(expected, identifier, body):
        if expected == CONNACK:
            if body != ACCEPTED:
                raise MQTTError("Broker refused connection or session")
            return True
        if len(body) != (3 if expected == SUBACK else 2):
            raise MQTTError("Invalid acknowledgment")
        received = read_u16(body)
        if received == 0:
            raise MQTTError("Invalid acknowledgment identifier")
        if received != identifier:
            if expected == PUBACK:
                return False
            raise MQTTError("Acknowledgment identifier mismatch")
        if expected == SUBACK and body[2] not in SUBACK_GRANTED_QOS:
            raise MQTTError("Broker refused subscription")
        return True


def open_connection(settings):
    import socket

    addresses = socket.getaddrinfo(
        settings.mqtt_host, settings.mqtt_port, 0, socket.SOCK_STREAM
    )
    if not addresses:
        raise OSError("MQTT host did not resolve")
    family, kind, protocol = addresses[0][:3]
    stream = socket.socket(family, kind, protocol)
    try:
        stream.settimeout(settings.socket_timeout)
        # Send responses at once rather than after the host's delayed ACK.
        if hasattr(socket, "TCP_NODELAY"):
            stream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        stream.connect(addresses[0][-1])
        if settings.mqtt_tls:
            import ssl

            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.verify_mode = ssl.CERT_REQUIRED
            context.load_verify_locations(cafile=settings.mqtt_ca_file)
            stream = context.wrap_socket(stream, server_hostname=settings.mqtt_host)
        return MQTT(
            stream,
            timeout_ms=settings.socket_timeout * MILLISECONDS_PER_SECOND,
            keepalive=settings.keepalive,
        )
    except BaseException:
        stream.close()
        raise
