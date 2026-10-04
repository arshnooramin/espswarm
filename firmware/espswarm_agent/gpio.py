"""Classic ESP32 GPIO and a preallocated queue for hard interrupts."""

from .peripheral import Peripheral
from .validation import CommandError, is_integer

EVENT_QUEUE_CAPACITY = 16
MAX_DROPPED_EVENTS = 65535
INPUT_ONLY_PIN_START = 34
MODES = ("input", "output")

# Exclude flash, console UART, and pins potentially used by PSRAM.
PINS = (
    0,
    2,
    4,
    5,
    12,
    13,
    14,
    15,
    18,
    19,
    21,
    22,
    23,
    25,
    26,
    27,
    32,
    33,
    34,
    35,
    36,
    37,
    38,
    39,
)

# Distinguishes an omitted output level from an explicit JSON null.
_UNSET = object()


class EventQueue:
    def __init__(self, machine, capacity=EVENT_QUEUE_CAPACITY):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("Queue capacity must be a positive integer")
        self.pins = bytearray(capacity)
        self.levels = bytearray(capacity)
        self.capacity = capacity
        self.head = 0
        self.tail = 0
        self.count = 0
        self.dropped = 0
        self.disable_irq = machine.disable_irq
        self.enable_irq = machine.enable_irq

    def put(self, pin, level):
        # Only small integers and existing buffers are changed in the IRQ.
        if self.count == self.capacity:
            if self.dropped < MAX_DROPPED_EVENTS:
                self.dropped += 1
            return
        self.pins[self.head] = pin
        self.levels[self.head] = level
        self.head = (self.head + 1) % self.capacity
        self.count += 1

    def pop(self):
        state = self.disable_irq()
        try:
            if not self.count:
                return None
            pin = self.pins[self.tail]
            level = self.levels[self.tail]
            dropped = self.dropped
            self.dropped = 0
            self.tail = (self.tail + 1) % self.capacity
            self.count -= 1
        finally:
            self.enable_irq(state)
        return pin, level, dropped

    def clear(self):
        state = self.disable_irq()
        try:
            self.head = self.tail = self.count = self.dropped = 0
        finally:
            self.enable_irq(state)


class ConfiguredPin:
    def __init__(self, pin, mode):
        self.pin = pin
        self.mode = mode
        self.handler = None


class GPIO(Peripheral):
    capabilities = ("gpio", "gpio.events")
    operations = {
        "gpio.configure": ("configure", ("pin", "mode"), ("pull", "initial")),
        "gpio.read": ("read", ("pin",), ()),
        "gpio.write": ("write", ("pin", "level"), ()),
        "gpio.reset": ("reset", ("pin",), ()),
        "gpio.watch": ("watch", ("pin", "edge"), ()),
        "gpio.unwatch": ("unwatch", ("pin",), ()),
    }

    def __init__(self, machine):
        self.machine = machine
        self.events = EventQueue(machine)
        self._configured = {}
        pin_class = machine.Pin
        self._pulls = {
            "none": None,
            "up": pin_class.PULL_UP,
            "down": pin_class.PULL_DOWN,
        }
        self._triggers = {
            "rising": pin_class.IRQ_RISING,
            "falling": pin_class.IRQ_FALLING,
            "any": pin_class.IRQ_RISING | pin_class.IRQ_FALLING,
        }

    @staticmethod
    def _validate_pin(pin):
        if type(pin) is not int or pin not in PINS:
            raise CommandError("invalid_args", "Pin is unavailable or reserved")

    @staticmethod
    def _validate_level(level):
        if not is_integer(level, 0, 1):
            raise CommandError("invalid_args", "Level must be 0 or 1")

    def _require_mode(self, pin, mode):
        self._validate_pin(pin)
        configured = self._configured.get(pin)
        if configured is None or configured.mode != mode:
            raise CommandError("invalid_state", "Configure pin as " + mode + " first")
        return configured

    def configure(self, pin, mode, pull="none", initial=_UNSET):
        self._validate_pin(pin)
        if initial is not _UNSET:
            self._validate_level(initial)
        if mode not in MODES or not isinstance(pull, str) or pull not in self._pulls:
            raise CommandError("invalid_args", "Invalid mode or pull")
        if pin >= INPUT_ONLY_PIN_START and (mode == "output" or pull != "none"):
            raise CommandError(
                "invalid_args", "Pin is input-only without internal pulls"
            )
        previous = self._configured.get(pin)
        if previous is not None and previous.handler is not None:
            raise CommandError("busy", "Unwatch pin before configuring it")
        if mode == "input" and initial is not _UNSET:
            raise CommandError("invalid_args", "initial requires output mode")
        pin_class = self.machine.Pin
        if mode == "output":
            hardware = pin_class(
                pin,
                pin_class.OUT,
                pull=self._pulls[pull],
                value=0 if initial is _UNSET else initial,
            )
        else:
            hardware = pin_class(pin, pin_class.IN, pull=self._pulls[pull])
        self._configured[pin] = ConfiguredPin(hardware, mode)
        return {}

    def read(self, pin):
        return {"level": self._require_mode(pin, "input").pin.value()}

    def write(self, pin, level):
        self._validate_level(level)
        self._require_mode(pin, "output").pin.value(level)
        return {}

    def reset(self, pin):
        self.unwatch(pin)
        self.machine.Pin(pin, self.machine.Pin.IN, pull=None)
        self._configured.pop(pin, None)
        return {}

    def watch(self, pin, edge):
        configured = self._require_mode(pin, "input")
        if not isinstance(edge, str) or edge not in self._triggers:
            raise CommandError("invalid_args", "Invalid edge")
        if configured.handler is not None:
            raise CommandError("busy", "Pin is already watched")
        # Bind locals so the hard IRQ handler does no attribute lookups.
        enqueue = self.events.put
        read = configured.pin.value

        def handler(_):
            enqueue(pin, read())

        configured.pin.irq(handler=handler, trigger=self._triggers[edge], hard=True)
        configured.handler = handler
        return {}

    def unwatch(self, pin):
        self._validate_pin(pin)
        configured = self._configured.get(pin)
        if configured is not None and configured.handler is not None:
            configured.pin.irq(handler=None)
            configured.handler = None
        return {}
