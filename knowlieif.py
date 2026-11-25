"""Knowlieif: a small, quotation-based stack language (standard library only)."""

from __future__ import annotations

import argparse
import binascii
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
import json
import operator
from pathlib import Path
import re
import sys
from typing import TextIO


class KnowlieifError(Exception):
    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        super().__init__(message)
        self.line = line
        self.column = column

    def __str__(self) -> str:
        message = super().__str__()
        if self.line is None:
            return message
        return f"{self.line}:{self.column}: {message}"


@dataclass(frozen=True)
class Token:
    text: str
    line: int
    column: int


@dataclass(frozen=True)
class Word:
    name: str
    line: int | None
    column: int | None


@dataclass(frozen=True)
class Quote:
    body: Program


Value = int | float | bool | str | bytes | Quote
Instruction = Value | Word
Program = tuple[Instruction, ...]


# A quoted JSON string is one token; a malformed quote becomes a syntax error.
_TOKEN = re.compile(r'\s+|#[^\n]*|"(?:\\.|[^"\\\n])*"|\[|\]|[^\s\[\]#"]+|.', re.S)
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")


def tokenize(source: str) -> list[Token]:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[Token] = []
    line, column = 1, 1
    for match in _TOKEN.finditer(source):
        text = match.group()
        start_line, start_column = line, column
        if "\n" in text:
            line += text.count("\n")
            column = len(text) - text.rfind("\n")
        else:
            column += len(text)
        if text.isspace() or text.startswith("#"):
            continue
        if text == '"':
            raise KnowlieifError("unterminated string", start_line, start_column)
        tokens.append(Token(text, start_line, start_column))
    return tokens


# Language-level nesting limit, independent of Python's call stack.
MAX_DEPTH = 1024


def parse(source: str) -> Program:
    result: list[Instruction] = []
    parents: list[tuple[Token, list[Instruction]]] = []

    for token in tokenize(source):
        raw = token.text
        if raw == "[":
            if len(parents) == MAX_DEPTH:
                raise KnowlieifError("recursion limit exceeded (quotation nesting)", token.line, token.column)
            parents.append((token, result))
            result = []
        elif raw == "]":
            if not parents:
                raise KnowlieifError("unexpected ']'", token.line, token.column)
            body = Quote(tuple(result))
            _, result = parents.pop()
            result.append(body)
        elif raw.startswith('"'):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as error:
                raise KnowlieifError("invalid string literal", token.line, token.column + error.pos) from None
            result.append(value)
        elif raw == "true":
            result.append(True)
        elif raw == "false":
            result.append(False)
        elif _NUMBER.fullmatch(raw):
            try:
                value = float(raw) if "." in raw else int(raw)
            except ValueError:
                raise KnowlieifError("numeric literal out of range", token.line, token.column) from None
            result.append(value)
        else:
            result.append(Word(raw, token.line, token.column))

    if parents:
        opener, _ = parents[-1]
        raise KnowlieifError("unclosed '['", opener.line, opener.column)
    return tuple(result)


_ARITHMETIC = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "%": operator.mod,
}
_COMPARE = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}
_CONTROL = {"dup", "drop", "swap", ".", "def", "call", "if", "times"}
_BINARY = {"hex", "utf8", "cat", "len", "u32be", "crc32"}
_RESERVED = set(_ARITHMETIC) | set(_COMPARE) | _CONTROL | _BINARY | {"==", "true", "false"}


def number(value: object) -> bool:
    return type(value) in (int, float)


def render(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Quote):
        return f"<quote:{len(value.body)}>"
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def repeated(body: Program, count: int) -> Iterator[Instruction]:
    for _ in range(count):
        yield from body


class Knowlieif:
    def __init__(self, output: TextIO | None = None, *, max_steps: int | None = None) -> None:
        if max_steps is not None and (type(max_steps) is not int or max_steps < 0):
            raise ValueError("max_steps must be a nonnegative integer or None")
        self.max_steps = max_steps
        self.stack: list[Value] = []
        self.words: dict[str, Quote] = {}
        self.output = sys.stdout if output is None else output

    def run(self, source: str) -> None:
        # Parsing the whole input before evaluation avoids partial syntax effects.
        self.execute(parse(source))

    def execute(self, program: Iterable[Instruction]) -> None:
        frames = [iter(program)]
        remaining = self.max_steps
        while frames:
            try:
                item = next(frames[-1])
            except StopIteration:
                frames.pop()
                continue
            if remaining is not None:
                if remaining == 0:
                    line, column = (item.line, item.column) if isinstance(item, Word) else (None, None)
                    raise KnowlieifError("step limit exceeded", line, column)
                remaining -= 1
            if not isinstance(item, Word):
                self.stack.append(item)
                continue
            try:
                pending = self._apply(item.name)
                if pending is not None:
                    body, count = pending
                    if body and count:
                        if len(frames) > MAX_DEPTH:
                            raise KnowlieifError("recursion limit exceeded (execution nesting)")
                        frames.append(repeated(body, count))
            except KnowlieifError as error:
                if error.line is None:
                    error.line, error.column = item.line, item.column
                raise

    def take(self, count: int, name: str) -> tuple[Value, ...]:
        if len(self.stack) < count:
            raise KnowlieifError(f"{name}: stack underflow (need {count})")
        values = tuple(self.stack[-count:])
        del self.stack[-count:]
        return values

    def word(self, name: str) -> None:
        self.execute((Word(name, None, None),))

    def _apply(self, name: str) -> tuple[Program, int] | None:
        if name in _ARITHMETIC:
            left, right = self.take(2, name)
            if not number(left) or not number(right):
                raise KnowlieifError(f"{name}: expected two numbers")
            if name in {"/", "%"} and right == 0:
                raise KnowlieifError(f"{name}: division by zero")
            try:
                result = _ARITHMETIC[name](left, right)
            except (OverflowError, ValueError) as error:
                raise KnowlieifError(f"{name}: {error}") from None
            self.stack.append(result)
        elif name in _COMPARE:
            left, right = self.take(2, name)
            if not number(left) or not number(right):
                raise KnowlieifError(f"{name}: expected two numbers")
            self.stack.append(_COMPARE[name](left, right))
        elif name == "==":
            left, right = self.take(2, name)
            if isinstance(left, Quote) or isinstance(right, Quote):
                raise KnowlieifError("==: quotations cannot be compared")
            if number(left) and number(right):
                self.stack.append(left == right)
            else:
                self.stack.append(type(left) is type(right) and left == right)
        elif name == "dup":
            (value,) = self.take(1, name)
            self.stack.extend((value, value))
        elif name == "drop":
            self.take(1, name)
        elif name == "swap":
            left, right = self.take(2, name)
            self.stack.extend((right, left))
        elif name == ".":
            (value,) = self.take(1, name)
            try:
                print(render(value), file=self.output, flush=True)
            except (ValueError, UnicodeError, OSError):
                raise KnowlieifError(".: value cannot be displayed") from None
        elif name == "def":
            name_value, body = self.take(2, name)
            if not isinstance(name_value, str) or not name_value.isidentifier():
                raise KnowlieifError("def: expected an identifier string")
            if not isinstance(body, Quote):
                raise KnowlieifError("def: expected a quotation")
            if name_value in _RESERVED:
                raise KnowlieifError(f"def: reserved word '{name_value}'")
            self.words[name_value] = body
        elif name == "call":
            (body,) = self.take(1, name)
            if not isinstance(body, Quote):
                raise KnowlieifError("call: expected a quotation")
            return body.body, 1
        elif name == "if":
            condition, yes, no = self.take(3, name)
            if not isinstance(condition, bool):
                raise KnowlieifError("if: expected a boolean condition")
            if not isinstance(yes, Quote) or not isinstance(no, Quote):
                raise KnowlieifError("if: expected two quotations")
            return (yes.body if condition else no.body), 1
        elif name == "times":
            count, body = self.take(2, name)
            if type(count) is not int or count < 0:
                raise KnowlieifError("times: expected a nonnegative integer")
            if not isinstance(body, Quote):
                raise KnowlieifError("times: expected a quotation")
            return body.body, count
        elif name in _BINARY:
            self._binary(name)
        elif name in self.words:
            return self.words[name].body, 1
        else:
            raise KnowlieifError(f"unknown word '{name}'")
        return None

    def _binary(self, name: str) -> None:
        if name == "cat":
            left, right = self.take(2, name)
            if not isinstance(left, bytes) or not isinstance(right, bytes):
                raise KnowlieifError("cat: expected two byte strings")
            self.stack.append(left + right)
            return

        (value,) = self.take(1, name)
        if name in {"hex", "utf8"}:
            if not isinstance(value, str):
                raise KnowlieifError(f"{name}: expected a string")
            try:
                result = bytes.fromhex(value) if name == "hex" else value.encode("utf-8")
            except ValueError:
                raise KnowlieifError(f"{name}: invalid input") from None
            self.stack.append(result)
        elif name == "u32be":
            if type(value) is not int or not 0 <= value <= 0xffffffff:
                raise KnowlieifError("u32be: expected an unsigned 32-bit integer")
            self.stack.append(value.to_bytes(4, "big"))
        else:
            if not isinstance(value, bytes):
                raise KnowlieifError(f"{name}: expected a byte string")
            self.stack.append(len(value) if name == "len" else binascii.crc32(value))


def nonnegative_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from None
    if result < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Knowlieif stack-language interpreter")
    parser.add_argument("file", nargs="?", help="run a .knowlieif source file")
    parser.add_argument("-e", "--eval", help="run the supplied source")
    parser.add_argument("--steps", type=nonnegative_int, help="maximum evaluated items per run")
    parser.add_argument("--binary", action="store_true", help="write the final byte string to stdout")
    args = parser.parse_args(argv)
    if args.file is not None and args.eval is not None:
        parser.error("choose a file or -e")
    batch = args.file is not None or args.eval is not None
    if args.binary and not batch:
        parser.error("--binary requires a file or -e")
    interpreter = Knowlieif(output=sys.stderr if args.binary else None, max_steps=args.steps)

    if batch:
        try:
            source = Path(args.file).read_text(encoding="utf-8") if args.file is not None else args.eval
            interpreter.run(source)
            if args.binary:
                if len(interpreter.stack) != 1 or not isinstance(interpreter.stack[0], bytes):
                    raise KnowlieifError("--binary requires exactly one byte string on the final stack")
                sys.stdout.buffer.write(interpreter.stack[0])
                sys.stdout.buffer.flush()
        except UnicodeDecodeError:
            print(f"knowlieif: {args.file}: invalid UTF-8 source", file=sys.stderr)
            return 1
        except (KnowlieifError, OSError) as error:
            print(f"knowlieif: {error}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("knowlieif: interrupted", file=sys.stderr)
            return 130
        return 0

    print("Knowlieif REPL (one line per input; Ctrl-D to quit)")
    while True:
        try:
            source = input("knowlieif> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        # A failed line does not corrupt the next line's data/word definitions.
        stack, words = interpreter.stack.copy(), interpreter.words.copy()
        try:
            interpreter.run(source)
        except (KnowlieifError, KeyboardInterrupt) as error:
            interpreter.stack, interpreter.words = stack, words
            message = "interrupted" if isinstance(error, KeyboardInterrupt) else str(error)
            print(f"knowlieif: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
