# Virtual ESP

Control physical ESP32 boards from Python over MQTT. A MicroPython agent runs on
the board; this library talks to it through your broker.

```mermaid
flowchart LR
    App[Your code] --> Lib[virtual_esp]
    Lib <--> Broker[(MQTT broker)]
    Broker <--> Agent[Board agent]
    Agent --> Pins[GPIO]
```

Requires Python 3.14+ and MicroPython 1.29.0. Not yet validated on hardware.

## Usage

```python
from virtual_esp import Board, RequestTimeout

with Board("workbench", host="192.168.1.10") as board:
    board.call("gpio.configure", {"pin": 2, "mode": "output"})
    board.call("gpio.write", {"pin": 2, "level": 1})
```

| Exception | Meaning |
| --- | --- |
| `RequestTimeout` | No response in time; the operation may or may not have run |
| `BoardRestarted` | Board rebooted and lost pin configuration; reconfigure and continue |
| `BoardOffline` | Board or broker connection is down |
| `BoardError` subclasses | Board rejected the request (`InvalidState`, `Busy`, …) |

`call()` is thread-safe. See the [protocol](docs/protocol.md) for operations,
timeouts and failure handling, and the [board agent](firmware/README.md) for
flashing.

## Layout

| Path | Contents |
| --- | --- |
| `src/virtual_esp/` | Host library: `Board`, `MQTTTransport`, protocol encoding, errors |
| `firmware/` | MicroPython board agent |
| `docs/protocol.md` | Protocol specification |
| `tests/` | `test_host_*` (library), `test_board_*` (firmware), MicroPython smoke test |

## Development

```sh
python -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
ruff check . && black --check .
python -m build
```

Host tests run against the real firmware protocol in-process
(`tests/host_fakes.py`); no broker or board is needed.
