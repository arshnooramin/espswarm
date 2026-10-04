import pytest
from board_fakes import Machine
from virtual_esp_board.gpio import GPIO, EventQueue
from virtual_esp_board.validation import CommandError


@pytest.fixture
def machine():
    return Machine()


@pytest.fixture
def hardware(machine):
    return GPIO(machine)


def test_output_initialization_and_write(machine, hardware):
    hardware.execute("gpio.configure", {"pin": 2, "mode": "output", "initial": 1})
    pin = machine.pins[2]
    assert pin.value() == 1
    hardware.execute("gpio.write", {"pin": 2, "level": 0})
    assert pin.value() == 0


def test_input_pull_and_read(machine, hardware):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input", "pull": "up"})
    pin = machine.pins[5]
    pin.level = 1
    assert pin.pull == machine.Pin.PULL_UP
    assert hardware.execute("gpio.read", {"pin": 5}) == {"level": 1}


@pytest.mark.parametrize(
    "args",
    [
        {"pin": 6, "mode": "output"},
        {"pin": 1, "mode": "input"},
        {"pin": 34, "mode": "output"},
        {"pin": 39, "mode": "input", "pull": "up"},
        {"pin": 2, "mode": "output", "initial": 2},
        {"pin": 2, "mode": "output", "initial": None},
        {"pin": 2, "mode": "output", "pull": ["up"]},
        {"pin": True, "mode": "input"},
        {"pin": 2.0, "mode": "input"},
        {"pin": 2, "mode": "input", "initial": 1},
        {"pin": 2, "mode": "output", "extra": 1},
    ],
)
def test_invalid_configuration_never_touches_hardware(machine, hardware, args):
    with pytest.raises(CommandError):
        hardware.execute("gpio.configure", args)
    assert not machine.constructions


@pytest.mark.parametrize(
    "args",
    [
        {"pin": 2, "level": 1},
        {"pin": 5, "level": 1},
        {"pin": 5, "level": True},
        {"pin": 5, "level": -1},
    ],
)
def test_writes_require_output_and_validate_levels(hardware, args):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    with pytest.raises(CommandError):
        hardware.execute("gpio.write", args)


def test_interrupts_only_enqueue_captured_levels(machine, hardware):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})
    pin = machine.pins[5]
    assert pin.irq_calls[-1][2]
    pin.fire(1)
    pin.fire(0)
    assert hardware.events.pop() == (5, 1, 0)
    assert hardware.events.pop() == (5, 0, 0)
    assert hardware.events.pop() is None


def test_watch_requires_input_and_valid_edge(hardware):
    for args in ({"pin": 5, "edge": "rising"}, {"pin": 5, "edge": "level"}):
        with pytest.raises(CommandError):
            hardware.execute("gpio.watch", args)
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    with pytest.raises(CommandError):
        hardware.execute("gpio.watch", {"pin": 5, "edge": "level"})


def test_watching_twice_is_busy(hardware):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})
    with pytest.raises(CommandError) as caught:
        hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})
    assert caught.value.code == "busy"


def test_unwatch_and_reset_release_resources(machine, hardware):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "rising"})
    with pytest.raises(CommandError):
        hardware.execute("gpio.configure", {"pin": 5, "mode": "output"})
    pin = machine.pins[5]
    hardware.execute("gpio.unwatch", {"pin": 5})
    pin.fire(1)
    assert hardware.events.pop() is None
    hardware.execute("gpio.configure", {"pin": 5, "mode": "output"})
    hardware.execute("gpio.reset", {"pin": 5})
    assert machine.pins[5].mode == machine.Pin.IN
    with pytest.raises(CommandError):
        hardware.execute("gpio.write", {"pin": 5, "level": 1})


def test_reset_stops_watching_a_watched_pin(machine, hardware):
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})
    watched = machine.pins[5]
    hardware.execute("gpio.reset", {"pin": 5})
    assert watched.handler is None
    watched.fire(1)
    assert hardware.events.pop() is None
    hardware.execute("gpio.configure", {"pin": 5, "mode": "input"})
    hardware.execute("gpio.watch", {"pin": 5, "edge": "any"})


def test_unknown_operations_have_no_effect(machine, hardware):
    with pytest.raises(CommandError) as caught:
        hardware.execute("spi.transfer", {})
    assert caught.value.code == "unsupported_operation"
    assert not machine.constructions


def test_queue_overflow_is_bounded_and_reported():
    events = EventQueue(Machine(), capacity=2)
    events.put(2, 1)
    events.put(5, 0)
    for _ in range(20):
        events.put(12, 1)
    assert events.pop() == (2, 1, 20)
    assert events.pop() == (5, 0, 0)
    assert events.pop() is None


def test_queue_wrap_and_clear():
    events = EventQueue(Machine(), capacity=2)
    for _ in range(10):
        events.put(2, 1)
        assert events.pop() == (2, 1, 0)
    events.put(5, 0)
    events.clear()
    assert events.pop() is None


@pytest.mark.parametrize("pin", [True, 2.0, 6, 40, [], None])
@pytest.mark.parametrize("operation", ["configure", "read", "reset", "unwatch"])
def test_direct_gpio_methods_validate_pins(machine, hardware, pin, operation):
    args = (pin, "output") if operation == "configure" else (pin,)
    with pytest.raises(CommandError, match="Pin is unavailable"):
        getattr(hardware, operation)(*args)
    assert not machine.constructions


@pytest.mark.parametrize("level", [True, -1, 2, 1.0, "1"])
def test_direct_write_rejects_invalid_levels_without_changing_output(
    machine, hardware, level
):
    hardware.configure(2, "output", initial=0)
    with pytest.raises(CommandError):
        hardware.write(2, level)
    assert machine.pins[2].value() == 0


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_queue_rejects_invalid_capacity(capacity):
    with pytest.raises(ValueError):
        EventQueue(Machine(), capacity)
