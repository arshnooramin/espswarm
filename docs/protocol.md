# MQTT protocol v1

The board agent speaks MQTT 3.1.1 and advertises its supported hardware
capabilities. The initial target is a classic ESP32 running MicroPython 1.29.0.

## Topics

All topics start with `virtual-esp/v1/<board_id>/`. Board IDs contain 1–32 ASCII
letters, digits, `_` or `-`. The default ID is the full Wi-Fi MAC address as 12
hexadecimal digits.

| Suffix | Direction | QoS | Retained |
| --- | --- | --- | --- |
| `request` | Client → board | 1 | Never |
| `response` | Board → clients | 1 | No |
| `status` | Board → clients | 1 | Yes |
| `events/gpio` | Board → clients | 0 | No |

Subscribe to responses before sending commands. Use globally unique request
IDs, such as UUIDs, because response topics are shared by clients of that board.
Broker permissions should restrict clients to the boards they control.

## Requests and responses

Commands are UTF-8 JSON objects of at most 1024 bytes. Each request must contain
exactly `v`, `id`, `session`, `op`, and `args`. IDs contain 1–64 ASCII letters,
digits, `_` or `-`; `args` is an object. Unknown arguments, duplicate JSON keys,
non-finite numbers, NULs, and nesting beyond eight levels are rejected.

```json
{"v":1,"id":"request-1","session":"current-boot-session","op":"gpio.write","args":{"pin":2,"level":1}}
```

The retained status message supplies the board's current `session` token. It
changes on each boot; commands for an older session cannot operate hardware.
For discovery, `board.info` accepts `"session": null` and empty `args`.

```json
{"v":1,"id":"request-1","session":"current-boot-session","ok":true,"result":{}}
```

```json
{"v":1,"id":"request-1","session":"current-boot-session","ok":false,"error":{"code":"invalid_state","message":"Configure pin as output first"}}
```

Unparseable requests have `"id": null`. Match both ID and session when accepting
a response. A broker PUBACK confirms MQTT delivery; a successful response confirms
that the board operation completed. Clients must set an application timeout.

## Operations

| Operation | Arguments | Result |
| --- | --- | --- |
| `board.info` | `{}` | Board ID, target, runtime, capabilities, payload and cache limits |
| `gpio.configure` | `pin`, `mode`: `input`/`output`; optional `pull`: `none`/`up`/`down`, `initial`: 0/1 for output | `{}` |
| `gpio.read` | `pin` configured as input | `{"level":0}` or `{"level":1}` |
| `gpio.write` | `pin` configured as output, `level`: 0/1 | `{}` |
| `gpio.watch` | `pin` configured as input, `edge`: `rising`/`falling`/`any` | `{}` |
| `gpio.unwatch` | `pin` | `{}` |
| `gpio.reset` | `pin` | `{}`; disables watching and restores input without pulls |

Numeric arguments must be JSON integers, not booleans or floating-point values.
Only advertised capabilities are available. Unsupported operations return
`unsupported_operation`.

## Retries and reconnects

The board caches the last eight processed requests and their responses in RAM.
An identical byte-for-byte retry with the same ID returns the cached response.
Reusing a cached ID for different bytes returns `id_conflict`. Failures are cached
as well. The cache survives MQTT reconnects and disappears on board reset.

This is bounded duplicate suppression, not a guarantee of exactly-once execution.
Once an entry is evicted, it can execute again. Do not automatically retry a
mutation after a timeout: its outcome may be unknown. Check the current state or
let the application decide how to recover. `unknown_outcome` means response
construction failed after the command was reserved; that ID will not be executed
again while it remains cached.

The board reconnects with a clean MQTT session, subscribes again, and publishes
online status after SUBACK. Its retained last will marks it offline following
connection loss. GPIO configuration persists across network reconnects, including
output levels; board resets clear configuration. Buffered MQTT commands from a
failed connection are discarded. A boot session token prevents commands from
being silently replayed across a reboot.

## GPIO events

Hard IRQ handlers capture the pin and level into a preallocated 16-entry queue.
Networking and JSON encoding happen in the main loop.

```json
{"v":1,"session":"current-boot-session","event":"gpio.edge","pin":5,"level":1,"sequence":1,"dropped":0}
```

Events are best-effort: QoS 0, queue overflow, and network loss can lose edges.
`dropped` reports queue overflow since the previous consumed event, saturated at
65535. `sequence` wraps at 2³⁰. Offline events are discarded on reconnect. Events
already queued before `unwatch` may still arrive. This interface is for remote
observation, not precise timing or guaranteed pulse counting.

## Transport limits

The agent supports QoS 0/1 and one outstanding outbound acknowledgment at a time.
It queues at most four commands while waiting for an acknowledgment and closes
the connection on overflow or malformed/oversized MQTT frames. Socket operations
have a total deadline (five seconds by default); DNS resolution is performed by
the runtime and is outside that deadline. Wi-Fi attempts time out after 20 seconds
by default. Reconnect delays increase from one second to a maximum of 30 seconds.

TLS uses certificate and hostname verification against the configured CA file.
NTP sets the device clock before each TLS connection so certificate validity can
be checked. Username/password authentication is optional.
