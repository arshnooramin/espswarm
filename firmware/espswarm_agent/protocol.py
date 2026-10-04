"""Versioned board commands, validation, and bounded duplicate suppression."""

from .json_codec import dumps, loads
from .limits import MAX_IDENTITY_LENGTH, MAX_PAYLOAD, MAX_REQUEST_ID_LENGTH
from .validation import CommandError, is_token, require_keys

# Reported in status so a fleet's agent versions can be audited.
AGENT_VERSION = "0.1.0.dev0"
VERSION = 1
CACHE_SIZE = 8
MAX_OPERATION_LENGTH = 48
TOPIC_PREFIX = "espswarm/v" + str(VERSION) + "/"
REQUEST_FIELDS = {"v", "id", "session", "op", "args"}
# Stay within MicroPython's small-integer range on 32-bit boards.
EVENT_SEQUENCE_MODULUS = 1 << 30


class CacheEntry:
    def __init__(self, request_id, payload):
        self.request_id = request_id
        self.payload = payload
        # Stays None if execution never produced an encoded response.
        self.response = None


class ResponseCache:
    """The most recent request IDs with their exact payloads and responses."""

    def __init__(self, size=CACHE_SIZE):
        self.size = size
        self._entries = [None] * size
        self._next = 0

    def lookup(self, request_id, payload):
        for entry in self._entries:
            if entry is None or entry.request_id != request_id:
                continue
            if entry.payload != payload:
                raise CommandError("id_conflict", "Request ID was already used")
            return entry
        return None

    def reserve(self, request_id, payload):
        entry = CacheEntry(request_id, payload)
        self._entries[self._next] = entry
        self._next = (self._next + 1) % self.size
        return entry


class Protocol:
    def __init__(self, board_id, session, peripherals):
        if not is_token(board_id, MAX_IDENTITY_LENGTH) or not is_token(
            session, MAX_IDENTITY_LENGTH
        ):
            raise ValueError("Invalid board identity or session")
        self.board_id = board_id
        self.session = session
        self.cache = ResponseCache()
        self.capabilities = []
        self._routes = {}
        for peripheral in peripherals:
            self.capabilities.extend(peripheral.capabilities)
            for operation in peripheral.operations:
                if operation in self._routes:
                    raise ValueError("Operation is provided twice: " + operation)
                self._routes[operation] = peripheral
        self._sequence = 0
        self.client_id = ("espswarm-" + board_id).encode()
        self.request_topic = self._topic("request")
        self.response_topic = self._topic("response")
        self.status_topic = self._topic("status")
        self.event_topic = self._topic("events/gpio")

    def _topic(self, suffix):
        return (TOPIC_PREFIX + self.board_id + "/" + suffix).encode()

    def _envelope(self, fields):
        message = {"v": VERSION, "session": self.session}
        message.update(fields)
        return message

    def information(self):
        return {
            "board_id": self.board_id,
            "target": "esp32",
            "runtime": "micropython",
            "agent_version": AGENT_VERSION,
            "capabilities": self.capabilities,
            "max_payload": MAX_PAYLOAD,
            "response_cache_size": CACHE_SIZE,
        }

    def status(self, online):
        fields = self.information()
        fields["online"] = online
        return dumps(self._envelope(fields))

    def event(self, pin, level, dropped):
        self._sequence = (self._sequence + 1) % EVENT_SEQUENCE_MODULUS
        return dumps(
            self._envelope(
                {
                    "event": "gpio.edge",
                    "pin": pin,
                    "level": level,
                    "sequence": self._sequence,
                    "dropped": dropped,
                }
            )
        )

    def error(self, request_id, code, message):
        return self._envelope(
            {"id": request_id, "ok": False, "error": {"code": code, "message": message}}
        )

    def handle(self, payload, retained=False):
        """Return the encoded response to one request payload."""
        request_id = None
        try:
            request = self._parse_request(payload)
            if is_token(request.get("id"), MAX_REQUEST_ID_LENGTH):
                request_id = request["id"]
            self._validate_request(request, request_id, retained)
            cached = self.cache.lookup(request_id, payload)
        except CommandError as error:
            return dumps(self.error(request_id, error.code, error.message))
        if cached is not None:
            return cached.response or dumps(
                self.error(
                    request_id, "unknown_outcome", "Operation outcome is unavailable"
                )
            )
        # Reserve before hardware access so the command never runs twice, even
        # if building its response fails.
        entry = self.cache.reserve(request_id, payload)
        entry.response = self._encode_response(
            request_id, self._execute(request_id, request)
        )
        return entry.response

    def _parse_request(self, payload):
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_PAYLOAD:
            raise CommandError(
                "invalid_request", "Payload must be 1.." + str(MAX_PAYLOAD) + " bytes"
            )
        try:
            request = loads(payload)
        except (ValueError, UnicodeError):
            raise CommandError("invalid_request", "Invalid JSON") from None
        if not isinstance(request, dict):
            raise CommandError("invalid_request", "Expected an object")
        return request

    def _validate_request(self, request, request_id, retained):
        if retained:
            raise CommandError("retained_request", "Commands must not be retained")
        if request_id is None or set(request) != REQUEST_FIELDS:
            raise CommandError(
                "invalid_request", "Expected v, id, session, op and args"
            )
        if type(request["v"]) is not int or request["v"] != VERSION:
            raise CommandError(
                "unsupported_version", "Expected protocol version " + str(VERSION)
            )
        operation = request["op"]
        if (
            not isinstance(operation, str)
            or not 1 <= len(operation) <= MAX_OPERATION_LENGTH
            or not isinstance(request["args"], dict)
        ):
            raise CommandError("invalid_request", "Invalid operation or arguments")
        if request["session"] != self.session and not (
            operation == "board.info" and request["session"] is None
        ):
            raise CommandError("stale_session", "Discover the current board session")

    def _execute(self, request_id, request):
        try:
            result = self._dispatch(request["op"], request["args"])
        except CommandError as error:
            return self.error(request_id, error.code, error.message)
        except OSError:
            return self.error(request_id, "hardware_error", "Hardware operation failed")
        except Exception:
            return self.error(
                request_id, "internal_error", "Operation could not be completed"
            )
        return self._envelope({"id": request_id, "ok": True, "result": result})

    def _dispatch(self, operation, args):
        if operation == "board.info":
            require_keys(args, ())
            return self.information()
        peripheral = self._routes.get(operation)
        if peripheral is None:
            raise CommandError("unsupported_operation", "Operation is not supported")
        return peripheral.execute(operation, args)

    def _encode_response(self, request_id, response):
        try:
            payload = dumps(response)
        except (TypeError, ValueError, OverflowError):
            return dumps(
                self.error(
                    request_id, "internal_error", "Operation could not be completed"
                )
            )
        if len(payload) > MAX_PAYLOAD:
            return dumps(
                self.error(
                    request_id, "response_too_large", "Result exceeds payload limit"
                )
            )
        return payload
