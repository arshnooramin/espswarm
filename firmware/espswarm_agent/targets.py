"""Per-chip pin rules. Each target lists only pins safe for general use."""


class Target:
    def __init__(self, name, pins, input_only=(), bitbang_pixels=False):
        self.name = name
        self.pins = pins
        # Input-only pins also have no internal pull resistors.
        self.input_only = input_only
        # Drive WS2812 LEDs in software instead of with the RMT peripheral.
        self.bitbang_pixels = bitbang_pixels


# Classic ESP32. Excludes flash (6-11), the console UART (1, 3), and pins PSRAM
# modules may use (16, 17).
ESP32 = Target(
    "esp32",
    (0, 2, 4, 5, 12, 13, 14, 15, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33)
    + (34, 35, 36, 37, 38, 39),
    input_only=(34, 35, 36, 37, 38, 39),
)

# ESP32-S2. Excludes flash and PSRAM (26-32), USB (19, 20), the console UART
# (43, 44), and the strapping pins 45 and 46. GPIO 22-25 do not exist.
# MicroPython 1.29's RMT bitstream sends nothing visible to the DevKitM-1's
# WS2812 on GPIO18; software bit-banging works.
ESP32S2 = Target(
    "esp32s2",
    tuple(range(0, 19)) + (21,) + tuple(range(33, 43)),
    bitbang_pixels=True,
)

# Keyed by the chip name MicroPython reports, without dashes.
TARGETS = {"ESP32": ESP32, "ESP32S2": ESP32S2}


def detect(machine):
    """Pick the target from os.uname().machine, e.g. "... with ESP32-S2"."""
    chip = machine.rpartition(" with ")[2].replace("-", "").upper()
    target = TARGETS.get(chip)
    if target is None:
        raise ValueError("Unsupported chip: " + machine)
    return target
