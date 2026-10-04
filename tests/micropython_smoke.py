"""Execute with MicroPython from the repository root; no CPython test helpers."""

import json
import sys

sys.path.insert(0, "firmware")

from espswarm_agent.agent import Agent
from espswarm_agent.gpio import GPIO
from espswarm_agent.mqtt import MQTT
from espswarm_agent.protocol import Protocol
from espswarm_agent.settings import Settings


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2
    created = {}

    def __init__(self, number, mode, pull=None, value=0):
        self.level = value
        self.handler = None
        Pin.created[number] = self

    def value(self, value=None):
        if value is not None:
            self.level = value
        return self.level

    def irq(self, handler=None, trigger=0, hard=False):
        self.handler = handler


class Machine:
    Pin = Pin

    @staticmethod
    def disable_irq():
        return 0

    @staticmethod
    def enable_irq(state):
        pass


class Stream:
    def __init__(self, incoming):
        self.incoming = incoming
        self.sent = b""

    def read(self, length):
        part = self.incoming[:length]
        self.incoming = self.incoming[length:]
        return part

    def write(self, value):
        self.sent += bytes(value)
        return len(value)


class Poller:
    def register(self, stream, flags):
        pass


def check_protocol(hardware, protocol):
    payload = (
        b'{"v":1,"id":"r1","session":"boot1","op":"gpio.configure",'
        b'"args":{"pin":2,"mode":"output","initial":1}}'
    )
    reply = protocol.handle(payload)
    assert json.loads(reply)["ok"]
    configured = Pin.created[2]
    assert configured.value() == 1
    assert protocol.handle(payload) == reply
    assert Pin.created[2] is configured
    retained = json.loads(protocol.handle(payload, retained=True))
    assert retained["error"]["code"] == "retained_request"
    for malformed in (b'{"v":1,"v":2}', b'{"pin":NaN}', b"[" * 10 + b"0" + b"]" * 10):
        assert json.loads(protocol.handle(malformed))["ok"] is False


def check_irq_queue(hardware):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})
    handler = Pin.created[5].handler
    try:
        import micropython
    except ImportError:
        micropython = None
    # The hard IRQ path must not allocate.
    if micropython:
        micropython.heap_lock()
    try:
        for _ in range(18):
            handler(None)
    finally:
        if micropython:
            micropython.heap_unlock()
    assert hardware.events.pop() == (5, 0, 2)


def check_agent_and_transport(hardware, protocol):
    connack, suback = b"\x20\x02\x00\x00", b"\x90\x03\x00\x01\x01"
    puback = b"\x40\x02\x00\x02"
    stream = Stream(connack + suback + puback)
    agent = Agent(protocol, hardware.events, Settings(wifi_ssid="t", mqtt_host="b"))
    agent.start(MQTT(stream, poller=Poller()))
    assert stream.sent.startswith(b"\x10")
    assert b"espswarm/v1/workbench/status" in stream.sent

    stream = Stream(b"\x40\x02\x00\x01")
    MQTT(stream, poller=Poller()).publish(b"response", b"ok")
    assert stream.sent == b"\x32\x0e\x00\x08response\x00\x01ok"


def smoke():
    hardware = GPIO(Machine)
    protocol = Protocol("workbench", "boot1", [hardware])
    check_protocol(hardware, protocol)
    check_irq_queue(hardware)
    check_agent_and_transport(hardware, protocol)
    print("MicroPython board smoke checks passed")


if __name__ == "__main__":
    smoke()
