"""Observable behavior and failure boundaries for Knowlieif."""

import io
import operator
from pathlib import Path
import random
import struct
import zlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from knowlieif import MAX_DEPTH, Knowlieif, KnowlieifError, main, parse


class KnowlieifTests(unittest.TestCase):
    def execute(self, source: str) -> tuple[list[object], str]:
        output = io.StringIO()
        machine = Knowlieif(output)
        machine.run(source)
        return machine.stack, output.getvalue()

    def test_numbers_negative_decimal_and_arithmetic(self):
        stack, output = self.execute("-3 2 * 1.5 + .")
        self.assertEqual(stack, [])
        self.assertEqual(output, "-4.5\n")

    def test_comments_and_string_escapes(self):
        stack, output = self.execute('"a#b\\nnext" . # ignore this\n"ok" .')
        self.assertEqual(stack, [])
        self.assertEqual(output, "a#b\nnext\nok\n")

    def test_quote_is_not_eager(self):
        stack, output = self.execute('[ missing ] drop "ok" .')
        self.assertEqual(stack, [])
        self.assertEqual(output, "ok\n")

    def test_call_and_swap(self):
        stack, _ = self.execute('2 7 swap - [ dup * ] call')
        self.assertEqual(stack, [25])

    def test_definition_and_late_binding(self):
        stack, _ = self.execute('"double" [ 2 * ] def "quad" [ double double ] def 3 quad')
        self.assertEqual(stack, [12])

    def test_recursion_factorial(self):
        stack, output = self.execute("""
            "fact" [
                dup 1 <=
                [ drop 1 ]
                [ dup 1 - fact * ]
                if
            ] def
            6 fact .
        """)
        self.assertEqual(stack, [])
        self.assertEqual(output, "720\n")

    def test_times(self):
        stack, output = self.execute("0 5 [ 1 + ] times .")
        self.assertEqual(stack, [])
        self.assertEqual(output, "5\n")

    def test_if_executes_only_selected_quote(self):
        stack, output = self.execute('true [ "yes" . ] [ no_such_word ] if')
        self.assertEqual(stack, [])
        self.assertEqual(output, "yes\n")

    def test_bool_distinct_from_number(self):
        stack, _ = self.execute("true 1 == false 0 == 1 1.0 == ")
        self.assertEqual(stack, [False, False, True])

    def test_division_and_remainder(self):
        stack, _ = self.execute("7 2 / 7 2 %")
        self.assertEqual(stack, [3.5, 1])

    def test_empty_quote(self):
        stack, _ = self.execute("[ ] call 42")
        self.assertEqual(stack, [42])

    def test_underflow_with_location(self):
        with self.assertRaisesRegex(KnowlieifError, r"1:1: \+: stack underflow"):
            self.execute("+")

    def test_unknown_word_location_from_nested_definition(self):
        with self.assertRaisesRegex(KnowlieifError, r"2:3: unknown word 'oops'"):
            self.execute('[ \n  oops ] call')

    def test_type_errors(self):
        for source in ('1 [ 2 ] [ 3 ] if', 'true 1 +', '1 call',
                       '2 -1 [ 3 ] times', '"x" 3 def',
                       '"if" [ ] def', '[] [] ==', '1 0 /', '1 0 %'):
            with self.subTest(source=source), self.assertRaises(KnowlieifError):
                self.execute(source)

    def test_parse_errors(self):
        for source in ('[ 1', '1 ]', '"broken', '"invalid\\q"'):
            with self.subTest(source=source), self.assertRaises(KnowlieifError):
                parse(source)

    def set_conversion_limit(self):
        if not hasattr(sys, "set_int_max_str_digits"):
            self.skipTest("Python has no decimal conversion limit")
        limit = sys.get_int_max_str_digits()
        self.addCleanup(sys.set_int_max_str_digits, limit)
        sys.set_int_max_str_digits(4300)

    def test_oversized_integer_is_language_error(self):
        self.set_conversion_limit()
        with self.assertRaisesRegex(KnowlieifError, "numeric literal out of range"):
            parse("9" * 5000)

    def test_parse_error_prevents_partial_execution(self):
        interpreter = Knowlieif(io.StringIO())
        with self.assertRaises(KnowlieifError):
            interpreter.run('1 2 + [')
        self.assertEqual(interpreter.stack, [])

    def test_unrenderable_huge_integer_is_language_error(self):
        self.set_conversion_limit()
        with self.assertRaisesRegex(KnowlieifError, "value cannot be displayed"):
            self.execute("2 14 [ dup * ] times .")

    def test_deeply_nested_quotes_are_language_error(self):
        with self.assertRaisesRegex(KnowlieifError, "recursion limit exceeded"):
            parse("[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1))

    def test_redefinition_uses_current_body(self):
        stack, _ = self.execute('"x" [ 1 ] def "y" [ x ] def "x" [ 2 ] def y')
        self.assertEqual(stack, [2])

    def test_exact_quotation_and_execution_depth(self):
        source = "[ " * MAX_DEPTH + "7" + " ] call" * MAX_DEPTH
        stack, _ = self.execute(source)
        self.assertEqual(stack, [7])
        with self.assertRaisesRegex(
            KnowlieifError, rf"1:{MAX_DEPTH + 1}: recursion limit exceeded \(quotation nesting\)"
        ):
            parse("[" * (MAX_DEPTH + 1))

    def test_execution_depth_diagnostic_and_recovery(self):
        machine = Knowlieif(io.StringIO())
        with self.assertRaisesRegex(
            KnowlieifError, r"1:7: recursion limit exceeded \(execution nesting\)"
        ):
            machine.run('"x" [ x ] def x')
        machine.run("42")
        self.assertEqual(machine.stack, [42])

    def test_deep_but_finite_recursion(self):
        stack, _ = self.execute('"walk" [ dup 0 == [ drop ] [ 1 - walk ] if ] def 400 walk 42')
        self.assertEqual(stack, [42])

    def test_line_endings_and_error_positions(self):
        for newline in ("\n", "\r", "\r\n"):
            with self.subTest(newline=repr(newline)):
                stack, output = self.execute(f"# comment{newline}42 .")
                self.assertEqual((stack, output), ([], "42\n"))
                with self.assertRaisesRegex(KnowlieifError, r"2:3: unknown word"):
                    self.execute(f"# comment{newline}  missing")
        with self.assertRaisesRegex(KnowlieifError, r"1:4: invalid string literal"):
            parse('  "\\q"')

    def test_json_strings(self):
        stack, _ = self.execute(r'"\u65e5\u672c" "\ud83d\ude00" "a\tb" "\\" "\""')
        self.assertEqual(stack, ["\u65e5\u672c", "\U0001f600", "a\tb", "\\", '"'])

    def test_integer_arithmetic_and_order(self):
        operations = {
            "+": operator.add, "-": operator.sub, "*": operator.mul,
            "/": operator.truediv, "%": operator.mod, "<": operator.lt,
            "<=": operator.le, ">": operator.gt, ">=": operator.ge,
            "==": operator.eq,
        }
        for a in range(-5, 6):
            for b in range(-5, 6):
                for word, op in operations.items():
                    if b == 0 and word in ("/", "%"):
                        continue
                    with self.subTest(a=a, b=b, word=word):
                        stack, _ = self.execute(f"{a} {b} {word}")
                        expected = op(a, b)
                        self.assertEqual(stack, [expected])
                        self.assertIs(type(stack[0]), type(expected))

    def test_all_builtin_underflows(self):
        counts = {
            "+": 2, "-": 2, "*": 2, "/": 2, "%": 2,
            "<": 2, "<=": 2, ">": 2, ">=": 2, "==": 2,
            "dup": 1, "drop": 1, "swap": 2, ".": 1,
            "def": 2, "call": 1, "if": 3, "times": 2,
            "hex": 1, "utf8": 1, "cat": 2, "len": 1, "u32be": 1, "crc32": 1,
        }
        for word, count in counts.items():
            for present in range(count):
                with self.subTest(word=word, present=present):
                    machine = Knowlieif(io.StringIO())
                    machine.stack = [0] * present
                    with self.assertRaisesRegex(KnowlieifError, "stack underflow"):
                        machine.run(word)
                    self.assertEqual(machine.stack, [0] * present)

    def test_times_late_binding_and_empty_body(self):
        stack, _ = self.execute('"x" [ 1 "x" [ 2 ] def ] def 3 [ x ] times 0 [ missing ] times')
        self.assertEqual(stack, [1, 2, 2])
        stack, _ = self.execute("999999999999999999999999999999 [] times 7")
        self.assertEqual(stack, [7])

    def test_runtime_failure_keeps_prior_effects(self):
        output = io.StringIO()
        machine = Knowlieif(output)
        with self.assertRaisesRegex(KnowlieifError, "unknown word"):
            machine.run('"x" [ 7 ] def 3 . 4 missing 8')
        self.assertEqual(machine.stack, [4])
        self.assertEqual(output.getvalue(), "3\n")
        machine.run("x")
        self.assertEqual(machine.stack, [4, 7])

    def test_output_failure_is_language_error(self):
        output = io.StringIO()
        output.close()
        machine = Knowlieif(output)
        with self.assertRaisesRegex(KnowlieifError, r"1:3: \.: value cannot be displayed"):
            machine.run("1 .")

    def test_parser_fuzz(self):
        rng = random.Random(0xABAC)
        alphabet = '[]"\\# \t\r\n0123456789abc+-\U0001f600'
        for _ in range(3000):
            source = "".join(rng.choice(alphabet) for _ in range(rng.randrange(50)))
            try:
                parse(source)
            except KnowlieifError as error:
                self.assertIsNotNone(error.line)
                self.assertGreaterEqual(error.line, 1)
                self.assertGreaterEqual(error.column, 1)

    def test_cli_invalid_utf8_and_argument_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.knowlieif"
            path.write_bytes(b"\xff")
            result = self.cli(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid UTF-8 source", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")
        result = self.cli("", "-e", "1 .")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("choose a file or -e", result.stderr)

    def cli(self, *args, input=None):
        return subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("knowlieif.py")), *args],
            input=input, text=True, capture_output=True, timeout=10,
        )

    def test_cli_modes(self):
        result = self.cli("-e", "7 2 * .")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "14\n", ""))
        result = self.cli(str(Path(__file__).parent / "examples" / "factorial.knowlieif"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "720\n", ""))
        result = self.cli("-e", "missing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("1:1: unknown word", result.stderr)

    def test_repl_rolls_back_stack_and_definitions_not_output(self):
        result = self.cli(input='"x" [ 7 ] def 5\n"x" [ 99 ] def 2 . drop missing\nx + .\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn("knowlieif> 2\n", result.stdout)
        self.assertIn("knowlieif> 12\n", result.stdout)
        self.assertIn("unknown word 'missing'", result.stderr)

    def test_repl_interrupt_recovers(self):
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        original = Knowlieif.run
        def run(machine, source):
            if source == "interrupt":
                machine.stack.clear()
                machine.words.clear()
                raise KeyboardInterrupt
            original(machine, source)
        with patch.object(Knowlieif, "run", run), patch(
            "builtins.input", side_effect=['"x" [ 7 ] def 5', "interrupt", "x + .", EOFError]
        ), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main([]), 0)
        self.assertIn("\n12\n", out.getvalue())
        self.assertIn("interrupted", err.getvalue())

    def test_value_rendering_and_equality(self):
        stack, output = self.execute('true . false . [ 1 [ 2 ] ] . "a" "a" == "a" "b" == "1" 1 ==')
        self.assertEqual(output, "true\nfalse\n<quote:2>\n")
        self.assertEqual(stack, [True, False, False])
        self.assertTrue(all(type(value) is bool for value in stack))

    def test_extra_type_and_numeric_failures(self):
        for source, message in (
            ('"a" 1 <', "expected two numbers"),
            ('"1" [] def', "expected an identifier string"),
            ('true [] 1 if', "expected two quotations"),
            ('1 2 times', "expected a quotation"),
            ('9' * 400 + ' 1.0 +', "int too large to convert to float"),
        ):
            with self.subTest(source=source), self.assertRaisesRegex(KnowlieifError, message):
                self.execute(source)

    def test_direct_word_and_nested_repetition(self):
        machine = Knowlieif(io.StringIO())
        machine.run('"x" [ 1 + ] def 0 3 [ 2 [ x ] times ] times')
        machine.word("x")
        machine.word("dup")
        self.assertEqual(machine.stack, [7, 7])

    def test_invalid_definition_preserves_existing_word(self):
        machine = Knowlieif(io.StringIO())
        machine.run('"x" [ 7 ] def')
        with self.assertRaisesRegex(KnowlieifError, "expected a quotation"):
            machine.run('"x" 1 def')
        machine.run("x")
        self.assertEqual(machine.stack, [7])

    def test_cli_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.knowlieif"
            result = self.cli(str(path))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)

    def test_byte_values_and_composition(self):
        stack, output = self.execute(
            r'"00 ff" hex "\u00e9" utf8 cat dup len . dup . "00ffc3a9" hex =='
        )
        self.assertEqual(stack, [True])
        self.assertEqual(output, "4\n00ffc3a9\n")
        stack, _ = self.execute('"" hex "" utf8 cat dup len swap crc32')
        self.assertEqual(stack, [0, 0])

    def test_binary_types_and_invalid_inputs(self):
        for source in ('1 hex', 'false utf8', r'"\ud800" utf8', '"0" hex',
                       '"zz" hex', '"0 0" hex', '"" hex "x" cat',
                       '"x" len', '1 crc32', '-1 u32be', '4294967296 u32be',
                       'true u32be', '1.0 u32be'):
            with self.subTest(source=source), self.assertRaises(KnowlieifError):
                self.execute(source)

    def test_binary_words_reserved(self):
        for name in ("hex", "utf8", "cat", "len", "u32be", "crc32"):
            with self.subTest(name=name), self.assertRaisesRegex(KnowlieifError, "reserved word"):
                self.execute(f'"{name}" [] def')

    def test_unsigned_encoding_and_crc(self):
        rng = random.Random(748)
        for n in [0, 1, 255, 256, 2**32 - 1] + [rng.randrange(2**32) for _ in range(256)]:
            stack, _ = self.execute(f"{n} u32be")
            self.assertEqual(stack, [struct.pack(">I", n)])
        stack, _ = self.execute('"123456789" utf8 crc32')
        self.assertEqual(stack, [0xCBF43926])

    def test_steps_exact_budget_and_recovery(self):
        machine = Knowlieif(io.StringIO(), max_steps=3)
        machine.run("1 2 +")
        self.assertEqual(machine.stack, [3])
        with self.assertRaisesRegex(KnowlieifError, "step limit exceeded"):
            machine.run("4 5 6 7")
        self.assertEqual(machine.stack, [3, 4, 5, 6])
        machine.run("drop drop drop")
        self.assertEqual(machine.stack, [3])
        with self.assertRaisesRegex(KnowlieifError, "step limit exceeded"):
            machine.run("9 [ 1 ] times")
        self.assertEqual(machine.stack, [3])
        machine = Knowlieif(io.StringIO(), max_steps=0)
        machine.run("")
        with self.assertRaisesRegex(KnowlieifError, "step limit exceeded"):
            machine.run("1")

    def test_steps_counts_nested_and_direct_calls(self):
        machine = Knowlieif(io.StringIO(), max_steps=6)
        machine.run('"x" [ 1 ] def')
        machine.run("x x x")
        self.assertEqual(machine.stack, [1, 1, 1])
        with self.assertRaisesRegex(KnowlieifError, "step limit exceeded"):
            machine.run("x x x x")
        machine = Knowlieif(io.StringIO(), max_steps=1)
        with self.assertRaisesRegex(KnowlieifError, "step limit exceeded"):
            machine.run("[ 1 ] call")
        machine = Knowlieif(io.StringIO(), max_steps=3)
        machine.run('"x" [ 1 ] def')
        machine.word("x")
        self.assertEqual(machine.stack, [1])
        machine.max_steps = 1
        with self.assertRaisesRegex(KnowlieifError, "step limit exceeded"):
            machine.word("x")
        self.assertEqual(machine.stack, [1])
        machine = Knowlieif(io.StringIO(), max_steps=5)
        machine.run("2 [ 1 ] times")
        self.assertEqual(machine.stack, [1, 1])
        for n in (-1, True, 1.5):
            with self.subTest(n=n), self.assertRaises(ValueError):
                Knowlieif(max_steps=n)

    def binary_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", str(Path(__file__).with_name("knowlieif.py")), "--binary", *args],
            capture_output=True, timeout=10,
        )

    def test_binary_cli_success_and_separation(self):
        result = self.binary_cli("-e", '"notice" . "00 ff 0a" hex')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"\x00\xff\n", b"notice\n"))
        result = self.binary_cli("-e", '"" hex')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))

    def test_binary_cli_failure_never_outputs_payload(self):
        for source in ('', '1', '"x" utf8 1', '"ff" hex missing', '"x" . "ff" hex missing'):
            with self.subTest(source=source):
                result = self.binary_cli("-e", source)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertNotIn(b"Traceback", result.stderr)
        result = self.binary_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    def test_step_cli(self):
        result = self.cli("--steps", "3", "-e", "1 2 + .")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("step limit exceeded", result.stderr)
        result = self.cli("--steps", "-1", "-e", "")
        self.assertEqual(result.returncode, 2)
        result = self.cli("--steps", "3", input="1 2 +\n4 5 + .\n.\n")
        self.assertEqual(result.returncode, 0)
        self.assertIn("knowlieif> 3\n", result.stdout)
        self.assertIn("step limit exceeded", result.stderr)

    def test_byte_composition_properties(self):
        rng = random.Random(0xB17E)
        for _ in range(256):
            a, b, c = (bytes(rng.randrange(256) for _ in range(rng.randrange(32)))
                       for _ in range(3))
            x, y, z = (f'"{value.hex()}" hex' for value in (a, b, c))
            left, _ = self.execute(f"{x} {y} cat {z} cat")
            right, _ = self.execute(f"{x} {y} {z} cat cat")
            self.assertEqual(left, right)
            self.assertEqual(left, [a + b + c])
            stack, _ = self.execute(f"{x} {y} cat dup len swap crc32")
            self.assertEqual(stack, [len(a + b), zlib.crc32(a + b)])

    def test_pixel_example_against_independent_crc(self):
        result = self.binary_cli(str(Path(__file__).parent / "examples" / "pixel.knowlieif"))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = result.stdout
        self.assertEqual(len(data), 67)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")

        def crc_reference(payload):
            crc = 0xffffffff
            for byte in payload:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
            return crc ^ 0xffffffff

        pos, chunks = 8, []
        while pos < len(data):
            length = struct.unpack_from(">I", data, pos)[0]
            kind = data[pos + 4:pos + 8]
            payload = data[pos + 8:pos + 8 + length]
            crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
            self.assertEqual(crc, crc_reference(kind + payload))
            chunks.append((kind, payload))
            pos += 12 + length
        self.assertEqual(pos, len(data))
        self.assertEqual([kind for kind, _ in chunks], [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[0][1]), (1, 1, 8, 0, 0, 0, 0))
        self.assertEqual(zlib.decompress(chunks[1][1]), b"\x00\x00")
        self.assertEqual(chunks[2][1], b"")
        # Deliberately corrupt the final CRC using the language itself.
        source = (Path(__file__).parent / "examples" / "pixel.knowlieif").read_text()
        bad = self.binary_cli("-e", source.replace(
            '"IEND" utf8 chunk cat',
            '"IEND" utf8 dup len 4 - u32be swap dup crc32 1 + 4294967296 % u32be cat cat cat',
        ))
        self.assertEqual(bad.returncode, 0, bad.stderr)
        self.assertEqual(data[:-4], bad.stdout[:-4])
        self.assertNotEqual(data[-4:], bad.stdout[-4:])


if __name__ == "__main__":
    unittest.main()
