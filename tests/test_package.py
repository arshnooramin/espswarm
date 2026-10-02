"""Verify the installed distribution and side-effect-free imports."""

import importlib.metadata
import importlib.resources
import subprocess
import sys
import tempfile
import unittest


class PackageTests(unittest.TestCase):
    def test_distribution_contains_typed_library_only(self):
        distribution = importlib.metadata.distribution("virtual-esp")
        files = {str(file) for file in distribution.files or ()}
        package = importlib.resources.files("virtual_esp")
        self.assertTrue(package.joinpath("__init__.py").is_file())
        self.assertTrue(package.joinpath("py.typed").is_file())
        self.assertFalse(any(file.startswith("server/") for file in files))
        self.assertFalse(distribution.entry_points)

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
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
