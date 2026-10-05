"""Blink a board's on-board LED through the broker.

    python examples/onboard_led.py --broker 192.168.1.10 --board bench-1

The ESP32-S2-DevKitM-1 has an RGB LED (WS2812) on GPIO18, the default. For a
plain LED, such as GPIO2 on many classic ESP32 boards, pass --plain --pin 2.
"""

import argparse
import itertools
import time

from espswarm import Client

COLORS = {
    "red": [32, 0, 0],
    "green": [0, 32, 0],
    "blue": [0, 0, 32],
    "white": [24, 24, 24],
}
OFF = [0, 0, 0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--board", required=True, help="board_id to control")
    parser.add_argument("--pin", type=int, default=18)
    parser.add_argument("--plain", action="store_true", help="plain on/off LED")
    parser.add_argument("--blinks", type=int, default=8)
    parser.add_argument("--interval", type=float, default=0.3, help="seconds")
    options = parser.parse_args()

    with Client(options.broker) as client:
        board = client.board(options.board)
        print(f"{board.board_id}: {board.status.target}, {board.capabilities}")
        if options.plain:
            blink_plain(board, options)
        else:
            blink_rgb(board, options)


def blink_rgb(board, options) -> None:
    def show(color):
        board.call("neopixel.write", {"pin": options.pin, "colors": [color]})

    try:
        for _, name in zip(range(options.blinks), itertools.cycle(COLORS)):
            print(name)
            show(COLORS[name])
            time.sleep(options.interval)
            show(OFF)
            time.sleep(options.interval)
    finally:
        show(OFF)


def blink_plain(board, options) -> None:
    board.call("gpio.configure", {"pin": options.pin, "mode": "output"})
    try:
        for _ in range(options.blinks):
            for level in (1, 0):
                board.call("gpio.write", {"pin": options.pin, "level": level})
                time.sleep(options.interval)
    finally:
        board.call("gpio.reset", {"pin": options.pin})


if __name__ == "__main__":
    main()
