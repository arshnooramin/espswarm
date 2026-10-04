# Virtual ESP

A Python library for controlling physical ESP32 boards over MQTT, with companion
firmware running on the board. Intended for remote hardware interaction and data
collection across applications.

Requires Python 3.14 or newer on the host and MicroPython 1.29.0 on the ESP32.

## Installation

Install from a local checkout:

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install .
```

On Windows, activate with `.venv\Scripts\activate`.

## Usage

The package is under development; board-control APIs are not available yet.
The [MicroPython board agent](firmware/README.md) provides GPIO commands and
interrupt events over the [MQTT protocol](docs/protocol.md). Physical board
validation is still required. Operations that require precise timing must run
on the ESP32.

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest
ruff check .
black --check .
python -m build
```
