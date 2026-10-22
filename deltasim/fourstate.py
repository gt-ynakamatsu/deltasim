"""deltasim/fourstate.py — §18.7 4値論理コア

IEEE 1800 の 4 値論理 (0/1/x/z) をビット単位で扱う。

内部表現: (aval, bval, width) — IEEE 1800 Annex A の2マスク形式
  bit i:  aval[i]=0, bval[i]=0 → 0
  bit i:  aval[i]=1, bval[i]=0 → 1
  bit i:  aval[i]=0, bval[i]=1 → z
  bit i:  aval[i]=1, bval[i]=1 → x

性能: bval==0 の場合（純2値）は既存の int パスと同等。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union


# ---------------------------------------------------------------------------
# 型定義
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FourState:
    """ビット単位の 0/1/x/z を持つ N ビット値。

    純2値（bval=0）の場合は int に変換可能。
    """
    aval: int   # value bits
    bval: int   # x/z mask bits (0=pure 0/1, 1=x or z)
    width: int

    def __post_init__(self):
        mask = _mask(self.width)
        object.__setattr__(self, "aval", self.aval & mask)
        object.__setattr__(self, "bval", self.bval & mask)

    # ----------------------------------------------------------------
    # 変換
    # ----------------------------------------------------------------

    def is_pure_2state(self) -> bool:
        return self.bval == 0

    def to_int_or_none(self) -> Optional[int]:
        """純2値なら int、x/z を含むなら None を返す。"""
        if self.bval == 0:
            return self.aval
        return None

    def to_display_str(self, radix: str = "b") -> str:
        """%b/%h/%d 形式の文字列を返す。"""
        if radix == "b":
            return _fmt_bin(self.aval, self.bval, self.width)
        if radix in ("h", "x"):
            return _fmt_hex(self.aval, self.bval, self.width)
        # %d: 全ビットが確定なら10進、そうでなければ x
        if self.bval == 0:
            return str(self.aval)
        return "x"

    def to_4state_str(self) -> str:
        """4 値文字列（result.py 互換）を返す。"""
        return _fmt_bin(self.aval, self.bval, self.width)

    # ----------------------------------------------------------------
    # IEEE 1800 演算
    # ----------------------------------------------------------------

    def bitwise_not(self) -> "FourState":
        """~ 演算: x/z はそのまま x に"""
        mask = _mask(self.width)
        new_aval = (~self.aval) & mask
        # x/z → x (bval unchanged)
        return FourState(new_aval | self.bval, self.bval, self.width)

    def bitwise_and(self, other: "FourState") -> "FourState":
        """& 演算: 0&x=0, 1&x=x, x&x=x"""
        mask = _mask(max(self.width, other.width))
        w = max(self.width, other.width)
        a1, b1 = self.aval, self.bval
        a2, b2 = other.aval, other.bval
        # Known-zero bits: ~a1 & ~b1 (self=0) OR ~a2 & ~b2 (other=0)
        known_zero = ((~a1 & ~b1) | (~a2 & ~b2)) & mask
        new_bval = ((b1 | b2) & ~known_zero) & mask
        new_aval = (a1 & a2) & mask
        return FourState(new_aval, new_bval, w)

    def bitwise_or(self, other: "FourState") -> "FourState":
        """| 演算: 1|x=1, 0|x=x, x|x=x"""
        mask = _mask(max(self.width, other.width))
        w = max(self.width, other.width)
        a1, b1 = self.aval, self.bval
        a2, b2 = other.aval, other.bval
        # Known-one bits
        known_one = ((a1 & ~b1) | (a2 & ~b2)) & mask
        new_bval = ((b1 | b2) & ~known_one) & mask
        new_aval = ((a1 | a2) | known_one) & mask
        return FourState(new_aval, new_bval, w)

    def bitwise_xor(self, other: "FourState") -> "FourState":
        """^ 演算: x/z → x"""
        mask = _mask(max(self.width, other.width))
        w = max(self.width, other.width)
        a1, b1 = self.aval, self.bval
        a2, b2 = other.aval, other.bval
        new_bval = (b1 | b2) & mask
        new_aval = (a1 ^ a2) & mask
        return FourState(new_aval, new_bval, w)

    def eq_4state(self, other: "FourState") -> Optional[bool]:
        """== 演算: x/z が含まれると結果は x (None を返す)。"""
        if self.bval != 0 or other.bval != 0:
            return None  # x
        return self.aval == other.aval

    def ceq(self, other: "FourState") -> bool:
        """=== 演算: x/z も含めてビット完全一致。常に 0/1。"""
        return self.aval == other.aval and self.bval == other.bval

    def wildcard_eq(self, pattern: "FourState") -> bool:
        """==? 演算: pattern 側の x/z/? ビットはワイルドカード。"""
        # Don't care bits in pattern: where bval=1 (x or z) OR explicitly '?'
        dont_care = pattern.bval
        compare_mask = _mask(max(self.width, pattern.width)) & ~dont_care
        return (self.aval & compare_mask) == (pattern.aval & compare_mask) and \
               (self.bval & compare_mask) == 0  # self must be 0/1 at compared bits

    def arith_result_x(self) -> bool:
        """算術演算で x/z が含まれれば True（結果は全ビット x）。"""
        return self.bval != 0

    # ----------------------------------------------------------------
    # Python dunder
    # ----------------------------------------------------------------

    def __int__(self):
        if self.bval != 0:
            raise ValueError(f"Cannot convert 4-state value with x/z to int: {self}")
        return self.aval

    def __bool__(self):
        """if 文の条件評価: x/z は False（SV 準拠）。"""
        if self.bval != 0:
            return False
        return bool(self.aval)

    def __eq__(self, other):
        if isinstance(other, FourState):
            return self.aval == other.aval and self.bval == other.bval and self.width == other.width
        if isinstance(other, int):
            return self.bval == 0 and self.aval == other
        return NotImplemented

    def __hash__(self):
        return hash((self.aval, self.bval, self.width))

    def __repr__(self):
        return f"FourState({self.width}'b{self.to_4state_str()})"


# ---------------------------------------------------------------------------
# ファクトリ関数
# ---------------------------------------------------------------------------

def from_int(v: int, width: int) -> FourState:
    """純2値 int から FourState を作る。"""
    return FourState(v & _mask(width), 0, width)


def from_x(width: int) -> FourState:
    """全ビット x の FourState を作る。"""
    m = _mask(width)
    return FourState(m, m, width)


def from_z(width: int) -> FourState:
    """全ビット z の FourState を作る。"""
    return FourState(0, _mask(width), width)


def parse_literal(s: str) -> FourState:
    """SV 数値リテラル文字列をパースして FourState を返す。

    §18.7.1: 基数つき形式のみ4値として扱う。
    - sized:        "4'b1x0z"、"8'hxx"、"4'bxxxx"
    - unsized base: "'bx"、"'hz"、"'b1010"
    - fill:         "'x"、"'z"、"'0"、"'1"

    bare 文字列 ("x"、"z" など) はエラー。
    """
    s = s.strip().replace("_", "")

    # Fill: 'x 'z '0 '1
    m_fill = re.fullmatch(r"'([01xXzZ])", s)
    if m_fill:
        ch = m_fill.group(1).lower()
        if ch == "x":
            return from_x(1)
        if ch == "z":
            return from_z(1)
        return from_int(int(ch), 1)

    # Sized or unsized base: [N]'[s]B digits
    m = re.fullmatch(
        r"([0-9]*)'([sS]?)([bBoOdDhH])([0-9a-fA-FxXzZ?]+)", s
    )
    if m:
        width_str = m.group(1)
        width = int(width_str) if width_str else None
        base_ch = m.group(3).lower()
        digits = m.group(4)
        base = {"b": 2, "o": 8, "d": 10, "h": 16}[base_ch]

        if base == 10:
            if width is None:
                return from_int(int(digits), 32)
            return from_int(int(digits) & _mask(width), width)

        # infer width from digit count if not specified
        bits_per_digit = {2: 1, 8: 3, 16: 4}[base]
        eff_width = width if width is not None else len(digits) * bits_per_digit
        aval, bval = _parse_xz_digits(digits, base, eff_width)
        return FourState(aval, bval, eff_width)

    # Plain integer (no base prefix, no tick)
    try:
        return from_int(int(s), 32)
    except ValueError:
        raise ValueError(
            f"SV リテラルとして不正な文字列: {s!r}。"
            "基数つきリテラル（\"4'b1x0z\"、\"'bx\"、\"'z\" 等）を使ってください。"
        )


def coerce(v, width: int) -> FourState:
    """int, None, FourState → FourState に変換。None は全ビット x。"""
    if isinstance(v, FourState):
        return FourState(v.aval & _mask(width), v.bval & _mask(width), width)
    if v is None:
        return from_x(width)
    # Z_VALUE (legacy)
    try:
        from deltasim.result import Z_VALUE as _Z
        if v is _Z:
            return from_z(width)
    except ImportError:
        pass
    return from_int(int(v) & _mask(width), width)


def to_legacy(fs: FourState) -> object:
    """FourState → 既存の int/None/Z_VALUE 表現に変換（後方互換）。"""
    if fs.bval == 0:
        return fs.aval
    if fs.bval == _mask(fs.width) and fs.aval == 0:
        # All z
        from deltasim.result import Z_VALUE
        return Z_VALUE
    if (fs.aval & ~fs.bval) == 0 and (fs.bval != 0):
        # Mixed x/z → x (None)
        return None
    return None  # any x/z → x


# ---------------------------------------------------------------------------
# パース・表示内部関数
# ---------------------------------------------------------------------------

def _mask(width: int) -> int:
    if width <= 0:
        return 0
    return (1 << width) - 1


def _parse_xz_digits(digits: str, base: int, width: int):
    """x/z を含むデジット列を (aval, bval) のペアに変換する。"""
    bits_per_digit = {2: 1, 8: 3, 16: 4}[base]
    aval = 0
    bval = 0
    for d in digits:
        d_lower = d.lower()
        aval <<= bits_per_digit
        bval <<= bits_per_digit
        if d_lower == "x":
            bits = _mask(bits_per_digit)
            aval |= bits   # aval=1 for x
            bval |= bits   # bval=1 for x
        elif d_lower == "z" or d == "?":
            bits = _mask(bits_per_digit)
            bval |= bits   # aval=0, bval=1 for z
        else:
            aval |= int(d, base)
    # Truncate to width
    mask = _mask(width)
    return aval & mask, bval & mask


def _fmt_bin(aval: int, bval: int, width: int) -> str:
    result = []
    for i in range(width - 1, -1, -1):
        a = (aval >> i) & 1
        b = (bval >> i) & 1
        if b == 0:
            result.append("1" if a else "0")
        elif a == 1:
            result.append("x")
        else:
            result.append("z")
    return "".join(result) or "0"


def _fmt_hex(aval: int, bval: int, width: int) -> str:
    nibbles = max(1, (width + 3) // 4)
    result = []
    for i in range(nibbles - 1, -1, -1):
        a = (aval >> (i * 4)) & 0xF
        b = (bval >> (i * 4)) & 0xF
        if b == 0:
            result.append(f"{a:X}")
        elif b == 0xF and a == 0xF:
            result.append("X")  # all x nibble
        elif b == 0xF and a == 0:
            result.append("Z")  # all z nibble
        else:
            result.append("X")  # mixed → X
    return "".join(result) or "0"


# ---------------------------------------------------------------------------
# YAML 期待値パーサ
# ---------------------------------------------------------------------------

def parse_expected_value(v) -> Optional["FourStatePattern"]:
    """YAML の expected/check 値を 4 値パターンとしてパース。

    - int → 純2値（FourStatePattern）
    - str "4'b1x0z" など → FourStatePattern
    - str "4'b10??" → ワイルドカードパターン
    Returns None if not parseable as 4-state.
    """
    if isinstance(v, int):
        return FourStatePattern(from_int(v, 64), False)
    if not isinstance(v, str):
        return None
    v = v.strip()
    # Check for ? (wildcard) in pattern
    has_wildcard = "?" in v
    try:
        fs = parse_literal(v)
        return FourStatePattern(fs, has_wildcard)
    except Exception:
        return None


@dataclass(frozen=True)
class FourStatePattern:
    """期待値パターン: 実値と比較する際のルールを持つ。"""
    value: FourState
    has_wildcard: bool  # True なら ==? 相当（? ビットはワイルドカード）

    def matches(self, actual: FourState) -> bool:
        """実際の信号値が期待値パターンに一致するか判定する。

        - has_wildcard=False: === 相当（x/z もビット一致）
        - has_wildcard=True: ==? 相当（パターン側の x/z/? はワイルドカード）
        """
        if self.has_wildcard:
            return actual.wildcard_eq(self.value)
        return actual.ceq(self.value)

    def to_display_str(self) -> str:
        return self.value.to_4state_str()
