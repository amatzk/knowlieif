# knowlieif

A small stack-based interpreted language written for no particular reason. I just needed a break. It has no intended purpose, although it can do a few things.

## Installation

Python 3.10 or newer is required. Clone the repository and change into its directory. There are no third-party dependencies or build steps.

```sh
git clone https://github.com/amatzk/knowlieif.git
cd knowlieif
```

## Usage

```sh
python knowlieif.py examples/factorial.knowlieif
python knowlieif.py -e '7 2 * .'
python knowlieif.py
```

Run a UTF-8 source file, evaluate a string, or start the REPL. The REPL evaluates one line at a time and retains the stack and word definitions. Use a file for multiline programs. A failed or interrupted REPL evaluation restores the stack and definitions, but cannot undo output. Ctrl-D or Ctrl-C at the prompt exits.

```text
# Square a number.
"square" [ dup * ] def
7 square .  # 49
```

Values are pushed onto the stack; words operate on it. A quotation, `[ ... ]`, is a value containing unevaluated code. Whitespace separates words, brackets and quoted strings also delimit tokens, and `#` starts a line comment outside strings.

| Words or syntax | Meaning |
| --- | --- |
| Integers, decimals, JSON-style strings, `true`, `false` | Literal values |
| `dup`, `drop`, `swap` | Duplicate, remove, exchange |
| `.` | Pop and print (byte strings use lowercase hexadecimal) |
| `+`, `-`, `*`, `/`, `%` | Arithmetic |
| `==`, `<`, `<=`, `>`, `>=` | Equality and numeric comparisons |
| `[ body ] call` | Evaluate a quotation |
| `condition [ then ] [ else ] if` | Evaluate one branch; requires a boolean |
| `count [ body ] times` | Repeat a quotation; requires a nonnegative integer |
| `"name" [ body ] def` | Define a word; names resolve at execution time |
| `hex` | String to bytes (two hexadecimal digits per byte; ASCII whitespace between bytes is allowed) |
| `utf8` | String to UTF-8 bytes |
| `cat` | Concatenate two byte strings |
| `len` | Byte string length |
| `u32be` | Unsigned 32-bit integer to four big-endian bytes |
| `crc32` | Byte string to unsigned CRC-32 checksum |

For no particularly good reason, it can also produce a tiny PNG:

```sh
python knowlieif.py --binary examples/pixel.knowlieif > pixel.png
```

In binary mode, the final stack must contain exactly one byte string. Its raw bytes are written to stdout only after successful evaluation; `.` prints to stderr instead. Binary mode requires a file or `-e`.

Numbers follow Python's integer and floating-point rules, including its optional decimal conversion limit. Booleans are distinct from numbers; quotations cannot be compared. Quotation nesting and execution nesting are each limited to 1,024 levels. `--steps N` optionally limits evaluated items (literals and words, including those inside quotations) per run or REPL line. This is not a memory, time, or security sandbox. Run trusted code only.
