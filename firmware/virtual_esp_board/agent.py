"""Single-threaded board service; GPIO IRQs never perform networking."""

import gc
import logging

from . import clock
from .mqtt import open_connection
from .protocol import Protocol

logger = logging.getLogger(__name__)

MILLISECONDS_PER_SECOND = 1000
WIFI_POLL_INTERVAL_MS = 100
INITIAL_BACKOFF_MS = 1000
MAX_BACKOFF_MS = 30000
STABLE_CONNECTION_MS = 30000
BACKOFF_MULTIPLIER = 2
EVENTS_PER_STEP = 16
SESSION_RANDOM_BYTES = 8


class Agent:
    """Moves messages between one MQTT connection and the protocol."""

    def __init__(self, protocol, events, settings):
        self.protocol = protocol
        self.events = events
        self.settings = settings
        self.client = None

    def start(self, client):
        self.client = client
        client.connect(
            self.protocol.client_id,
            self.protocol.status_topic,
            self.protocol.status(False),
            username=self.settings.mqtt_username,
            password=self.settings.mqtt_password,
        )
        client.subscribe(self.protocol.request_topic)
        self.events.clear()
        client.publish(
            self.protocol.status_topic, self.protocol.status(True), retain=True
        )
        logger.info("Board online: %s", self.protocol.board_id)

    def step(self):
        message = self.client.poll()
        if message is not None:
            self._handle_message(message)
        self._publish_events()

    def _handle_message(self, message):
        topic, payload, retained = message
        if topic == self.protocol.request_topic:
            response = self.protocol.handle(payload, retained=retained)
            self.client.publish(self.protocol.response_topic, response)

    def _publish_events(self):
        # A finite drain prevents IRQ storms from starving command handling.
        for _ in range(EVENTS_PER_STEP):
            event = self.events.pop()
            if event is None:
                break
            self.client.publish(
                self.protocol.event_topic, self.protocol.event(*event), qos=0
            )

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except OSError:
                # A failed transport close must not prevent reconnection.
                pass
            finally:
                self.client = None
        self.events.clear()


class ConnectionSupervisor:
    """Keeps Wi-Fi and MQTT connected, retrying with exponential backoff."""

    def __init__(self, settings, wlan, agent, connector=open_connection, timer=clock):
        self.settings = settings
        self.wlan = wlan
        self.agent = agent
        self.connector = connector
        self.timer = timer
        self.backoff = INITIAL_BACKOFF_MS

    def run(self):
        try:
            while True:
                try:
                    self._run_session()
                except (OSError, ValueError, MemoryError):
                    # Do not log credentials or broker exception text.
                    logger.warning("Connection lost; retrying in %d ms", self.backoff)
                finally:
                    self.agent.close()
                self._wait_before_retry()
        finally:
            self.agent.close()

    def connect_wifi(self):
        self.wlan.active(True)
        if self.wlan.isconnected():
            return
        self.wlan.connect(self.settings.wifi_ssid, self.settings.wifi_password)
        started = self.timer.ticks_ms()
        timeout_ms = self.settings.wifi_timeout * MILLISECONDS_PER_SECOND
        while not self.wlan.isconnected():
            if self._elapsed_since(started) >= timeout_ms:
                self.wlan.disconnect()
                raise OSError("Wi-Fi connection timed out")
            self.timer.sleep_ms(WIFI_POLL_INTERVAL_MS)

    def _run_session(self):
        self.connect_wifi()
        if self.settings.mqtt_tls:
            self._sync_clock()
        self.agent.start(self.connector(self.settings))
        connected_at = self.timer.ticks_ms()
        stable = False
        while self.wlan.isconnected():
            self.agent.step()
            if not stable and self._elapsed_since(connected_at) >= STABLE_CONNECTION_MS:
                self.backoff = INITIAL_BACKOFF_MS
                stable = True
        raise OSError("Wi-Fi disconnected")

    def _elapsed_since(self, started):
        return self.timer.ticks_diff(self.timer.ticks_ms(), started)

    def _sync_clock(self):
        import ntptime

        ntptime.host = self.settings.ntp_host
        ntptime.timeout = self.settings.socket_timeout
        ntptime.settime()

    def _wait_before_retry(self):
        gc.collect()
        self.timer.sleep_ms(self.backoff)
        self.backoff = min(self.backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_MS)


def run(settings):
    import binascii
    import os

    import machine
    import network

    from .gpio import GPIO

    wlan = network.WLAN(network.WLAN.IF_STA)
    # The interface must be active before its MAC address can be read.
    wlan.active(True)
    identity = settings.board_id or binascii.hexlify(wlan.config("mac")).decode()
    session = binascii.hexlify(os.urandom(SESSION_RANDOM_BYTES)).decode()
    gpio = GPIO(machine)
    agent = Agent(Protocol(identity, session, [gpio]), gpio.events, settings)
    logger.info("Starting board: %s", identity)
    ConnectionSupervisor(settings, wlan, agent).run()
