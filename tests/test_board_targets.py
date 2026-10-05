import json

import pytest
from board_fakes import Machine
from espswarm_agent.gpio import GPIO
from espswarm_agent.protocol import Protocol
from espswarm_agent.targets import ESP32, ESP32S2, detect
from espswarm_agent.validation import CommandError


@pytest.mark.parametrize(
    "machine, target",
    [
        ("Generic ESP32 module with ESP32", ESP32),
        ("Generic ESP32S2 module with ESP32-S2", ESP32S2),
        ("ESP32S2 module with ESP32S2", ESP32S2),
    ],
)
def test_detect_reads_the_chip_name(machine, target):
    assert detect(machine) is target


@pytest.mark.parametrize(
    "machine",
    ["Generic ESP32S3 module with ESP32S3", "Raspberry Pi Pico W with RP2040"],
)
def test_unsupported_chips_are_rejected(machine):
    with pytest.raises(ValueError, match="Unsupported chip"):
        detect(machine)


@pytest.mark.parametrize(
    "pin, reason",
    [
        (22, "does not exist"),
        (25, "does not exist"),
        (19, "USB"),
        (20, "USB"),
        (26, "flash/PSRAM"),
        (32, "flash/PSRAM"),
        (43, "console UART"),
        (45, "strapping"),
        (46, "strapping"),
    ],
)
def test_esp32s2_reserved_pins_never_touch_hardware(pin, reason):
    machine = Machine()
    with pytest.raises(CommandError, match="unavailable or reserved"):
        GPIO(machine, ESP32S2).configure(pin, "input")
    assert not machine.constructions


@pytest.mark.parametrize("pin", [0, 4, 5, 18, 21, 34, 39, 42])
def test_esp32s2_general_pins_support_output_and_pulls(pin):
    machine = Machine()
    GPIO(machine, ESP32S2).configure(pin, "output", pull="up")
    assert machine.pins[pin].mode == machine.Pin.OUT


def test_esp32_high_pins_stay_input_only():
    with pytest.raises(CommandError, match="input-only"):
        GPIO(Machine(), ESP32).configure(34, "output")


def test_info_reports_the_target():
    protocol = Protocol("board", "boot", [], target=ESP32S2.name)
    assert json.loads(protocol.status(True))["target"] == "esp32s2"
