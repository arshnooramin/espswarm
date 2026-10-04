"""Validated board settings, loaded as data rather than executable code."""

from .json_codec import loads
from .limits import MAX_IDENTITY_LENGTH
from .validation import is_integer, is_token

MAX_CONFIG_BYTES = 4096

# Wi-Fi SSID and passphrase limits; raw hexadecimal PSKs are not supported here.
MAX_WIFI_SSID_BYTES = 32
MAX_WIFI_PASSWORD_BYTES = 63
# Hostnames are bounded in their encoded form; this is not DNS syntax validation.
MAX_HOSTNAME_BYTES = 253
# Library memory budgets, rather than filesystem or MQTT protocol limits.
MAX_CA_FILE_PATH_BYTES = 128
MAX_MQTT_CREDENTIAL_BYTES = 128

# Nonzero network port range.
MQTT_PORT_RANGE = (1, 65535)
# Library policy: bound connection waits and keepalive intervals.
WIFI_TIMEOUT_RANGE_SECONDS = (1, 120)
SOCKET_TIMEOUT_RANGE_SECONDS = (1, 30)
KEEPALIVE_RANGE_SECONDS = (10, 300)


def text(max_bytes, required=False, nullable=False):
    def check(name, value):
        if value is None and nullable:
            return
        if not isinstance(value, str):
            raise ValueError(name + " must be a string")
        if required and not value:
            raise ValueError(name + " must not be empty")
        if len(value.encode("utf-8")) > max_bytes:
            raise ValueError(
                name + " must be at most " + str(max_bytes) + " UTF-8 bytes"
            )
        if "\x00" in value:
            raise ValueError(name + " must not contain NUL characters")

    return check


def integer(bounds, unit=""):
    minimum, maximum = bounds
    suffix = " " + unit if unit else ""

    def check(name, value):
        if not is_integer(value, minimum, maximum):
            raise ValueError(
                name
                + " must be an integer between "
                + str(minimum)
                + " and "
                + str(maximum)
                + suffix
                + " (inclusive)"
            )

    return check


def boolean(name, value):
    if type(value) is not bool:
        raise ValueError(name + " must be a boolean")


def identity(name, value):
    if value is not None and not is_token(value, MAX_IDENTITY_LENGTH):
        raise ValueError(
            name
            + " must be 1-"
            + str(MAX_IDENTITY_LENGTH)
            + " ASCII letters, digits, '_' or '-'"
        )


REQUIRED = object()

# Every accepted setting: name, default, and validator.
FIELDS = (
    ("wifi_ssid", REQUIRED, text(MAX_WIFI_SSID_BYTES, required=True)),
    ("mqtt_host", REQUIRED, text(MAX_HOSTNAME_BYTES, required=True)),
    ("wifi_password", "", text(MAX_WIFI_PASSWORD_BYTES)),
    ("mqtt_port", 1883, integer(MQTT_PORT_RANGE)),
    ("mqtt_username", None, text(MAX_MQTT_CREDENTIAL_BYTES, nullable=True)),
    ("mqtt_password", None, text(MAX_MQTT_CREDENTIAL_BYTES, nullable=True)),
    ("mqtt_tls", False, boolean),
    ("mqtt_ca_file", "broker-ca.pem", text(MAX_CA_FILE_PATH_BYTES)),
    ("ntp_host", "pool.ntp.org", text(MAX_HOSTNAME_BYTES)),
    ("board_id", None, identity),
    ("wifi_timeout", 20, integer(WIFI_TIMEOUT_RANGE_SECONDS, "seconds")),
    ("socket_timeout", 5, integer(SOCKET_TIMEOUT_RANGE_SECONDS, "seconds")),
    ("keepalive", 30, integer(KEEPALIVE_RANGE_SECONDS, "seconds")),
)


class Settings:
    """Board settings; each entry in FIELDS becomes an attribute."""

    def __init__(self, **values):
        for name, default, check in FIELDS:
            value = values.pop(name, default)
            if value is REQUIRED:
                raise TypeError("Missing setting: " + name)
            check(name, value)
            setattr(self, name, value)
        if values:
            raise TypeError("Unknown settings: " + ", ".join(sorted(values)))
        if self.mqtt_password is not None and self.mqtt_username is None:
            raise ValueError("mqtt_password requires mqtt_username")
        if self.mqtt_tls and (not self.mqtt_ca_file or not self.ntp_host):
            raise ValueError("TLS requires a CA file and NTP host")

    @classmethod
    def from_file(cls, path="config.json"):
        with open(path, "rb") as file:
            payload = file.read(MAX_CONFIG_BYTES + 1)
        if len(payload) > MAX_CONFIG_BYTES:
            raise ValueError(
                "Configuration exceeds " + str(MAX_CONFIG_BYTES) + " bytes"
            )
        values = loads(payload)
        if not isinstance(values, dict):
            raise ValueError("Configuration must be an object")
        return cls(**values)
