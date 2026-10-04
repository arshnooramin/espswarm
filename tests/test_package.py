"""Verify the installed distribution and side-effect-free imports."""

import importlib.metadata
import importlib.resources
import subprocess
import sys
import tempfile


class TestPackage:

    def test_distribution_contains_typed_library_only(self):
        distribution = importlib.metadata.distribution("virtual-esp")
        files = {str(file) for file in distribution.files or ()}
        package = importlib.resources.files("virtual_esp")
        assert package.joinpath("__init__.py").is_file()
        assert package.joinpath("py.typed").is_file()
        assert not any(file.startswith("server/") for file in files)
        assert not distribution.entry_points

    def test_import_without_checkout_or_network(self):
        script = """
import socket
import threading

def reject_connection(*args, **kwargs):
    raise AssertionError("Import attempted a network connection")

socket.socket.connect = reject_connection
socket.socket.connect_ex = reject_connection
before = set(threading.enumerate())
import virtual_esp
assert set(threading.enumerate()) == before, "Import started a background thread"
assert virtual_esp.__doc__
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-c", script],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert result.stderr == ""
