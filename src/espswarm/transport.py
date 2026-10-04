"""MQTT connection used by Board; any object with the Transport methods works."""

import logging
import ssl
import threading
from collections.abc import Callable
from typing import Any, Protocol

import paho.mqtt.client as mqtt

from .errors import BrokerDisconnected, ConnectionFailed

logger = logging.getLogger(__name__)

MessageHandler = Callable[[str, bytes], None]

QOS = 1
DEFAULT_KEEPALIVE_SECONDS = 30
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
RECONNECT_DELAY_SECONDS = (1, 30)


class Transport(Protocol):
    """What Board needs from a connection; handlers run on the transport's thread."""

    def connect(self, on_message: MessageHandler) -> None: ...
    def subscribe(self, topic: str) -> None: ...
    def publish(self, topic: str, payload: bytes) -> None: ...
    def close(self) -> None: ...


class MQTTTransport:
    """A paho-mqtt connection that resubscribes after reconnecting."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        username: str | None = None,
        password: str | None = None,
        tls: bool | ssl.SSLContext = False,
        client_id: str = "",
        keepalive: int = DEFAULT_KEEPALIVE_SECONDS,
        timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        client: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.keepalive = keepalive
        self.timeout = timeout
        self._client = client or mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        if username is not None:
            self._client.username_pw_set(username, password)
        elif password is not None:
            raise ValueError("password requires username")
        if tls:
            context = tls if isinstance(tls, ssl.SSLContext) else None
            self._client.tls_set_context(context or ssl.create_default_context())
        self._client.reconnect_delay_set(*RECONNECT_DELAY_SECONDS)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_subscribe = self._on_subscribe
        self._client.on_message = self._on_message
        self._on_message_handler: MessageHandler | None = None
        self._topics: list[str] = []
        self._state = threading.Condition()
        self._connect_result: Any = None
        # SUBACK reason codes, keyed by message ID, for subscribe() calls only.
        self._awaited_subscriptions: set[int] = set()
        self._subscribe_results: dict[int, list[Any]] = {}

    def connect(self, on_message: MessageHandler) -> None:
        self._on_message_handler = on_message
        try:
            self._client.connect(self.host, self.port, self.keepalive)
        except OSError as error:
            raise ConnectionFailed(
                f"Could not reach MQTT broker {self.host}:{self.port}"
            ) from error
        self._client.loop_start()
        with self._state:
            if not self._state.wait_for(
                lambda: self._connect_result is not None, self.timeout
            ):
                self.close()
                raise ConnectionFailed("MQTT broker did not answer in time")
            result = self._connect_result
        if result.is_failure:
            self.close()
            raise ConnectionFailed(f"MQTT broker refused the connection: {result}")

    def subscribe(self, topic: str) -> None:
        """Subscribe and wait for the broker to confirm."""
        with self._state:
            status, mid = self._client.subscribe(topic, QOS)
            if status != mqtt.MQTT_ERR_SUCCESS:
                raise BrokerDisconnected("Not connected to the MQTT broker")
            self._awaited_subscriptions.add(mid)
            try:
                confirmed = self._state.wait_for(
                    lambda: mid in self._subscribe_results, self.timeout
                )
            finally:
                self._awaited_subscriptions.discard(mid)
            if not confirmed:
                raise ConnectionFailed("MQTT broker did not confirm subscription")
            codes = self._subscribe_results.pop(mid)
            if any(code.is_failure for code in codes):
                raise ConnectionFailed(f"MQTT broker refused subscription to {topic}")
            self._topics.append(topic)

    def publish(self, topic: str, payload: bytes) -> None:
        if not self._client.is_connected():
            raise BrokerDisconnected("Not connected to the MQTT broker")
        info = self._client.publish(topic, payload, QOS)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise BrokerDisconnected("Not connected to the MQTT broker")

    def close(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if not reason_code.is_failure:
            # Sessions are clean, so the broker forgot earlier subscriptions.
            for topic in self._topics:
                client.subscribe(topic, QOS)
        with self._state:
            self._connect_result = reason_code
            self._state.notify_all()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.warning("MQTT connection lost (%s); reconnecting", reason_code)

    def _on_subscribe(self, client, userdata, mid, reason_codes, properties) -> None:
        with self._state:
            if mid in self._awaited_subscriptions:
                self._subscribe_results[mid] = list(reason_codes)
                self._state.notify_all()

    def _on_message(self, client, userdata, message) -> None:
        handler = self._on_message_handler
        if handler is not None:
            handler(message.topic, message.payload)
