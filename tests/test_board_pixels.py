from types import SimpleNamespace

import pytest
from board_fakes import Machine
from espswarm_agent.pixels import MAX_PIXELS, NeoPixel
from espswarm_agent.targets import ESP32, ESP32S2
from espswarm_agent.validation import CommandError


class FakeStrip:
    """Mimics MicroPython's neopixel.NeoPixel."""

    created = []

    def __init__(self, pin, n):
        self.pin = pin
        self.n = n
        self.pixels = [(0, 0, 0)] * n
        self.written = []
        FakeStrip.created.append(self)

    def __setitem__(self, index, color):
        self.pixels[index] = color

    def write(self):
        self.written.append(list(self.pixels))


@pytest.fixture
def pixels():
    FakeStrip.created = []
    return NeoPixel(Machine(), SimpleNamespace(NeoPixel=FakeStrip), ESP32S2)


def test_write_sets_colors_and_reuses_the_strip(pixels):
    pixels.execute("neopixel.write", {"pin": 18, "colors": [[255, 0, 0]]})
    pixels.execute("neopixel.write", {"pin": 18, "colors": [[0, 0, 32]]})
    (strip,) = FakeStrip.created
    assert strip.pin.number == 18
    assert strip.written == [[(255, 0, 0)], [(0, 0, 32)]]


def test_changing_length_recreates_the_strip(pixels):
    pixels.write(18, [[1, 2, 3]])
    pixels.write(18, [[1, 2, 3], [4, 5, 6]])
    assert [strip.n for strip in FakeStrip.created] == [1, 2]


@pytest.mark.parametrize(
    "args",
    [
        {"pin": 26, "colors": [[0, 0, 0]]},
        {"pin": True, "colors": [[0, 0, 0]]},
        {"pin": 18, "colors": []},
        {"pin": 18, "colors": [[0, 0, 0]] * (MAX_PIXELS + 1)},
        {"pin": 18, "colors": [[0, 0]]},
        {"pin": 18, "colors": [[0, 0, 256]]},
        {"pin": 18, "colors": [[0, 0, True]]},
        {"pin": 18, "colors": [(0, 0, 0)]},
        {"pin": 18, "colors": "red"},
    ],
)
def test_invalid_writes_never_touch_hardware(pixels, args):
    with pytest.raises(CommandError):
        pixels.execute("neopixel.write", args)
    assert not FakeStrip.created


@pytest.mark.parametrize("target, selected", [(ESP32S2, [None]), (ESP32, [])])
def test_bitbanging_is_selected_per_target(target, selected):
    channels = []
    esp32 = SimpleNamespace(RMT=SimpleNamespace(bitstream_channel=channels.append))
    NeoPixel(Machine(), SimpleNamespace(NeoPixel=FakeStrip), target, esp32)
    assert channels == selected
