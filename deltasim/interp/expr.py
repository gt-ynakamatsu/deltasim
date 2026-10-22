"""deltasim/interp/expr.py — SV式サブセットの自前評価器

§18.4: Python の eval/exec を使わず、自前のパーサで評価する。
対応演算子: ビット演算、比較、論理、シフト、加減乗、三項、括弧、
            ビット選択 [i]、範囲選択 [hi:lo]、単項 ~!/-.
リテラル: 10進整数、4'b..., 8'hFF など SV 形式、ベアシンボル（信号参照）。
x/z 値: deltasim 内部では None(x) / Z_VALUE(z) として扱う。
無限再帰防止: 最大再帰深さを MAX_EXPR_DEPTH で制限する。
"""
from __future__ import annotations
import re
from typing import Any, Optional, Dict

MAX_EXPR_DEPTH = 128   # 式の再帰深さ上限（無限ループ防止）

# ---------------------------------------------------------------------------
# 例外
# ---------------------------------------------------------------------------

class ExprError(Exception):
    """式評価エラー"""


# ---------------------------------------------------------------------------
# トークナイザ
# ---------------------------------------------------------------------------

_TOK_PATTERNS = [
    ("WS",      r"[ \t\r\n]+"),
    # §18.7.1: 基数つきリテラルのみ4値として扱う。
    # 優先順位: sized → unsized base → fill。bare x/z は常に識別子 (ID)。
    # sized:        4'b1010  8'hFF  4'b1x0z
    # unsized base: 'b1010  'bx  'hz
    # fill:         'x  'z  '0  '1
    ("SV_NUM",  r"[0-9]*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZwW_?]+|'[01xXzZ]"),
    ("HEX",     r"0[xX][0-9a-fA-F]+"),        # C-style 0xFF hex literal
    ("DEC",     r"[0-9]+(?:_[0-9]+)*"),
    ("ID",      r"[a-zA-Z_][a-zA-Z0-9_$]*"),
    # multi-char ops first
    ("OP",      r"===|!==|==|!=|<=|>=|<<<|>>>|<<|>>|&&|\|\||\^~|~\^|~&|~\||[+\-*/%&|^~!<>=?:,\[\]()]"),
]
_TOK_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOK_PATTERNS))


def _tokenize(src: str):
    tokens = []
    pos = 0
    n = len(src)
    while pos < n:
        m = _TOK_RE.match(src, pos)
        if m is None:
            raise ExprError(f"式の不正な文字: {src[pos]!r} (位置 {pos})")
        kind = m.lastgroup
        val  = m.group()
        if kind != "WS":
            tokens.append((kind, val))
        pos = m.end()
    return tokens


# ---------------------------------------------------------------------------
# リテラル解析
# ---------------------------------------------------------------------------

def _parse_sv_number(tok: str):
    """SV 数値リテラル (sized/unsized base/fill) を (value, width) に変換。

    §18.7.1: 基数つき形式のみ4値として扱う。
    - x ビットを含む → value=None (x)
    - z のみ（x なし）→ value=Z_VALUE (z)
    - 純2値 → value=int
    """
    # Fill: '0 '1 'x 'z
    m_fill = re.fullmatch(r"'([01xXzZ])", tok)
    if m_fill:
        ch = m_fill.group(1).lower()
        if ch == "x":
            return (None, 1)
        if ch == "z":
            return (_z(), 1)
        return (int(ch), 1)

    # Sized (4'b...) または unsized ('b...)
    m = re.fullmatch(
        r"([0-9]*)'([sS]?)([bBoOdDhH])([0-9a-fA-FxXzZwW_?]+)", tok)
    if not m:
        return (int(tok), None)
    width_str = m.group(1)
    width = int(width_str) if width_str else None
    signed = bool(m.group(2))
    base_ch = m.group(3).lower()
    digits = m.group(4).replace("_", "").replace("?", "z")
    base = {"b": 2, "o": 8, "d": 10, "h": 16}[base_ch]
    has_x = any(c in digits.lower() for c in "xw")
    has_z = any(c == "z" for c in digits.lower())
    if has_x:
        return (None, width)   # x ビットあり → x
    if has_z:
        return (_z(), width)   # 純 z → z
    val = int(digits, base)
    if width is not None:
        val = val & ((1 << width) - 1)
        if signed and (val >> (width - 1)):
            val -= (1 << width)
    return (val, width)


# ---------------------------------------------------------------------------
# パーサ / 評価器（Pratt 式）
# ---------------------------------------------------------------------------

_UNARY_OPS  = {"~", "!", "-", "+", "&", "|", "^", "~&", "~|", "~^", "^~"}
_BINOP_PREC = {
    "||": 2, "&&": 3,
    "|": 4, "^": 5, "^~": 5, "~^": 5, "&": 6,
    "==": 7, "!=": 7, "===": 7, "!==": 7,
    "<": 8, ">": 8, "<=": 8, ">=": 8,
    "<<": 9, ">>": 9, "<<<": 9, ">>>": 9,
    "+": 10, "-": 10,
    "*": 11, "/": 11, "%": 11,
}

_Z_VALUE = None   # placeholder; evaluated lazily


def _z():
    from deltasim.result import Z_VALUE
    return Z_VALUE


class _Parser:
    def __init__(self, tokens, env: Dict[str, Any], depth: int = 0):
        self._toks = tokens
        self._pos  = 0
        self._env  = env
        self._depth = depth

    def _peek(self) -> Optional[tuple]:
        if self._pos < len(self._toks):
            return self._toks[self._pos]
        return None

    def _peek_val(self) -> str:
        t = self._peek()
        return t[1] if t else ""

    def _consume(self) -> tuple:
        t = self._toks[self._pos]
        self._pos += 1
        return t

    def _expect(self, val: str):
        t = self._peek()
        if t is None or t[1] != val:
            raise ExprError(f"'{val}' を期待しましたが {t!r} です")
        self._pos += 1

    def parse(self) -> Any:
        val = self._expr(0)
        if self._peek() is not None:
            raise ExprError(f"式の途中に余分なトークン: {self._peek()!r}")
        return val

    def _expr(self, min_prec: int) -> Any:
        if self._depth > MAX_EXPR_DEPTH:
            raise ExprError("式の入れ子が深すぎます")
        self._depth += 1
        try:
            left = self._unary()
            while True:
                t = self._peek()
                if t is None:
                    break
                op = t[1]
                # ternary
                if op == "?" and min_prec <= 1:
                    self._consume()
                    then_v = self._expr(0)
                    self._expect(":")
                    else_v = self._expr(1)
                    left = then_v if left else else_v
                    continue
                prec = _BINOP_PREC.get(op)
                if prec is None or prec < min_prec:
                    break
                self._consume()
                right = self._expr(prec + 1)
                left = _apply_binop(op, left, right)
            return left
        finally:
            self._depth -= 1

    def _unary(self) -> Any:
        t = self._peek()
        if t is None:
            raise ExprError("式が途中で終わっています")
        op = t[1]
        if op in _UNARY_OPS:
            self._consume()
            operand = self._unary()
            return _apply_unary(op, operand)
        return self._postfix(self._primary())

    def _primary(self) -> Any:
        t = self._peek()
        if t is None:
            raise ExprError("項を期待しましたが入力が終わっています")
        kind, val = t
        if kind == "SV_NUM":
            self._consume()
            v, _ = _parse_sv_number(val)
            return v
        if kind == "HEX":
            self._consume()
            return int(val, 16)
        if kind == "DEC":
            self._consume()
            return int(val)
        if kind == "ID":
            self._consume()
            # §18.7.1: bare x/z は常に識別子。4値リテラルは基数つき形式のみ。
            if val in self._env:
                return self._env[val]
            if val == "1":
                return 1
            if val == "0":
                return 0
            raise ExprError(f"未定義のシンボル: {val!r}")
        if val == "(":
            self._consume()
            v = self._expr(0)
            self._expect(")")
            return v
        raise ExprError(f"式に不正なトークン: {t!r}")

    def _postfix(self, base: Any) -> Any:
        while True:
            t = self._peek()
            if t is None or t[1] != "[":
                break
            self._consume()
            idx = self._expr(0)
            t2 = self._peek()
            if t2 is not None and t2[1] == ":":
                self._consume()
                lo = self._expr(0)
                self._expect("]")
                base = _part_select(base, idx, lo)
            else:
                self._expect("]")
                base = _bit_select(base, idx)
        return base


# ---------------------------------------------------------------------------
# 演算ヘルパー
# ---------------------------------------------------------------------------

def _is_x(v: Any) -> bool:
    return v is None


def _is_z(v: Any) -> bool:
    from deltasim.result import Z_VALUE
    return v is Z_VALUE


def _to_bool(v: Any) -> bool:
    if _is_x(v) or _is_z(v):
        return False
    return bool(v)


def _apply_binop(op: str, a: Any, b: Any) -> Any:
    """2項演算。x/z を含む場合は None を返す（基本ルール）。"""
    if op in ("===", "!=="):
        # 厳密等価: x/z も含めて比較
        eq = (a is None and b is None) or (a == b)
        return int(eq) if op == "===" else int(not eq)
    # x/z が含まれる場合は x
    if _is_x(a) or _is_z(a) or _is_x(b) or _is_z(b):
        return None
    try:
        if op == "+":  return a + b
        if op == "-":  return a - b
        if op == "*":  return a * b
        if op == "/":  return (a // b) if b != 0 else None
        if op == "%":  return (a % b)  if b != 0 else None
        if op == "&":  return a & b
        if op == "|":  return a | b
        if op == "^":  return a ^ b
        if op in ("^~", "~^"): return ~(a ^ b)
        if op == "<<":  return a << (b & 0x3F)
        if op == ">>":  return (a & 0xFFFF_FFFF_FFFF_FFFF) >> (b & 0x3F)
        if op == "<<<": return a << (b & 0x3F)
        if op == ">>>": return a >> (b & 0x3F)
        if op == "&&": return int(bool(a) and bool(b))
        if op == "||": return int(bool(a) or  bool(b))
        if op == "==": return int(a == b)
        if op == "!=": return int(a != b)
        if op == "<":  return int(a < b)
        if op == ">":  return int(a > b)
        if op == "<=": return int(a <= b)
        if op == ">=": return int(a >= b)
    except Exception:
        return None
    return None


def _apply_unary(op: str, a: Any) -> Any:
    if _is_x(a) or _is_z(a):
        return None
    if op == "~":   return ~a
    if op == "!":   return int(not bool(a))
    if op == "-":   return -a
    if op == "+":   return a
    if op == "&":   return _reduce_and(a)
    if op == "|":   return _reduce_or(a)
    if op == "^":   return _reduce_xor(a)
    if op in ("~&", "~|", "~^"):
        inner = _apply_unary(op[1:], a)
        return _apply_unary("~", inner) if inner is not None else None
    return None


def _reduce_and(v: int) -> int:
    while v > 0xFFFF:
        v = (v & 0xFFFF) & (v >> 16)
    v = v & 0xFF; v = (v & 0xF) & (v >> 4); v = (v & 3) & (v >> 2); v = (v & 1) & (v >> 1)
    return v & 1


def _reduce_or(v: int) -> int:
    return int(v != 0)


def _reduce_xor(v: int) -> int:
    x = 0
    while v:
        x ^= v & 1
        v >>= 1
    return x


def _bit_select(v: Any, idx: Any) -> Any:
    if _is_x(v) or _is_z(v) or _is_x(idx) or _is_z(idx):
        return None
    return (v >> idx) & 1


def _part_select(v: Any, hi: Any, lo: Any) -> Any:
    if _is_x(v) or _is_z(v) or _is_x(hi) or _is_z(hi) or _is_x(lo) or _is_z(lo):
        return None
    width = hi - lo + 1
    return (v >> lo) & ((1 << width) - 1)


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def eval_expr(src: str, env: Optional[Dict[str, Any]] = None) -> Any:
    """SV 式文字列を評価して値を返す。

    env は {信号名: 値} の辞書（省略可）。
    x/z を含む結果は None / Z_VALUE を返す。
    構文エラー時は ExprError を送出する。
    """
    env = env or {}
    try:
        toks = _tokenize(src.strip())
    except ExprError:
        raise
    if not toks:
        raise ExprError("空の式です")
    p = _Parser(toks, env)
    return p.parse()


def eval_bool(src: str, env: Optional[Dict[str, Any]] = None) -> bool:
    """式を評価して bool に変換する（x/z は False）。"""
    v = eval_expr(src, env)
    return _to_bool(v)
