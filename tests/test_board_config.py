import json
from pathlib import Path

import pytest
from espswarm_agent.settings import MAX_CONFIG_BYTES, Settings

REQUIRED = {"wifi_ssid": "home", "mqtt_host": "broker"}


def test_load_configuration_and_apply_defaults(tmp_path):
    source = tmp_path / "config.json"
    source.write_text(json.dumps({**REQUIRED, "wifi_password": "secret"}))
    settings = Settings.from_file(str(source))
    assert settings.wifi_ssid == "home"
    assert settings.wifi_password == "secret"
    assert settings.mqtt_host == "broker"
    assert settings.mqtt_port == 1883
    assert settings.keepalive == 30
    assert settings.board_id is None


def test_example_configuration_loads():
    source = Path(__file__).resolve().parents[1] / "firmware/config.example.json"
    settings = Settings.from_file(str(source))
    assert settings.wifi_ssid == "your-network"
    assert settings.mqtt_host == "192.168.1.10"


def test_overrides_are_local_to_each_instance():
    custom = Settings(**REQUIRED, mqtt_port=8883)
    default = Settings(**REQUIRED)
    assert custom.mqtt_port == 8883
    assert default.mqtt_port == 1883


def test_credentials():
    settings = Settings(**REQUIRED, mqtt_username="user", mqtt_password="secret")
    assert settings.mqtt_username == "user"
    assert settings.mqtt_password == "secret"


@pytest.mark.parametrize(
    "changes",
    [
        {"mqtt_host": ""},
        {"wifi_ssid": None},
        {"mqtt_port": True},
        {"mqtt_port": 65536},
        {"board_id": "board/#"},
        {"socket_timeout": 0},
        {"keepalive": 9},
        {"mqtt_tls": 1},
        {"mqtt_password": "secret"},
        {"mqtt_username": 1},
        {"mqtt_tls": True, "mqtt_ca_file": ""},
        {"mqtt_tls": True, "ntp_host": ""},
        {"mqtt_host": "host\x00extra"},
        {"wifi_ssid": "a" * 33},
        {"wifi_password": "é" * 32},
    ],
)
def test_invalid_settings_fail_before_connection(changes):
    with pytest.raises(ValueError):
        Settings(**{**REQUIRED, **changes})


@pytest.mark.parametrize(
    "name", ["from_file", "_validate_strings", "__class__", "MQTT_BROKER"]
)
def test_configuration_cannot_override_class_members(name):
    with pytest.raises(TypeError):
        Settings(**REQUIRED, **{name: None})


@pytest.mark.parametrize(
    "content",
    [
        b'{"wifi_ssid":"home","wifi_ssid":"duplicate","mqtt_host":"broker"}',
        b'{"wifi_ssid":"home","mqtt_host":"broker","mqtt_port":true}',
        b'{"wifi_ssid":"home","mqtt_host":"broker",}',
        b"wifi_ssid: home\nmqtt_host: broker\n",
        b"[]",
        b"",
        b"\xff",
    ],
)
def test_invalid_configuration_is_rejected(tmp_path, content):
    source = tmp_path / "config.json"
    source.write_bytes(content)
    with pytest.raises(ValueError):
        Settings.from_file(str(source))


def test_configuration_size_limit(tmp_path):
    source = tmp_path / "config.json"
    content = json.dumps(REQUIRED).encode()
    source.write_bytes(content.ljust(MAX_CONFIG_BYTES, b" "))
    assert Settings.from_file(str(source)).mqtt_host == "broker"
    source.write_bytes(content.ljust(MAX_CONFIG_BYTES + 1, b" "))
    with pytest.raises(ValueError, match="Configuration exceeds"):
        Settings.from_file(str(source))


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"wifi_ssid": "home"},
        {"mqtt_host": "broker"},
        {**REQUIRED, "unknown": True},
    ],
)
def test_missing_or_unknown_parameters_are_rejected_from_file(tmp_path, values):
    source = tmp_path / "config.json"
    source.write_text(json.dumps(values))
    with pytest.raises(TypeError):
        Settings.from_file(str(source))
