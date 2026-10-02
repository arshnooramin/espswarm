# Virtual ESP

A Python library for controlling physical ESP32 boards over MQTT, with companion
firmware running on the board. Intended for remote hardware interaction and data
collection across applications.

Requires Python 3.14 or newer. ESP32 is the initial hardware target.

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
The existing ESP32 firmware is experimental and has not been validated with
this package. Operations that require precise timing must run on the ESP32.

## Development

```sh
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
python -m build
```
