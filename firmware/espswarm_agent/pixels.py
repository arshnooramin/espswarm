"""Addressable RGB LEDs (WS2812, "NeoPixel"), such as on-board status LEDs."""

from .peripheral import Peripheral
from .validation import CommandError, is_integer

# Bounds the request size and the driver's buffer.
MAX_PIXELS = 64


class NeoPixel(Peripheral):
    capabilities = ("neopixel",)
    operations = {"neopixel.write": ("write", ("pin", "colors"), ())}

    def __init__(self, machine, driver, target, esp32=None):
        self.machine = machine
        # MicroPython's built-in `neopixel` module, injected for testing.
        self.driver = driver
        self.target = target
        self._strips = {}
        if target.bitbang_pixels and esp32 is not None:
            # None selects software bit-banging for machine.bitstream.
            esp32.RMT.bitstream_channel(None)

    def write(self, pin, colors):
        """Set `colors`, a list of [r, g, b] (0-255), on the strip at `pin`."""
        if type(pin) is not int or pin not in self.target.pins:
            raise CommandError("invalid_args", "Pin is unavailable or reserved")
        if not isinstance(colors, list) or not 1 <= len(colors) <= MAX_PIXELS:
            raise CommandError(
                "invalid_args", "colors must list 1-" + str(MAX_PIXELS) + " pixels"
            )
        for color in colors:
            if (
                not isinstance(color, list)
                or len(color) != 3
                or not all(is_integer(value, 0, 255) for value in color)
            ):
                raise CommandError("invalid_args", "Each color is [r, g, b], 0-255")
        strip = self._strips.get(pin)
        if strip is None or strip.n != len(colors):
            strip = self.driver.NeoPixel(self.machine.Pin(pin), len(colors))
            self._strips[pin] = strip
        for index, color in enumerate(colors):
            strip[index] = (color[0], color[1], color[2])
        strip.write()
        return {}
