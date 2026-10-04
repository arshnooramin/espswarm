# ESP32 board agent

MicroPython companion for Virtual ESP. Supports a classic Espressif ESP32 DevKit
with GPIO commands and interrupt events. Tested runtime compatibility:
MicroPython 1.29.0. Physical board validation is still required.

## Install MicroPython

Install the board tools from the repository root:

```sh
python -m pip install '.[board]'
```

Download the stable 1.29.0 `.bin` for your board from the
[official ESP32 firmware page](https://micropython.org/download/ESP32_GENERIC/).
Use the generic build for a typical WROOM DevKit and the SPIRAM variant for WROVER.
The following initial installation erases existing flash contents:

```sh
esptool --chip esp32 --port PORT erase-flash
esptool --chip esp32 --port PORT write-flash 0x1000 PATH_TO_FIRMWARE.bin
```

Replace `PORT` with the USB serial device (for example, `/dev/cu.usbserial-...`
on macOS or `COM4` on Windows).

## Configure and upload

Copy `firmware/config.example.json` to `firmware/config.json`. Set Wi-Fi credentials
and the address of your MQTT broker. The board connects to an existing broker;
it does not host one. Your local `config.json` is ignored by Git and loaded and
validated directly by the board at startup. Use valid JSON with double-quoted
keys and strings, and no comments. Give each board a unique `board_id`, or leave
it as `null` to use the full Wi-Fi MAC address.

From the repository root:

```sh
mpremote connect PORT mip install logging@0.6.2
mpremote connect PORT fs cp -r firmware/virtual_esp_board :
mpremote connect PORT fs cp firmware/config.json :config.json
mpremote connect PORT fs cp firmware/main.py :main.py
mpremote connect PORT reset
```

The board uses MicroPython's `logging` package for console output at INFO level.
Startup and online status use INFO; connection retries use WARNING. View logs
over USB with `mpremote connect PORT repl`. Credentials and command payloads
are not logged. Configuration errors propagate as tracebacks during startup.
Any other unexpected error is logged, and the board resets after 10 seconds.

Subscribe to `virtual-esp/v1/<board_id>/status` to see the boot session and
available capabilities. See the [protocol reference](../docs/protocol.md) for
commands and responses. The high-level host API is under development.

For TLS, set `"mqtt_tls": true`, select your broker's TLS port (usually 8883), and
upload its trusted CA certificate to the `mqtt_ca_file` path on the board. PEM
certificates are supported. Set `ntp_host` to a reachable NTP server; certificate
validation requires a correct clock. TLS verification is always enabled.

## GPIO availability

Supported pins: 0, 2, 4, 5, 12–15, 18–19, 21–23, 25–27, and 32–39. Some pins
may not be exposed on your DevKit. Pins 34–39 are input-only and have no internal
pull resistors. Flash pins 6–11, console pins 1/3, and possible PSRAM pins 16/17
are reserved. Check your board wiring around boot-strapping pins 0/2/5/12/15.

Outputs retain their last configured state during a network outage. Resetting the
board clears agent configuration. GPIO events are best-effort; precise timing
and reliable pulse counting need local hardware support.
