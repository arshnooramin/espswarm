"""Hardware doubles for exercising board behavior without an ESP32."""

import json


class Pin:
    IN = 0
    OUT = 1
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_RISING = 1
    IRQ_FALLING = 2

    def __init__(self, number, mode=None, pull=None, value=0):
        self.number = number
        self.mode = mode
        self.pull = pull
        self.level = value
        self.handler = None
        self.irq_calls = []

    def value(self, level=None):
        if level is not None:
            self.level = level
        return self.level

    def irq(self, handler=None, trigger=0, wake=None):
        # Same keywords as the ESP32 port's Pin.irq; it has no `hard` mode.
        self.irq_calls.append((handler, trigger))
        self.handler = handler

    def fire(self, level):
        self.level = level
        if self.handler:
            self.handler(self)


class Machine:
    """Stands in for the `machine` module and records every Pin it constructs."""

    def __init__(self):
        self.pins = {}
        self.constructions = []
        machine = self

        class RecordedPin(Pin):
            def __init__(self, number, *args, **kwargs):
                super().__init__(number, *args, **kwargs)
                machine.pins[number] = self
                machine.constructions.append(number)

        self.Pin = RecordedPin

    def disable_irq(self):
        return 1

    def enable_irq(self, state):
        pass


class Timer:
    def __init__(self):
        self.now = 0

    def ticks_ms(self):
        return self.now

    def ticks_diff(self, new, old):
        return new - old

    def sleep_ms(self, delay):
        self.now += delay


class Stream:
    def __init__(self, incoming=b"", chunk_size=4096, timer=None):
        self.incoming = bytearray(incoming)
        self.outgoing = bytearray()
        self.chunk_size = chunk_size
        self.closed = False
        self.timer = timer

    def read(self, length):
        if self.timer:
            self.timer.now += 1
        length = min(length, self.chunk_size, len(self.incoming))
        data = bytes(self.incoming[:length])
        del self.incoming[:length]
        return data

    def write(self, data):
        length = min(len(data), self.chunk_size)
        self.outgoing.extend(data[:length])
        return length

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


class Poller:
    def __init__(self, stream, timer):
        self.stream = stream
        self.timer = timer

    def register(self, stream, flags):
        pass

    def poll(self, timeout):
        if self.stream.incoming:
            return [(1, 1)]
        self.timer.now += timeout
        return []


def request(identifier="req1", **overrides):
    """Encode a valid gpio.read request for session boot1, with overrides."""
    value = {
        "v": 1,
        "id": identifier,
        "session": "boot1",
        "op": "gpio.read",
        "args": {"pin": 5},
    }
    value.update(overrides)
    return json.dumps(value).encode()


def publication(topic, payload, *, qos=1, retained=False, identifier=7):
    body = len(topic).to_bytes(2, "big") + topic
    if qos:
        body += identifier.to_bytes(2, "big")
    body += payload
    length = len(body)
    remaining = (
        bytes([length]) if length < 128 else bytes([length % 128 + 128, length // 128])
    )
    return bytes([0x30 | (qos << 1) | int(retained)]) + remaining + body
