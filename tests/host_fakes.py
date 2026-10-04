"""Test doubles that connect the host library to the real firmware code."""

from board_fakes import Machine
from virtual_esp_board.gpio import GPIO
from virtual_esp_board.protocol import Protocol as FirmwareProtocol


class LoopbackTransport:
    """A Transport wired straight to the firmware Protocol, with no broker.

    Messages are delivered synchronously. Counters let tests drop requests or
    responses to simulate a lossy network.
    """

    def __init__(self, board_id="workbench", session="boot1", online=True):
        self.board_id = board_id
        self._boot(session)
        self.on_message = None
        self.subscriptions = []
        self.requests = []
        self.drop_requests = 0
        self.drop_responses = 0
        self.closed = False
        self.retained = {}
        if online is not None:
            self.retained[self.status_topic] = self.firmware.status(online)

    def _boot(self, session):
        self.machine = Machine()
        self.gpio = GPIO(self.machine)
        self.firmware = FirmwareProtocol(self.board_id, session, [self.gpio])
        self.request_topic = self.firmware.request_topic.decode()
        self.response_topic = self.firmware.response_topic.decode()
        self.status_topic = self.firmware.status_topic.decode()

    def connect(self, on_message):
        self.on_message = on_message

    def subscribe(self, topic):
        self.subscriptions.append(topic)
        if topic in self.retained:
            self.on_message(topic, self.retained[topic])

    def publish(self, topic, payload):
        if topic != self.request_topic:
            return
        self.requests.append(payload)
        if self.drop_requests:
            self.drop_requests -= 1
            return
        response = self.firmware.handle(payload)
        if self.drop_responses:
            self.drop_responses -= 1
            return
        self.deliver(self.response_topic, response)

    def deliver(self, topic, payload):
        if topic in self.subscriptions:
            self.on_message(topic, payload)

    def set_status(self, payload):
        self.retained[self.status_topic] = payload
        self.deliver(self.status_topic, payload)

    def reboot(self, session):
        """Restart the board: new session, all pin configuration lost."""
        self._boot(session)
        self.set_status(self.firmware.status(True))

    def go_offline(self):
        """Publish what the broker sends as the board's last will."""
        self.set_status(self.firmware.status(False))

    def close(self):
        self.closed = True
