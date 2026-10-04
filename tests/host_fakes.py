"""Test doubles that connect the host library to the real firmware code."""

from board_fakes import Machine
from espswarm_agent.gpio import GPIO
from espswarm_agent.protocol import Protocol as FirmwareProtocol


def topic_matches(pattern, topic):
    """MQTT matching for the single-level `+` wildcard."""
    pattern_parts, topic_parts = pattern.split("/"), topic.split("/")
    return len(pattern_parts) == len(topic_parts) and all(
        expected in ("+", actual)
        for expected, actual in zip(pattern_parts, topic_parts, strict=True)
    )


class FakeBoard:
    """One board running the real firmware Protocol and GPIO, on fake pins.

    Counters let tests drop requests or responses to simulate a lossy network.
    """

    def __init__(self, broker, board_id, session):
        self.broker = broker
        self.board_id = board_id
        self.requests = []
        self.drop_requests = 0
        self.drop_responses = 0
        self._boot(session)

    def _boot(self, session):
        self.machine = Machine()
        self.gpio = GPIO(self.machine)
        self.firmware = FirmwareProtocol(self.board_id, session, [self.gpio])
        self.request_topic = self.firmware.request_topic.decode()
        self.response_topic = self.firmware.response_topic.decode()
        self.status_topic = self.firmware.status_topic.decode()

    def handle(self, payload):
        self.requests.append(payload)
        if self.drop_requests:
            self.drop_requests -= 1
            return
        response = self.firmware.handle(payload)
        if self.drop_responses:
            self.drop_responses -= 1
            return
        self.broker.deliver(self.response_topic, response)

    def go_online(self):
        self.broker.retain(self.status_topic, self.firmware.status(True))

    def go_offline(self):
        """Publish what the broker sends as the board's last will."""
        self.broker.retain(self.status_topic, self.firmware.status(False))

    def reboot(self, session):
        """Restart: new session, all pin configuration lost."""
        self._boot(session)
        self.go_online()


class FakeBroker:
    """A Transport with an in-process broker and boards behind it.

    Messages are delivered synchronously on the publishing thread.
    """

    def __init__(self):
        self.boards = {}
        self.retained = {}
        self.subscriptions = []
        self.on_message = None
        self.connected = False
        self.closed = False

    def add_board(self, board_id="workbench", session="boot1", online=True):
        board = self.boards[board_id] = FakeBoard(self, board_id, session)
        if online is not None:
            (board.go_online if online else board.go_offline)()
        return board

    def connect(self, on_message):
        self.on_message = on_message
        self.connected = True

    def subscribe(self, pattern):
        self.subscriptions.append(pattern)
        for topic, payload in list(self.retained.items()):
            if topic_matches(pattern, topic):
                self.on_message(topic, payload)

    def publish(self, topic, payload):
        for board in self.boards.values():
            if topic == board.request_topic:
                board.handle(payload)

    def retain(self, topic, payload):
        self.retained[topic] = payload
        self.deliver(topic, payload)

    def deliver(self, topic, payload):
        if self.connected and any(
            topic_matches(pattern, topic) for pattern in self.subscriptions
        ):
            self.on_message(topic, payload)

    def close(self):
        self.connected = False
        self.closed = True
