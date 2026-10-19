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

def tokenize(source: str, filename: str = "<string>") -> List[Token]:
    """Tokenize *source* and return a list of Tokens (no WHITESPACE/COMMENT).
    
    CRLF (\\r\\n) and bare CR (\\r) are normalised to LF before tokenising,
    which ensures Windows-edited files are handled correctly.
    """
    # Normalise line endings (Windows CRLF / old Mac CR)
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    tokens: List[Token] = []
    line = 1
    line_start = 0
    pos = 0

    while pos < len(source):
        m = _MASTER_RE.match(source, pos)
        if m is None:
            col = pos - line_start + 1
            raise LexError(f"{filename}:{line}:{col}: unexpected character {source[pos]!r}")

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
            tokens.append(Token(tok_type, tok_value, line, col))

        # Update line / position tracking
        newlines = text.count("\n")
        if newlines:
            line += newlines
            line_start = pos + text.rfind("\n") + 1
        pos = m.end()

    tokens.append(Token("EOF", "", line, 0))
    return tokens
