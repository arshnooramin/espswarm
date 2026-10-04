"""Small, bounded JSON decoder with consistent CPython/MicroPython validation."""

import json

MAX_NESTING_DEPTH = 8


class Decoder:
    def __init__(self, text):
        self.text = text
        self.position = 0

    def whitespace(self):
        while self.position < len(self.text) and self.text[self.position] in " \t\r\n":
            self.position += 1

    def take(self, character):
        self.whitespace()
        if self.position < len(self.text) and self.text[self.position] == character:
            self.position += 1
            return True
        return False

    def string(self):
        self.whitespace()
        start = self.position
        if not self.take('"'):
            raise ValueError("Expected string")
        while self.position < len(self.text):
            character = self.text[self.position]
            self.position += 1
            if character == '"':
                value = json.loads(self.text[start : self.position])
                if "\x00" in value:
                    raise ValueError("NUL is not allowed")
                return value
            if ord(character) < 32:
                raise ValueError("Unescaped control character")
            if character == "\\":
                if self.position >= len(self.text):
                    break
                escape = self.text[self.position]
                self.position += 1
                if escape == "u":
                    digits = self.text[self.position : self.position + 4]
                    if len(digits) != 4 or any(
                        c not in "0123456789abcdefABCDEF" for c in digits
                    ):
                        raise ValueError("Invalid Unicode escape")
                    self.position += 4
                elif escape not in '"\\/bfnrt':
                    raise ValueError("Invalid escape")
        raise ValueError("Unterminated string")

    def number(self):
        start = self.position
        self.take("-")
        if self.position >= len(self.text):
            raise ValueError("Missing number")
        if self.text[self.position] == "0":
            self.position += 1
        else:
            if self.text[self.position] not in "123456789":
                raise ValueError("Invalid number")
            self.digits()
        if self.position < len(self.text) and self.text[self.position] == ".":
            self.position += 1
            self.digits()
        if self.position < len(self.text) and self.text[self.position] in "eE":
            self.position += 1
            if self.position < len(self.text) and self.text[self.position] in "+-":
                self.position += 1
            self.digits()
        value = json.loads(self.text[start : self.position])
        if isinstance(value, float) and (
            value != value or value in (float("inf"), -float("inf"))
        ):
            raise ValueError("Non-finite number")
        return value

    def digits(self):
        start = self.position
        while (
            self.position < len(self.text) and self.text[self.position] in "0123456789"
        ):
            self.position += 1
        if self.position == start:
            raise ValueError("Missing digits")

    def value(self, depth=0):
        if depth > MAX_NESTING_DEPTH:
            raise ValueError("JSON is nested too deeply")
        self.whitespace()
        if self.position >= len(self.text):
            raise ValueError("Missing value")
        character = self.text[self.position]
        if character == '"':
            return self.string()
        if self.take("{"):
            result = {}
            if self.take("}"):
                return result
            while True:
                key = self.string()
                if key in result:
                    raise ValueError("Duplicate key")
                if not self.take(":"):
                    raise ValueError("Missing colon")
                result[key] = self.value(depth + 1)
                if self.take("}"):
                    return result
                if not self.take(","):
                    raise ValueError("Missing comma")
        if self.take("["):
            result = []
            if self.take("]"):
                return result
            while True:
                result.append(self.value(depth + 1))
                if self.take("]"):
                    return result
                if not self.take(","):
                    raise ValueError("Missing comma")
        for literal, value in (("true", True), ("false", False), ("null", None)):
            if self.text[self.position : self.position + len(literal)] == literal:
                self.position += len(literal)
                return value
        return self.number()


def loads(payload):
    decoder = Decoder(payload.decode("utf-8"))
    result = decoder.value()
    decoder.whitespace()
    if decoder.position != len(decoder.text):
        raise ValueError("Trailing data")
    return result


def dumps(value):
    return json.dumps(value).encode("utf-8")
