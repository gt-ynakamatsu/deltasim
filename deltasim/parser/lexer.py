"""Lexer (tokenizer) for the synthesizable SystemVerilog subset.

Converts a source string into a flat list of Token objects,
stripping comments and whitespace.  Line/column tracking is included
for error messages.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

@dataclass
class Token:
    type: str    # see TOKEN_TYPES below
    value: str
    line: int
    col: int
    # §18.9: プリプロセッサ通過後の元ファイル・元行（ソースマップ由来）
    source_file: str = ""
    source_line: int = 0   # 0 = line と同じ（未セット）

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, {self.line}:{self.col})"


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

KEYWORDS = {
    "module", "endmodule",
    "input", "output", "inout",
    "logic", "wire", "reg", "bit", "int", "integer", "byte", "shortint", "longint",
    "parameter", "localparam",
    "assign",
    "always", "always_ff", "always_comb", "always_latch",
    "initial",
    "begin", "end",
    "if", "else",
    "case", "casex", "casez", "endcase",
    "for", "while", "repeat", "forever", "do",
    "posedge", "negedge",
    "or",
    "default",
    "signed", "unsigned",
    "generate", "endgenerate", "genvar",
    "function", "endfunction",
    "task", "endtask",
    "return",
    "typedef", "struct", "union", "enum", "packed",
    "package", "endpackage", "import",
    "interface", "endinterface", "modport",
    "void", "automatic", "static",
    "supply0", "supply1", "tri", "wand", "wor",
}

# ---------------------------------------------------------------------------
# Token pattern table  (order matters – longer / more-specific patterns first)
# ---------------------------------------------------------------------------

_PATTERNS = [
    # Comments (consumed, not emitted)
    ("COMMENT_BLOCK", r"/\*[\s\S]*?\*/"),
    ("COMMENT_LINE",  r"//[^\n]*"),
    # Whitespace (consumed)
    ("WHITESPACE",    r"[ \t\r\n]+"),
    # Sized number literals:  8'hFF  4'b1010  8'd100  8'o177
    ("NUMBER",        r"[0-9]+'[sS]?[bBoOdDhH][0-9a-fA-FxXzZwW_?]+"),
    # Unsized base literals: 'b1010 'hFF 'o77 'd5
    ("NUMBER",        r"'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ_?]+"),
    # Fill literals: '0 '1 'x 'z
    ("FILL",          r"'[01xXzZ]"),
    # Plain decimal integer
    ("NUMBER",        r"[0-9]+(?:_[0-9]+)*"),
    # String literal
    ("STRING",        r'"[^"]*"'),
    # System task/function identifiers: $display $clog2 etc.
    ("SYSID",         r'\$[a-zA-Z_][a-zA-Z0-9_$]*'),
    # Regular identifiers (and escaped identifiers handled separately)
    ("ID",            r'[a-zA-Z_][a-zA-Z0-9_$]*'),
    # Multi-character operators (longest-match first)
    ("OP",            r'===|!==|==|!=|<<=|>>=|<<<|>>>|<<|>>|<=|>=|&&|\|\||\^~|~\^|~&|~\||\+\+|--|->'),
    # Single-character operators and punctuation
    ("OP",            r'[+\-*/%&|^~!<>=?:,;.#@()\[\]{}\'`]'),
]

# Compile into one master regex with named groups
_MASTER_RE = re.compile(
    "|".join(f"(?P<G{i}_{t}>{p})" for i, (t, p) in enumerate(_PATTERNS)),
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# LexError
# ---------------------------------------------------------------------------

class LexError(Exception):
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def tokenize(
    source: str,
    filename: str = "<string>",
    source_map: "Optional[dict]" = None,
) -> List[Token]:
    """Tokenize *source* and return a list of Tokens (no WHITESPACE/COMMENT).
    
    CRLF (\\r\\n) and bare CR (\\r) are normalised to LF before tokenising,
    which ensures Windows-edited files are handled correctly.
    §16: Unclosed block comments and unterminated string literals raise LexError.
    §18.9: source_map が与えられた場合、各トークンに元ファイル・元行を設定する。
    """
    from typing import Optional as _Opt  # local import to avoid circular

    # Normalise line endings (Windows CRLF / old Mac CR)
    source = source.replace("\r\n", "\n").replace("\r", "\n")

    # §16: Pre-scan for unclosed /* comments and unterminated strings
    _check_unclosed(source, filename)

    tokens: List[Token] = []
    line = 1
    line_start = 0
    pos = 0

    while pos < len(source):
        m = _MASTER_RE.match(source, pos)
        if m is None:
            col = pos - line_start + 1
            # ソースマップから元ファイル・元行を取得
            orig_file, orig_line = _resolve_loc(filename, line, source_map)
            raise LexError(
                f"{orig_file}:{orig_line}:{col}: unexpected character {source[pos]!r}"
            )

        # Identify which group matched
        matched_type: str = ""
        for key, val in m.groupdict().items():
            if val is not None:
                # group name is like G3_NUMBER  →  type = NUMBER
                matched_type = key.split("_", 1)[1]
                break

        text = m.group()
        col = pos - line_start + 1

        if matched_type not in ("WHITESPACE", "COMMENT_BLOCK", "COMMENT_LINE"):
            # Promote ID to KEYWORD if applicable
            tok_type = matched_type
            tok_value = text
            if matched_type == "ID" and text in KEYWORDS:
                tok_type = "KW"
            # §18.9: ソースマップから元位置を取得
            orig_file, orig_line = _resolve_loc(filename, line, source_map)
            tok = Token(tok_type, tok_value, line, col,
                        source_file=orig_file, source_line=orig_line)
            tokens.append(tok)

        # Update line / position tracking
        newlines = text.count("\n")
        if newlines:
            line += newlines
            line_start = pos + text.rfind("\n") + 1
        pos = m.end()

    orig_file, orig_line = _resolve_loc(filename, line, source_map)
    tokens.append(Token("EOF", "EOF", line, 0,
                         source_file=orig_file, source_line=orig_line))
    return tokens


def _resolve_loc(filename: str, line: int, source_map) -> "tuple":
    """ソースマップから (original_file, original_line) を取得する。"""
    if source_map is not None:
        loc = source_map.get(line)
        if loc is not None:
            return loc.file, loc.line
    return filename, line


def _check_unclosed(source: str, filename: str) -> None:
    """Detect unclosed /* block comments and unterminated string literals.

    Both constructs prevent the main regex from matching and would silently
    produce garbled tokens.  Raising LexError here gives a clear E001.
    """
    line = 1
    i = 0
    n = len(source)
    while i < n:
        c = source[i]
        if c == '/' and i + 1 < n and source[i + 1] == '*':
            start_line = line
            i += 2
            closed = False
            while i < n:
                if source[i] == '\n':
                    line += 1
                elif source[i] == '*' and i + 1 < n and source[i + 1] == '/':
                    i += 2
                    closed = True
                    break
                i += 1
            if not closed:
                raise LexError(
                    f"{filename}:{start_line}: 閉じていないブロックコメント /* が EOF に達しました"
                )
        elif c == '/' and i + 1 < n and source[i + 1] == '/':
            while i < n and source[i] != '\n':
                i += 1
        elif c == '"':
            start_line = line
            i += 1
            closed = False
            while i < n:
                if source[i] == '\n':
                    line += 1
                if source[i] == '"':
                    i += 1
                    closed = True
                    break
                if source[i] == '\\':
                    i += 1
                i += 1
            if not closed:
                raise LexError(
                    f"{filename}:{start_line}: 閉じていない文字列リテラルが EOF に達しました"
                )
        else:
            if c == '\n':
                line += 1
            i += 1
