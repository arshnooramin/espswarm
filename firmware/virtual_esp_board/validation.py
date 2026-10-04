"""Value checks shared by commands and settings, and the command error type."""

TOKEN_CHARACTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


class CommandError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def is_integer(value, minimum, maximum):
    # bool is an int subclass, but is never a valid number here.
    return type(value) is int and minimum <= value <= maximum


def is_token(value, maximum):
    return (
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and all(c in TOKEN_CHARACTERS for c in value)
    )


def require_keys(args, required, optional=()):
    if not isinstance(args, dict) or any(key not in args for key in required):
        raise CommandError("invalid_args", "Missing required arguments")
    if any(key not in required and key not in optional for key in args):
        raise CommandError("invalid_args", "Unsupported arguments")
