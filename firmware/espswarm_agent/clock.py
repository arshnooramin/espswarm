"""MicroPython ticks with a CPython fallback for transport tests."""

import time

if hasattr(time, "ticks_ms"):
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
    sleep_ms = time.sleep_ms
else:

    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(new, old):
        return new - old

    def sleep_ms(milliseconds):
        time.sleep(milliseconds / 1000)
