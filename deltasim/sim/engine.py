"""Simulation engine: event-driven simulator with delta-cycle semantics.

Architecture
------------
Each simulation step (half clock period) follows the IEEE-1800 four-region
schedule, simplified to what's needed for synthesizable code:

  Active region
  ─────────────
  1. Apply clock / reset edge  →  evaluate triggered SeqProcs (NBA queued)
  2. Apply NBA queue (delta 0)
  3. Re-evaluate AssignProcs + CombProcs until quiescent (Δ1, Δ2, …)

  Initial blocks (M2)
  ───────────────────
  Python-generator coroutines run concurrently with the clock-driven loop.
  Time delays (#n) are scheduled in a min-heap; @(edge) waits block until
  the matching edge is observed.

Non-blocking assignment (NBA) semantics
----------------------------------------
All RHS values are captured *before* any LHS update, then applied in one
batch.  This correctly models concurrent register updates, e.g.:

  a <= b;   // reads old b
  b <= a;   // reads old a  →  swap!

Delta cycle limit
-----------------
If combinational logic does not converge within MAX_DELTA iterations,
a SimError is raised (indicates a combinational loop in the RTL).

Pre/post-posedge hooks (M2)
-----------------------------
``run()`` accepts optional callbacks for stimulus injection and result
checking at each clock cycle:

  pre_posedge_hook(cycle_1indexed, signals)   – apply inputs
  post_posedge_hook(cycle_1indexed, signals)  – check outputs
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from deltasim.elab.elaborator import ElabDesign, SignalInfo, SeqProc, CombProc, AssignProc
from deltasim.sim.context import EvalContext, StructFieldEntry, _EMPTY_CTX
from deltasim.parser.ast_nodes import (
    SensitivityList, SensitivityItem,
    Block, IfStmt, CaseStmt, CaseItem,
    BlockingAssign, NonBlockingAssign, NullStmt, SysTaskStmt,
    TimeDelay, ForeverStmt, RepeatStmt, WaitEventStmt, ForStmt, WhileStmt,
    NumberLiteral, FillLiteral, StringLiteral, Identifier,
    BinOp, UnaryOp, Conditional, BitSelect, PartSelect, Concat, Replicate,
    SysFunc, MemberAccess,
)


MAX_DELTA = 10_000  # default delta-cycle convergence limit (§0.2.1 E_DELTA_LIMIT)
MAX_FOREVER_TICKS = 1_000_000  # default loop iteration limit (§0.2.1 E_LOOP_LIMIT)


# ---------------------------------------------------------------------------
# SimError — generic simulation error
# ---------------------------------------------------------------------------

class SimError(Exception):
    pass


class BreakpointHit(Exception):
    """Raised after a time step completes when a line breakpoint fired (§18.1.1).

    Deferred approach: the executor records hits without raising immediately.
    After the full time step (posedge + NBA + negedge + VCD) the hit is raised.
    This ensures NBA, negedge, and VCD recording are never skipped.
    """
    def __init__(self, bp_id: int, source_file: str, source_line: int, time_ns: int):
        self.bp_id       = bp_id
        self.source_file = source_file
        self.source_line = source_line
        self.time_ns     = time_ns
        super().__init__(f"bp [{bp_id}] {source_file}:{source_line} @ {time_ns}ns")


class DeltaLimitError(SimError):
    """Raised when delta-cycle convergence exceeds max_delta (E_DELTA_LIMIT)."""
    def __init__(self, time_ns: int, signal_hint: str = "", max_delta: int = MAX_DELTA):
        self.time_ns = time_ns
        self.signal_hint = signal_hint
        self.max_delta = max_delta
        hint = f"（変化し続けている信号: {signal_hint}）" if signal_hint else ""
        super().__init__(
            f"E_DELTA_LIMIT: {time_ns}ns でデルタサイクルが {max_delta} 回を超えました{hint}"
        )


class LoopLimitError(SimError):
    """Raised when a loop body executes more than max_loop times (E_LOOP_LIMIT)."""
    def __init__(self, time_ns: int, loop_kind: str = "loop", location: str = "",
                 max_loop: int = MAX_FOREVER_TICKS):
        self.time_ns = time_ns
        self.loop_kind = loop_kind
        self.location = location
        self.max_loop = max_loop
        loc = f" ({location})" if location else ""
        super().__init__(
            f"E_LOOP_LIMIT: {time_ns}ns の {loop_kind}{loc} が {max_loop} 回を超えました"
        )


# ---------------------------------------------------------------------------
# Expression evaluator
# ---------------------------------------------------------------------------

def eval_expr(
    expr,
    signals: Dict[str, int],
    widths: Dict[str, int],
    params: Dict[str, int],
    ctx: Optional[EvalContext] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """Evaluate *expr* and return (value, width).

    *value* is ``None`` when the result is unknown (X/Z).
    *width* is ``None`` for unsized literals.
    Signed signals are sign-extended when read (M4 signed arithmetic).
    """
    if isinstance(expr, NumberLiteral):
        # §18.7: x/z リテラルを FourState として返す
        if expr.xz_mask:
            from deltasim.fourstate import FourState as _FS
            w = expr.width if expr.width is not None else 32
            return (_FS(expr.value, expr.xz_mask, w), expr.width)
        return (expr.value, expr.width)

    if isinstance(expr, FillLiteral):
        if expr.fill in ("x", "X"):
            return (None, None)          # X
        if expr.fill in ("z", "Z"):
            from deltasim.result import Z_VALUE
            return (Z_VALUE, None)        # Z distinct from X (§12.4)
        val = 0 if expr.fill in ("0",) else -1
        return (val, None)

    if isinstance(expr, StringLiteral):
        return (0, None)

    if isinstance(expr, Identifier):
        name = expr.name
        if name in params:
            return (params[name], 32)
        val = signals.get(name, 0)
        w = widths.get(name, 1)
        if val is None:
            return (None, w)
        from deltasim.result import Z_VALUE
        if val is Z_VALUE:
            return (Z_VALUE, w)
        # §18.7: FourState 信号値をそのまま返す
        try:
            from deltasim.fourstate import FourState as _FS
            if isinstance(val, _FS):
                return (val, w)
        except ImportError:
            pass
        # M4: sign-extend signed signals
        if ctx and ctx.signs.get(name, False) and w > 0 and val >= (1 << (w - 1)):
            val = val - (1 << w)
        return (val, w)

    # M4: struct packed field access  (expr.field)
    if isinstance(expr, MemberAccess):
        return _eval_member(expr, signals, widths, params, ctx)

    if isinstance(expr, BinOp):
        return _eval_binop(expr, signals, widths, params, ctx)

    if isinstance(expr, UnaryOp):
        return _eval_unaryop(expr, signals, widths, params, ctx)

    if isinstance(expr, Conditional):
        cond_v, _ = eval_expr(expr.cond, signals, widths, params, ctx)
        if _has_xz(cond_v):
            # §18.7 ?: x 条件: per-bit merge（一致ビットは値、不一致は x）
            then_v, then_w = eval_expr(expr.then_expr, signals, widths, params, ctx)
            else_v, else_w = eval_expr(expr.else_expr, signals, widths, params, ctx)
            w = max(_eff_width(then_w), _eff_width(else_w))
            from deltasim.fourstate import FourState, coerce
            from deltasim.fourstate import _mask as _fsmask
            then_fs = coerce(then_v, w)
            else_fs = coerce(else_v, w)
            mask = _fsmask(w)
            # ビットが一致し、かつ両方 0/1 なら確定。それ以外は x
            pure_mask = ~(then_fs.bval | else_fs.bval) & mask
            agree_mask = pure_mask & ~(then_fs.aval ^ else_fs.aval)
            new_aval = then_fs.aval & agree_mask
            new_bval = ~agree_mask & mask
            result_fs = FourState(new_aval, new_bval, w)
            if result_fs.bval == 0:
                return (result_fs.aval, w)
            return (result_fs, w)
        if cond_v:
            return eval_expr(expr.then_expr, signals, widths, params, ctx)
        else:
            return eval_expr(expr.else_expr, signals, widths, params, ctx)

    if isinstance(expr, BitSelect):
        # M4: check if base is an unpacked array
        if (ctx and isinstance(expr.base, Identifier)
                and expr.base.name in ctx.arrays):
            return _eval_array_element(expr, signals, widths, params, ctx)
        base_v, _ = eval_expr(expr.base, signals, widths, params, ctx)
        idx_v, _ = eval_expr(expr.index, signals, widths, params, ctx)
        if idx_v is None:
            return (None, 1)
        if base_v is None:
            return (None, 1)    # all-x base → 1-bit x
        # §18.7: FourState base → extract specific bit
        try:
            from deltasim.fourstate import FourState as _FS
            from deltasim.result import Z_VALUE as _ZV
            if base_v is _ZV:
                return (None, 1)   # z → x for bit-select
            if isinstance(base_v, _FS):
                aval_bit = (base_v.aval >> idx_v) & 1
                bval_bit = (base_v.bval >> idx_v) & 1
                if bval_bit:
                    return (None, 1)   # x or z bit → x
                return (aval_bit, 1)
        except ImportError:
            pass
        return ((base_v >> idx_v) & 1, 1)

    if isinstance(expr, PartSelect):
        base_v, _ = eval_expr(expr.base, signals, widths, params, ctx)
        hi_v, _ = eval_expr(expr.hi, signals, widths, params, ctx)
        lo_v, _ = eval_expr(expr.lo, signals, widths, params, ctx)
        if hi_v is None or lo_v is None:
            return (None, 1)
        w = hi_v - lo_v + 1
        if base_v is None:
            # all-x base: return w-bit all-x
            return (None, w)
        # §18.7: FourState base → extract per-bit slice
        try:
            from deltasim.fourstate import FourState as _FS
            from deltasim.result import Z_VALUE as _ZV
            if base_v is _ZV:
                # all-z base: return w-bit z
                from deltasim.fourstate import from_z
                return (from_z(w), w)
            if isinstance(base_v, _FS):
                aval = (base_v.aval >> lo_v) & ((1 << w) - 1)
                bval = (base_v.bval >> lo_v) & ((1 << w) - 1)
                result_fs = _FS(aval, bval, w)
                if result_fs.bval == 0:
                    return (result_fs.aval, w)
                return (result_fs, w)
        except ImportError:
            pass
        mask = (1 << w) - 1
        return ((base_v >> lo_v) & mask, w)

    if isinstance(expr, Concat):
        return _eval_concat(expr, signals, widths, params, ctx)

    if isinstance(expr, Replicate):
        cnt_v, _ = eval_expr(expr.count, signals, widths, params, ctx)
        val_v, val_w = eval_expr(expr.value, signals, widths, params, ctx)
        if cnt_v is None or val_v is None:
            return (None, None)
        if val_w is None:
            val_w = 32
        result = 0
        for _ in range(cnt_v):
            result = (result << val_w) | (val_v & ((1 << val_w) - 1))
        return (result, cnt_v * val_w)

    if isinstance(expr, SysFunc):
        return _eval_sysfunc(expr, signals, widths, params, ctx)

    raise SimError(f"Unknown expression type: {type(expr).__name__}")


def _eff_width(w: Optional[int]) -> int:
    return w if w is not None else 32


def _to_x(v):
    """Normalize Z to X (None) for arithmetic purposes."""
    from deltasim.result import Z_VALUE
    return None if isinstance(v, Z_VALUE.__class__) else v


# ---------------------------------------------------------------------------
# §18.7: 4値演算ヘルパー
# ---------------------------------------------------------------------------

def _has_xz(v) -> bool:
    """True ならオペランドに x/z ビットが含まれる（FourState 演算が必要）。"""
    if v is None:
        return True
    from deltasim.result import Z_VALUE
    if v is Z_VALUE:
        return True
    try:
        from deltasim.fourstate import FourState as _FS
        if isinstance(v, _FS):
            return v.bval != 0
    except ImportError:
        pass
    return False


def _to_fs(v, w: int):
    """int/None/Z_VALUE/FourState を FourState に変換する。"""
    from deltasim.fourstate import FourState, from_int, from_x, from_z, coerce
    return coerce(v, w)


def _fs_to_val(fs):
    """FourState を int/None/Z_VALUE に変換する（後方互換レガシー形式）。"""
    from deltasim.fourstate import to_legacy
    return to_legacy(fs)


def _apply_binop_fs(op: str, lv, rv, lw: int, rw: int):
    """FourState を使った 4 値バイナリ演算。(value, width) を返す。"""
    from deltasim.fourstate import FourState, coerce, from_int
    w = max(lw, rw)
    lfs = coerce(lv, w)
    rfs = coerce(rv, w)

    # 比較演算（=== は常に 0/1、== は x/z で None を返す）
    if op == "===":
        return (int(lfs.ceq(rfs)), 1)
    if op == "!==":
        return (int(not lfs.ceq(rfs)), 1)
    if op == "==":
        eq = lfs.eq_4state(rfs)
        return (None if eq is None else int(eq), 1)
    if op == "!=":
        eq = lfs.eq_4state(rfs)
        return (None if eq is None else int(not eq), 1)

    # ビット演算: IEEE 1800 per-bit ルール（0&x=0, 1|x=1 等）
    # 結果に x/z ビットが残る場合は FourState のまま返す（幅マスクは _write_signal が行う）
    if op == "&":
        res = lfs.bitwise_and(rfs)
        return (res.aval if res.bval == 0 else res, w)
    if op == "|":
        res = lfs.bitwise_or(rfs)
        return (res.aval if res.bval == 0 else res, w)
    if op == "^":
        res = lfs.bitwise_xor(rfs)
        return (res.aval if res.bval == 0 else res, w)
    if op in ("^~", "~^"):
        from deltasim.fourstate import FourState
        res_xor = lfs.bitwise_xor(rfs)
        mask = (1 << w) - 1
        not_aval = (~res_xor.aval) & mask
        res = FourState(not_aval | res_xor.bval, res_xor.bval, w)
        return (res.aval if res.bval == 0 else res, w)

    # 算術・シフト・論理比較: x/z があれば全ビット x に伝搬
    if lfs.bval != 0 or rfs.bval != 0:
        return (None, w)

    # 純2値（高速パス）
    lv_int, rv_int = lfs.aval, rfs.aval
    mask = (1 << w) - 1

    if op == "<":   return (int(lv_int < rv_int), 1)
    if op == ">":   return (int(lv_int > rv_int), 1)
    if op == "<=":  return (int(lv_int <= rv_int), 1)
    if op == ">=":  return (int(lv_int >= rv_int), 1)
    if op == "&&":  return (int(bool(lv_int) and bool(rv_int)), 1)
    if op == "||":  return (int(bool(lv_int) or bool(rv_int)), 1)
    if op == "+":   return ((lv_int + rv_int) & mask, w)
    if op == "-":   return ((lv_int - rv_int) & mask, w)
    if op == "*":   return ((lv_int * rv_int) & mask, w)
    if op == "/":   return ((lv_int // rv_int if rv_int != 0 else 0), w)
    if op == "%":   return ((lv_int % rv_int if rv_int != 0 else 0), w)
    if op == "<<":  return ((lv_int << rv_int) & mask, w)
    if op == ">>":  return (lv_int >> rv_int, w)
    if op == "<<<": return ((lv_int << rv_int) & mask, w)
    if op == ">>>": return (lv_int >> rv_int, w)
    if op == "**":  return ((lv_int ** rv_int) & mask, w)
    return (None, w)


def _case_match(expr_v, pat_v, w: int, kind: str) -> bool:
    """§18.7: case/casez/casex 比較。

    - case:  === 相当（x/z も一致しないと False）
    - casez: pattern 側の z/? ビットはワイルドカード
    - casex: pattern 側の x/z ビットはワイルドカード
    """
    from deltasim.fourstate import FourState, coerce
    ef = coerce(expr_v, w)
    pf = coerce(pat_v, w)
    if kind == "case":
        return ef.ceq(pf)
    # casez/casex: don't-care mask from pattern
    from deltasim.fourstate import _mask as _fm
    if kind == "casez":
        dont_care = pf.bval & ~pf.aval  # z bits in pattern (aval=0, bval=1)
    else:  # casex
        dont_care = pf.bval             # both x and z bits in pattern
    compare_mask = _fm(w) & ~dont_care
    # Expression bits must be pure 0/1 at compared positions
    if ef.bval & compare_mask:
        return False  # expression has x/z where we must compare
    return (ef.aval & compare_mask) == (pf.aval & compare_mask)


def _apply_unaryop_fs(op: str, v, w: int):
    """FourState を使った 4 値単項演算。(value, width) を返す。"""
    from deltasim.fourstate import FourState, coerce
    fs = coerce(v, w)
    mask = (1 << w) - 1

    if fs.bval != 0:
        # x/z ビットあり
        if op == "~":
            res = fs.bitwise_not()
            return (res.aval if res.bval == 0 else res, w)
        return (None, w)  # 論理/算術演算は x に

    v_int = fs.aval
    if op == "!":    return (int(not v_int), 1)
    if op == "~":    return (~v_int & mask, w)
    if op == "-":    return (-v_int & mask, w)
    if op == "+":    return (v_int, w)
    if op == "&":    return (int(v_int & mask == mask), 1)
    if op == "~&":   return (int(v_int & mask != mask), 1)
    if op == "|":    return (int(v_int != 0), 1)
    if op == "~|":   return (int(v_int == 0), 1)
    if op == "^":
        p = 0
        t = v_int & mask
        while t:
            p ^= t & 1
            t >>= 1
        return (p, 1)
    if op in ("^~", "~^"):
        p = 0
        t = v_int & mask
        while t:
            p ^= t & 1
            t >>= 1
        return (p ^ 1, 1)
    return (None, w)


def _eval_binop(expr, signals, widths, params, ctx=None) -> Tuple[Optional[int], Optional[int]]:
    lv_raw, lw = eval_expr(expr.left, signals, widths, params, ctx)
    rv_raw, rw = eval_expr(expr.right, signals, widths, params, ctx)
    op = expr.op
    w = max(_eff_width(lw), _eff_width(rw))
    mask = (1 << w) - 1

    # §18.7: どちらかに x/z があれば FourState パスへ（per-bit 精度）
    if _has_xz(lv_raw) or _has_xz(rv_raw):
        return _apply_binop_fs(op, lv_raw, rv_raw, _eff_width(lw), _eff_width(rw))

    # 純2値高速パス（既存ロジックを維持）
    lv = _to_x(lv_raw)
    rv = _to_x(rv_raw)

    if op == "==":   return (int(lv == rv), 1)
    if op == "!=":   return (int(lv != rv), 1)
    if op == "===":  return (int(lv == rv), 1)
    if op == "!==":  return (int(lv != rv), 1)
    if op == "<":    return (int(lv < rv), 1)
    if op == ">":    return (int(lv > rv), 1)
    if op == "<=":   return (int(lv <= rv), 1)
    if op == ">=":   return (int(lv >= rv), 1)
    if op == "&&":   return (int(bool(lv) and bool(rv)), 1)
    if op == "||":   return (int(bool(lv) or bool(rv)), 1)
    if op == "+":    return (lv + rv, w)
    if op == "-":    return (lv - rv, w)
    if op == "*":    return (lv * rv, w)
    if op == "/":    return ((lv // rv if rv != 0 else 0), w)
    if op == "%":    return ((lv % rv if rv != 0 else 0), w)
    if op == "&":    return (lv & rv, w)
    if op == "|":    return (lv | rv, w)
    if op == "^":    return (lv ^ rv, w)
    if op in ("^~", "~^"):  return (~(lv ^ rv) & mask, w)
    if op == "<<":   return (lv << rv, w)
    if op == ">>":   return ((lv >> rv), w)
    if op == "<<<":  return (lv << rv, w)
    if op == ">>>":
        wl = _eff_width(lw)
        sign = (lv >> (wl - 1)) & 1
        if sign:
            return ((-1 << wl | lv) >> rv, w)
        return (lv >> rv, w)
    if op == "**":   return (lv ** rv, w)
    raise SimError(f"Unknown binary operator: {op!r}")


def _eval_unaryop(expr, signals, widths, params, ctx=None) -> Tuple[Optional[int], Optional[int]]:
    v, w = eval_expr(expr.operand, signals, widths, params, ctx)
    op = expr.op
    ew = _eff_width(w)

    # §18.7: x/z があれば FourState パスへ
    if _has_xz(v):
        return _apply_unaryop_fs(op, v, ew)

    # 純2値高速パス
    mask = (1 << ew) - 1

    if op == "!":    return (int(not v), 1)
    if op == "~":    return (~v & mask, ew)
    if op == "-":    return (-v, ew)
    if op == "+":    return (v, ew)
    if op == "&":    return (int(v & mask == mask), 1)
    if op == "~&":   return (int(v & mask != mask), 1)
    if op == "|":    return (int(v != 0), 1)
    if op == "~|":   return (int(v == 0), 1)
    if op == "^":
        p = 0
        t = v & mask
        while t:
            p ^= t & 1
            t >>= 1
        return (p, 1)
    if op in ("^~", "~^"):
        p = 0
        t = v & mask
        while t:
            p ^= t & 1
            t >>= 1
        return (p ^ 1, 1)
    raise SimError(f"Unknown unary operator: {op!r}")


def _eval_concat(expr, signals, widths, params, ctx=None) -> Tuple[Optional[int], int]:
    result = 0
    total_w = 0
    has_xz = False
    parts_vals = []
    for part in expr.parts:
        v, w = eval_expr(part, signals, widths, params, ctx)
        pw = _eff_width(w)
        parts_vals.append((v, pw))
        if _has_xz(v):
            has_xz = True
        total_w += pw

    if not has_xz:
        for v, pw in parts_vals:
            result = (result << pw) | (v & ((1 << pw) - 1))
        return (result, total_w)

    # §18.7: x/z ビットを含む concat → FourState で組み立て
    from deltasim.fourstate import FourState, coerce
    aval = 0
    bval = 0
    for v, pw in parts_vals:
        fs = coerce(v, pw)
        aval = (aval << pw) | fs.aval
        bval = (bval << pw) | fs.bval
    result_fs = FourState(aval, bval, total_w)
    if result_fs.bval == 0:
        return (result_fs.aval, total_w)
    return (result_fs, total_w)


def _eval_sysfunc(expr, signals, widths, params, ctx=None) -> Tuple[int, Optional[int]]:
    name = expr.name
    if name == "clog2":
        v, _ = eval_expr(expr.args[0], signals, widths, params, ctx)
        return (max(1, math.ceil(math.log2(max(v, 2)))), 32)
    if name in ("signed", "unsigned"):
        return eval_expr(expr.args[0], signals, widths, params, ctx)
    return (0, 32)


def eval_val(expr, signals, widths, params, ctx=None) -> Optional[int]:
    v, _ = eval_expr(expr, signals, widths, params, ctx)
    return v


# M4: struct field access helper
def _eval_member(
    expr, signals, widths, params, ctx
) -> Tuple[Optional[int], Optional[int]]:
    """Evaluate `base.field` for struct packed types."""
    base_name = expr.base.name if isinstance(expr.base, Identifier) else None
    if ctx and base_name and base_name in ctx.signal_types:
        type_name = ctx.signal_types[base_name]
        for fe in ctx.struct_fields.get(type_name, []):
            if fe.name == expr.field:
                base_v, _ = eval_expr(expr.base, signals, widths, params, ctx)
                if base_v is None:
                    return (None, fe.width)
                mask = (1 << fe.width) - 1
                return ((base_v >> fe.lo) & mask, fe.width)
    # Fallback: evaluate base (field not found)
    return eval_expr(expr.base, signals, widths, params, ctx)


# M4: unpacked array element access helper
def _eval_array_element(
    expr, signals, widths, params, ctx
) -> Tuple[Optional[int], Optional[int]]:
    """Evaluate `array[idx]` for unpacked arrays stored in ctx.arrays."""
    arr_name = expr.base.name
    idx_v, _ = eval_expr(expr.index, signals, widths, params, ctx)
    elem_w = ctx.arrays_elem_width.get(arr_name, 1)
    if idx_v is None:
        return (None, elem_w)
    arr = ctx.arrays.get(arr_name, [])
    idx_i = int(idx_v)
    if 0 <= idx_i < len(arr):
        return (arr[idx_i], elem_w)
    return (0, elem_w)


# ---------------------------------------------------------------------------
# $display / $write format string handling
# ---------------------------------------------------------------------------

def _fmt_val_display(val, spec: str) -> str:
    """§18.7: $display の 1 引数を spec に従ってフォーマット（x/z 対応）。"""
    from deltasim.result import Z_VALUE
    from deltasim.fourstate import FourState as _FS

    # x/z 判定
    is_x = val is None
    is_z = val is Z_VALUE
    is_fs = isinstance(val, _FS)

    if spec in ('d', 'D'):
        if is_x or is_z:
            return "x"
        if is_fs:
            return val.to_display_str("d")
        return str(val)
    if spec in ('h', 'H', 'x', 'X'):
        if is_x:
            return "x"
        if is_z:
            return "z"
        if is_fs:
            return val.to_display_str("h").lower()
        return hex(val)[2:]
    if spec in ('b', 'B'):
        if is_x:
            return "x"
        if is_z:
            return "z"
        if is_fs:
            return val.to_display_str("b")
        return bin(val)[2:]
    if spec in ('o', 'O'):
        if is_x or is_z:
            return "x"
        if is_fs:
            return oct(val.aval)[2:] if val.bval == 0 else "x"
        return oct(val)[2:]
    if spec == 's':
        return str(val) if not (is_x or is_z) else "x"
    if spec == 't':
        return str(val)
    return str(val)


def _format_display(fmt: str, args: List[int]) -> str:
    """Very small subset of SV format specifiers."""
    arg_idx = 0
    out = []
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c == '%' and i + 1 < len(fmt):
            spec = fmt[i + 1]
            val = args[arg_idx] if arg_idx < len(args) else 0
            arg_idx += 1
            if spec == '0':
                # %0d / %0h etc.
                if i + 2 < len(fmt):
                    spec2 = fmt[i + 2]
                    out.append(_fmt_val_display(val, spec2))
                    i += 3
                    continue
                out.append(str(val))
            else:
                out.append(_fmt_val_display(val, spec))
            i += 2
        elif c == '\\':
            if i + 1 < len(fmt):
                nc = fmt[i + 1]
                if nc == 'n':
                    out.append('\n')
                elif nc == 't':
                    out.append('\t')
                else:
                    out.append(nc)
                i += 2
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    # Append remaining args
    while arg_idx < len(args):
        out.append(' ' + str(args[arg_idx]))
        arg_idx += 1
    return ''.join(out)


# ---------------------------------------------------------------------------
# Statement executor
# ---------------------------------------------------------------------------

class _Executor:
    """Executes a statement tree, collecting NBA assignments."""

    def __init__(
        self,
        signals: Dict[str, int],
        widths: Dict[str, int],
        params: Dict[str, int],
        ctx: Optional[EvalContext] = None,
        max_loop: int = MAX_FOREVER_TICKS,
        time_ns: int = 0,
        bp_check=None,      # §18: Optional[Callable[[str, int], None]] — file, line
        monitor_state=None, # §18.5: shared dict for $monitor dedup across executors
    ):
        self.signals = dict(signals)
        self.widths = widths
        self.params = params
        self.ctx = ctx or _EMPTY_CTX
        self.max_loop = max_loop
        self.time_ns = time_ns
        self._bp_check = bp_check  # raises BreakpointHit if a bp is set
        # §18.5: shared monitor state (mutable dict) for cross-executor dedup
        self._monitor_state: dict = monitor_state if monitor_state is not None else {}
        # NBA items: (name, val) for signals, (arr_name, idx, val) for arrays
        self.nba_queue: List[tuple] = []
        self.display_log: List[str] = []    # $display/$write/$strobe
        self.monitor_log: List[str] = []    # $monitor (deduplicated via _monitor_state)

    def execute(self, stmt) -> None:
        # §18.1.1: Check line breakpoints — deferred (does not raise, stores hit)
        if self._bp_check is not None:
            sl = getattr(stmt, "source_line", 0)
            sf = getattr(stmt, "source_file", "")
            if sl:
                self._bp_check(sf, sl)  # records hit in engine._deferred_bp_hit, no raise

        if isinstance(stmt, Block):
            for s in stmt.stmts:
                self.execute(s)

        elif isinstance(stmt, IfStmt):
            cond = eval_val(stmt.cond, self.signals, self.widths, self.params, self.ctx)
            if cond:
                self.execute(stmt.then_body)
            elif stmt.else_body is not None:
                self.execute(stmt.else_body)

        elif isinstance(stmt, CaseStmt):
            expr_raw, expr_w = eval_expr(stmt.expr, self.signals, self.widths, self.params, self.ctx)
            matched = False
            default_body = None
            kind = getattr(stmt, "keyword", "case")  # "case"|"casez"|"casex"
            for item in stmt.items:
                if item.exprs is None:
                    default_body = item.body
                else:
                    for e in item.exprs:
                        pat_raw, pat_w = eval_expr(e, self.signals, self.widths, self.params, self.ctx)
                        w = max(_eff_width(expr_w), _eff_width(pat_w))
                        if _case_match(expr_raw, pat_raw, w, kind):
                            self.execute(item.body)
                            matched = True
                            break
                if matched:
                    break
            if not matched and default_body is not None:
                self.execute(default_body)

        elif isinstance(stmt, BlockingAssign):
            v, w = eval_expr(stmt.rhs, self.signals, self.widths, self.params, self.ctx)
            lhs_name = _lhs_name(stmt.lhs)
            _write_signal(self.signals, lhs_name, v, self.widths)

        elif isinstance(stmt, NonBlockingAssign):
            v, _ = eval_expr(stmt.rhs, self.signals, self.widths, self.params, self.ctx)
            lhs = stmt.lhs
            # M4: array element write  (e.g. mem[addr] <= data)
            if (isinstance(lhs, BitSelect) and isinstance(lhs.base, Identifier)
                    and lhs.base.name in self.ctx.arrays):
                arr_name = lhs.base.name
                idx, _ = eval_expr(lhs.index, self.signals, self.widths,
                                   self.params, self.ctx)
                if idx is not None:
                    self.nba_queue.append((arr_name, int(idx), v))
            else:
                lhs_name = _lhs_name(lhs)
                self.nba_queue.append((lhs_name, v))

        elif isinstance(stmt, NullStmt):
            pass

        elif isinstance(stmt, SysTaskStmt):
            self._handle_sys_task(stmt)

        elif isinstance(stmt, ForStmt):
            self.execute(stmt.init)
            iterations = 0
            while True:
                cond = eval_val(stmt.cond, self.signals, self.widths, self.params, self.ctx)
                if not cond:
                    break
                self.execute(stmt.body)
                self.execute(stmt.update)
                iterations += 1
                if iterations > self.max_loop:
                    raise LoopLimitError(self.time_ns, "for loop", max_loop=self.max_loop)

        elif isinstance(stmt, WhileStmt):
            iterations = 0
            while True:
                cond = eval_val(stmt.cond, self.signals, self.widths, self.params, self.ctx)
                if not cond:
                    break
                self.execute(stmt.body)
                iterations += 1
                if iterations > self.max_loop:
                    raise LoopLimitError(self.time_ns, "while loop", max_loop=self.max_loop)

        # TB statements (silently ignored in synthesizable executor)
        elif isinstance(stmt, (TimeDelay, ForeverStmt, RepeatStmt,
                                WaitEventStmt)):
            pass

        else:
            raise SimError(f"Unknown statement: {type(stmt).__name__}")

    def _handle_sys_task(self, stmt: SysTaskStmt) -> None:
        name = stmt.name
        if name in ("finish", "stop"):
            raise _FinishSignal()
        if name in ("display", "write", "strobe"):
            args_vals = [eval_val(a, self.signals, self.widths, self.params, self.ctx)
                         for a in stmt.args]
            if stmt.args and isinstance(stmt.args[0], StringLiteral):
                fmt = stmt.args[0].text
                arg_vals2 = [eval_val(a, self.signals, self.widths, self.params, self.ctx)
                              for a in stmt.args[1:]]
                line = _format_display(fmt, arg_vals2)
            else:
                line = " ".join(str(v) for v in args_vals)
            self.display_log.append(line)
        elif name == "monitor":
            # §18.5: $monitor in always_ff context — treated as display (rare usage)
            # Proper $monitor registration is handled in _exec_initial_gen
            args_vals = [eval_val(a, self.signals, self.widths, self.params, self.ctx)
                         for a in stmt.args]
            if stmt.args and isinstance(stmt.args[0], StringLiteral):
                fmt = stmt.args[0].text
                arg_vals2 = [eval_val(a, self.signals, self.widths, self.params, self.ctx)
                              for a in stmt.args[1:]]
                line = _format_display(fmt, arg_vals2)
            else:
                line = " ".join(str(v) for v in args_vals)
            self.display_log.append(line)


def _engine_monitor_emit(engine, line: str) -> None:
    """§18.5: $monitor 出力（旧 API 互換 — 直接呼び出し時用）。"""
    mon_state = getattr(engine, "_monitor_state", None)
    if mon_state is None:
        engine._monitor_state = mon_state = {}
    cur_key = (engine._time, line)
    if mon_state.get("prev") != cur_key:
        engine._result.log.append(line)
        mon_state["prev"] = cur_key
        if engine.verbose:
            print(line)


def _engine_check_monitor(engine) -> None:
    """§18.5: タイムステップ終了後に $monitor の登録を評価し、変化があれば出力する。

    Verilog 標準: $monitor を一度呼ぶと登録され、以降の各タイムステップ終了時に
    引数のいずれかが変化していれば1行出力する。$time の変化だけでは出力しない。
    """
    reg = getattr(engine, "_monitor_registration", None)
    if reg is None:
        return
    fmt, arg_exprs = reg
    # 現在の引数値を評価
    try:
        cur_vals = tuple(
            eval_val(a, engine.signals, engine._widths, engine._params)
            for a in arg_exprs
        )
    except Exception:
        return

    last_vals = getattr(engine, "_monitor_last_values", None)
    if cur_vals == last_vals:
        return  # 変化なし

    engine._monitor_last_values = cur_vals

    # 出力行を生成
    if fmt is not None:
        line = _format_display(fmt, list(cur_vals))
    else:
        line = " ".join(str(v) if v is not None else "x" for v in cur_vals)

    engine._result.log.append(line)
    if engine.verbose:
        print(line)


class _FinishSignal(Exception):
    """Raised when $finish / $stop is executed."""


def _lhs_name(lhs) -> str:
    if isinstance(lhs, Identifier):
        return lhs.name
    if isinstance(lhs, (BitSelect, PartSelect)):
        return _lhs_name(lhs.base)
    raise SimError(f"Unsupported LHS expression type: {type(lhs).__name__}")


def _collect_lhs_from_stmt(stmt, out: set) -> None:
    """stmt ツリーを再帰的に走査して LHS 信号名を out に追加する。"""
    from deltasim.parser.ast_nodes import (
        Block, IfStmt, CaseStmt, BlockingAssign, NonBlockingAssign,
        ForStmt, WhileStmt, RepeatStmt, ForeverStmt,
    )
    if isinstance(stmt, (BlockingAssign, NonBlockingAssign)):
        try:
            out.add(_lhs_name(stmt.lhs))
        except (SimError, Exception):
            pass
    elif isinstance(stmt, Block):
        for s in stmt.stmts:
            _collect_lhs_from_stmt(s, out)
    elif isinstance(stmt, IfStmt):
        _collect_lhs_from_stmt(stmt.then_body, out)
        if stmt.else_body is not None:
            _collect_lhs_from_stmt(stmt.else_body, out)
    elif isinstance(stmt, CaseStmt):
        for item in stmt.items:
            if item.body is not None:
                _collect_lhs_from_stmt(item.body, out)
    elif isinstance(stmt, (ForStmt, WhileStmt, RepeatStmt, ForeverStmt)):
        body = getattr(stmt, "body", None)
        if body is not None:
            _collect_lhs_from_stmt(body, out)


def _collect_driven_signals(design):
    """プロセス別に書き込まれる信号名を収集する。

    返り値: (seq_driven, any_driven)
      - seq_driven: always_ff など seq_proc が書き込む信号名の集合 → x で初期化
      - any_driven: いずれかのプロセスが書き込む信号名の集合 → 駆動あり信号
    """
    seq_driven: set = set()
    any_driven: set = set()

    for proc in design.assign_procs:
        try:
            name = _lhs_name(proc.lhs)
            any_driven.add(name)
        except Exception:
            pass

    for proc in design.comb_procs:
        _collect_lhs_from_stmt(proc.body, any_driven)

    for proc in design.seq_procs:
        _collect_lhs_from_stmt(proc.body, seq_driven)
        any_driven.update(seq_driven)

    for blk in getattr(design, "initial_blocks", []):
        _collect_lhs_from_stmt(blk, any_driven)

    return seq_driven, any_driven


# ---------------------------------------------------------------------------
# Initial-block coroutine executor
# ---------------------------------------------------------------------------

class _WaitEdge:
    """Yielded by initial-block coroutines to wait for a signal edge."""
    __slots__ = ("edge", "signal")

    def __init__(self, edge: Optional[str], signal: Optional[str]):
        self.edge = edge
        self.signal = signal


def _exec_initial_gen(stmt, engine: "SimEngine"):
    """Generator coroutine for executing an initial-block statement.

    Yields:
      int  – time advance in ns (0 = continue in the same time step)
      _WaitEdge – block until the matching edge is observed
    """
    if isinstance(stmt, Block):
        for s in stmt.stmts:
            yield from _exec_initial_gen(s, engine)

    elif isinstance(stmt, TimeDelay):
        d = eval_val(stmt.delay, engine.signals, engine._widths, engine._params)
        yield int(max(0, d))
        if stmt.body:
            yield from _exec_initial_gen(stmt.body, engine)

    elif isinstance(stmt, ForeverStmt):
        ticks = 0
        while True:
            yield from _exec_initial_gen(stmt.body, engine)
            ticks += 1
            if ticks > engine.max_loop:
                raise LoopLimitError(engine._time, "forever loop", max_loop=engine.max_loop)

    elif isinstance(stmt, RepeatStmt):
        n = eval_val(stmt.count, engine.signals, engine._widths, engine._params)
        for _ in range(int(n)):
            yield from _exec_initial_gen(stmt.body, engine)

    elif isinstance(stmt, WaitEventStmt):
        for item in stmt.sensitivity.items:
            sig_name = (item.signal.name
                        if isinstance(item.signal, Identifier)
                        else None)
            yield _WaitEdge(item.edge, sig_name)
        if stmt.body:
            yield from _exec_initial_gen(stmt.body, engine)

    elif isinstance(stmt, WhileStmt):
        iters = 0
        while eval_val(stmt.cond, engine.signals, engine._widths, engine._params):
            yield from _exec_initial_gen(stmt.body, engine)
            iters += 1
            if iters > engine.max_loop:
                raise LoopLimitError(engine._time, "while loop", max_loop=engine.max_loop)

    elif isinstance(stmt, ForStmt):
        yield from _exec_initial_gen(stmt.init, engine)
        iters = 0
        while eval_val(stmt.cond, engine.signals, engine._widths, engine._params):
            yield from _exec_initial_gen(stmt.body, engine)
            yield from _exec_initial_gen(stmt.update, engine)
            iters += 1
            if iters > engine.max_loop:
                raise LoopLimitError(engine._time, "for loop", max_loop=engine.max_loop)

    elif isinstance(stmt, BlockingAssign):
        old_val = engine.signals.get(_lhs_name(stmt.lhs), 0)
        v, w = eval_expr(stmt.rhs, engine.signals, engine._widths, engine._params)
        name = _lhs_name(stmt.lhs)
        engine._write(name, v)
        new_val = engine.signals[name]
        if old_val != new_val:
            # Trigger edge-sensitive sequential processes (e.g., always_ff)
            nba = engine._collect_nba_for_edge(name, old_val, new_val)
            engine._apply_nba(nba)
            # Notify other initial block runners waiting for this edge
            if engine._initial_scheduler:
                engine._initial_scheduler.notify_edge(
                    name, old_val, new_val, engine._time, engine
                )
        engine._eval_comb_loop()
        yield 0

    elif isinstance(stmt, NonBlockingAssign):
        # In initial/TB context: treat as blocking
        old_val = engine.signals.get(_lhs_name(stmt.lhs), 0)
        v, _ = eval_expr(stmt.rhs, engine.signals, engine._widths, engine._params)
        name = _lhs_name(stmt.lhs)
        engine._write(name, v)
        new_val = engine.signals[name]
        if old_val != new_val:
            nba = engine._collect_nba_for_edge(name, old_val, new_val)
            engine._apply_nba(nba)
            if engine._initial_scheduler:
                engine._initial_scheduler.notify_edge(
                    name, old_val, new_val, engine._time, engine
                )
        engine._eval_comb_loop()
        yield 0

    elif isinstance(stmt, IfStmt):
        cond = eval_val(stmt.cond, engine.signals, engine._widths, engine._params)
        if cond:
            yield from _exec_initial_gen(stmt.then_body, engine)
        elif stmt.else_body:
            yield from _exec_initial_gen(stmt.else_body, engine)

    elif isinstance(stmt, CaseStmt):
        expr_raw, expr_w = eval_expr(stmt.expr, engine.signals, engine._widths, engine._params)
        matched = False
        default_body = None
        _kind = getattr(stmt, "keyword", "case")
        for item in stmt.items:
            if item.exprs is None:
                default_body = item.body
            else:
                for e in item.exprs:
                    pat_raw, pat_w = eval_expr(e, engine.signals, engine._widths, engine._params)
                    w2 = max(_eff_width(expr_w), _eff_width(pat_w))
                    if _case_match(expr_raw, pat_raw, w2, _kind):
                        yield from _exec_initial_gen(item.body, engine)
                        matched = True
                        break
            if matched:
                break
        if not matched and default_body is not None:
            yield from _exec_initial_gen(default_body, engine)

    elif isinstance(stmt, SysTaskStmt):
        name = stmt.name
        if name in ("finish", "stop"):
            raise _FinishSignal()
        if name in ("display", "write", "strobe"):
            args_vals = [eval_val(a, engine.signals, engine._widths, engine._params)
                         for a in stmt.args]
            if stmt.args and isinstance(stmt.args[0], StringLiteral):
                fmt = stmt.args[0].text
                line = _format_display(fmt, [
                    eval_val(a, engine.signals, engine._widths, engine._params)
                    for a in stmt.args[1:]
                ])
            else:
                line = " ".join(str(v) for v in args_vals)
            engine._result.log.append(line)
            if engine.verbose:
                print(line)
        elif name == "dumpfile":
            # §18.5: $dumpfile("path") — VCD 出力先を設定
            if stmt.args:
                raw = stmt.args[0]
                if isinstance(raw, StringLiteral):
                    path = raw.text
                else:
                    v = eval_val(raw, engine.signals, engine._widths, engine._params)
                    path = str(v) if v is not None else ""
                # カレントディレクトリ基準で解決
                import os as _os
                if path and not _os.path.isabs(path):
                    path = _os.path.join(_os.getcwd(), path)
                engine._dumpfile_path = path
        elif name == "dumpvars":
            # §18.5: $dumpvars / $dumpvars(0, top) / $dumpvars(1, sig)
            # 既存の VCD ライターが設定されていなければ $dumpfile のパスで作成
            if engine.vcd_writer is None:
                vcd_path = getattr(engine, "_dumpfile_path", None)
                if not vcd_path:
                    import os as _os
                    vcd_path = _os.path.join(_os.getcwd(), "dump.vcd")
                try:
                    from deltasim.vcd.writer import VCDWriter
                    engine.vcd_writer = VCDWriter(vcd_path, engine.design)
                    engine.vcd_writer.record_initial(engine._time, engine.signals)
                except Exception as _exc:
                    engine._result.log.append(f"// $dumpvars error: {_exc}")
        elif name == "monitor":
            # §18.5: $monitor — 登録のみ。実際の出力は各タイムステップ終了後に行う
            # 新しい $monitor は古い登録を置き換える
            if stmt.args and isinstance(stmt.args[0], StringLiteral):
                fmt = stmt.args[0].text
                arg_exprs = list(stmt.args[1:])
            elif stmt.args:
                fmt = None
                arg_exprs = list(stmt.args)
            else:
                fmt = None
                arg_exprs = []
            engine._monitor_registration = (fmt, arg_exprs)
            engine._monitor_last_values = None  # reset so first eval always emits
        yield 0

    elif isinstance(stmt, NullStmt):
        pass


@dataclass(order=True)
class _ScheduledRunner:
    """Min-heap entry for a scheduled initial-block runner."""
    resume_time: int
    runner_id: int = field(compare=True)
    runner: object = field(compare=False)


class _InitialScheduler:
    """Manages all initial-block coroutines and their scheduling."""

    def __init__(self):
        self._heap: List[_ScheduledRunner] = []   # scheduled runners
        self._waiting: List[object] = []           # edge-blocked runners
        self._next_id = 0

    def add(self, body, engine: "SimEngine", start_time: int = 0):
        gen = _exec_initial_gen(body, engine)
        runner = _InitialRunner(gen)
        self._schedule_runner(runner, start_time, engine)

    def _schedule_runner(self, runner, current_time: int, engine: "SimEngine"):
        """Advance *runner* until it yields a positive delay or a WaitEdge."""
        while True:
            try:
                event = next(runner.gen)
                if isinstance(event, _WaitEdge):
                    runner.wait_edge = event
                    self._waiting.append(runner)
                    return
                elif isinstance(event, int):
                    if event > 0:
                        rid = self._next_id
                        self._next_id += 1
                        heapq.heappush(self._heap, _ScheduledRunner(
                            current_time + event, rid, runner
                        ))
                        return
                    # event == 0: keep executing
            except StopIteration:
                return
            except _FinishSignal:
                raise
            except SimError:
                raise

    def advance_to(self, target_time: int, engine: "SimEngine"):
        """Run all scheduled runners up to *target_time*."""
        while self._heap and self._heap[0].resume_time <= target_time:
            sr = heapq.heappop(self._heap)
            sr.runner.wait_edge = None
            self._schedule_runner(sr.runner, sr.resume_time, engine)

    def notify_edge(self, signal: str, old: int, new: int,
                    current_time: int, engine: "SimEngine"):
        """Resume runners waiting for this edge."""
        # Snapshot the current waiting list and clear it first,
        # so that _schedule_runner can safely append new waiters.
        current_waiting = list(self._waiting)
        self._waiting = []
        for runner in current_waiting:
            we = runner.wait_edge
            if we is None:
                continue
            if we.signal and we.signal != signal:
                self._waiting.append(runner)
                continue
            triggered = False
            if we.edge == "posedge" and old == 0 and new == 1:
                triggered = True
            elif we.edge == "negedge" and old == 1 and new == 0:
                triggered = True
            elif we.edge is None and old != new:
                triggered = True
            if triggered:
                runner.wait_edge = None
                self._schedule_runner(runner, current_time, engine)
            else:
                self._waiting.append(runner)


class _InitialRunner:
    def __init__(self, gen):
        self.gen = gen
        self.wait_edge: Optional[_WaitEdge] = None


# ---------------------------------------------------------------------------
# SimResult
# ---------------------------------------------------------------------------

@dataclass
class SimResult:
    """Snapshot history: history[cycle] = signal dict after posedge #cycle."""
    history: List[Dict[str, int]] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    finished: bool = False

    def get_value(self, signal: str, cycle: int) -> int:
        idx = cycle
        if idx < 0 or idx >= len(self.history):
            raise IndexError(f"Cycle {cycle} out of range (0..{len(self.history)-1})")
        return self.history[idx].get(signal, 0)


# ---------------------------------------------------------------------------
# Clock / reset auto-detection helpers
# ---------------------------------------------------------------------------

_CLK_NAMES = {
    "clk", "clock", "sys_clk", "clk_i", "clk_p", "pclk",
    "hclk", "mclk", "aclk", "fclk",
}
_RST_N_SUFFIXES = ("_n", "_b", "_bar")
_RST_N_NAMES = {
    "rst_n", "resetn", "reset_n", "rst_b", "nrst", "arst_n", "rstn",
    "xrst", "srst_n", "prst_n", "hrst_n",
}
_RST_NAMES = {"rst", "reset", "arst", "srst", "prst"}


def _detect_clk(signals: Dict[str, SignalInfo]) -> Optional[str]:
    for name, info in signals.items():
        if info.direction == "input" and info.width == 1:
            if name.lower() in _CLK_NAMES:
                return name
    for name, info in signals.items():
        if info.direction == "input" and info.width == 1:
            if name.lower().endswith("clk") or name.lower().endswith("clock"):
                return name
    return None


def _detect_rst(signals: Dict[str, SignalInfo]) -> Tuple[Optional[str], bool]:
    for name, info in signals.items():
        if info.direction == "input" and info.width == 1:
            nl = name.lower()
            if nl in _RST_N_NAMES:
                return name, True
            for suf in _RST_N_SUFFIXES:
                if nl.endswith(suf):
                    return name, True
    for name, info in signals.items():
        if info.direction == "input" and info.width == 1:
            nl = name.lower()
            if nl in _RST_NAMES:
                return name, False
    return None, True


# ---------------------------------------------------------------------------
# SimEngine
# ---------------------------------------------------------------------------

class SimEngine:
    """Event-driven simulator with delta-cycle semantics.

    Parameters
    ----------
    design:
        Elaborated design from ``elaborate()``.
    half_period_ns:
        Half clock period in ns (default 5 → 100 MHz).
    reset_cycles:
        Number of clock cycles to hold reset before releasing it.
    verbose:
        Print $display output and simulation progress.
    """

    def __init__(
        self,
        design: ElabDesign,
        half_period_ns: int = 5,
        reset_cycles: int = 10,
        verbose: bool = False,
        max_delta: int = MAX_DELTA,
        max_loop: int = MAX_FOREVER_TICKS,
    ):
        self.design = design
        self.half_period = half_period_ns
        self.reset_cycles = reset_cycles
        self.verbose = verbose
        self.max_delta = max_delta
        self.max_loop = max_loop

        self._widths: Dict[str, int] = {
            name: info.width for name, info in design.signals.items()
        }
        self._params = design.params

        # §18.7: 初期値ポリシー（IEEE 1800 準拠）
        # - input  ポート              → 0    (テストベンチが駆動; §18.6 constraints との整合)
        # - output/inout ポート        → x    (未リセット FF 出力は x)
        # - 内部信号 (direction=None)  → x    (未初期化 logic/reg は x)
        # - 上記のうち assign/always_comb だけで駆動される信号
        #                              → x のまま初期化後の _eval_comb_loop() で上書き
        # - どこからも駆動されない信号  → z    (未駆動 net は z)
        from deltasim.result import Z_VALUE as _Z_INIT
        _seq_driven, _any_driven = _collect_driven_signals(design)
        self.signals: Dict[str, object] = {}
        for _name, _info in design.signals.items():
            if _info.direction == "input":
                self.signals[_name] = 0              # テストベンチ駆動 → 0
            elif _name in _seq_driven:
                self.signals[_name] = None           # always_ff 駆動 → x（未リセット FF）
            elif _name in _any_driven:
                self.signals[_name] = 0              # assign/always_comb 駆動 → 0
                                                     # (_eval_comb_loop() で即時上書き)
            else:
                self.signals[_name] = _Z_INIT        # 未駆動 net → z
        self._prev: Dict[str, object] = dict(self.signals)

        self.vcd_writer = None

        self._clk: Optional[str] = _detect_clk(design.signals)
        self._rst, self._rst_active_low = _detect_rst(design.signals)

        self._time: int = 0
        self._result: SimResult = SimResult()

        # M4: build EvalContext (signed signals, struct fields, arrays)
        self._ctx: EvalContext = self._build_ctx(design)

        # Initial block scheduler (M2)
        self._initial_scheduler: Optional[_InitialScheduler] = None

        # M6: forced-signal overrides — writes to these signals are ignored
        # until the force is released.  Populated by Simulator.force().
        self._force_overrides: Dict[str, int] = {}

        # §18.1.1 deferred breakpoint: set by bp_check callback (not raised immediately)
        # checked in Simulator.step_to_ns() after each full time step
        self._deferred_bp_hit: Optional[tuple] = None  # (bp_id, file, line, time_ns)
        # active bp_check callable (set by Simulator.step_to_ns before each cycle)
        self._active_bp_check = None
        # §18.5: shared $monitor state dict (prevents cross-executor duplicates)
        self._monitor_state: dict = {}
        # §18.5: $monitor registration — (stmt, fmt_str, arg_exprs) or None
        # registered once by $monitor call; emitted at end of each time step
        self._monitor_registration: Optional[tuple] = None  # (fmt, arg_exprs)
        self._monitor_last_values: Optional[tuple] = None   # last emitted arg values
        # §18.5: $dumpfile path (set by $dumpfile sys task)
        self._dumpfile_path: Optional[str] = None

        # §18.7: 初期コンビナショナル評価（assign/always_comb の初期値を解決）
        # DeltaLimitError はコンビナショナルループを示す—run() 時に改めて検出される
        try:
            self._eval_comb_loop()
        except DeltaLimitError:
            pass

    # ------------------------------------------------------------------
    # EvalContext builder  (M4)
    # ------------------------------------------------------------------

    def _build_ctx(self, design: ElabDesign) -> EvalContext:
        """Build an EvalContext from the elaborated design."""
        ctx = EvalContext()
        # Signs
        for name, info in design.signals.items():
            if info.signed:
                ctx.signs[name] = True
        # Struct fields
        ctx.struct_fields = {k: list(v) for k, v in design.struct_fields.items()}
        ctx.signal_types = dict(design.signal_types)
        # Unpacked arrays
        for arr_name, (depth, elem_w) in design.array_decls.items():
            ctx.arrays[arr_name] = [0] * depth
            ctx.arrays_elem_width[arr_name] = elem_w
        return ctx

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_input(self, name: str, value: int) -> None:
        self._write(name, value)
        self._eval_comb_loop()

    def get_output(self, name: str) -> int:
        return self.signals.get(name, 0)

    def evaluate(self) -> None:
        self._eval_comb_loop()

    def run(
        self,
        cycles: int,
        reset_cycles: Optional[int] = None,
        pre_posedge_hook: Optional[Callable] = None,
        post_posedge_hook: Optional[Callable] = None,
    ) -> SimResult:
        """Run simulation for *cycles* clock cycles.

        Parameters
        ----------
        cycles:
            Number of clock cycles to simulate.
        reset_cycles:
            Override the reset duration (cycles).
        pre_posedge_hook:
            Called before each posedge: ``hook(cycle_1indexed, signals)``.
            Use this to apply stimuli.
        post_posedge_hook:
            Called after each posedge: ``hook(cycle_1indexed, signals_snapshot)``.
            Use this to check expected values.
        """
        if reset_cycles is not None:
            self.reset_cycles = reset_cycles

        self._time = 0
        self._result = SimResult()

        # Assert reset
        if self._rst is not None:
            rst_assert_val = 0 if self._rst_active_low else 1
            self._write(self._rst, rst_assert_val)

        # Set up initial-block scheduler
        self._initial_scheduler = _InitialScheduler()
        for ib_body in getattr(self.design, "initial_blocks", []):
            self._initial_scheduler.add(ib_body, self)

        # Initial combinational evaluation
        self._eval_comb_loop()

        # Record initial state (cycle 0)
        self._result.history.append(dict(self.signals))
        if self.vcd_writer:
            self.vcd_writer.record(self._time, self.signals)

        try:
            for cycle_idx in range(cycles):
                self._run_cycle(
                    cycle_idx,
                    pre_posedge_hook=pre_posedge_hook,
                    post_posedge_hook=post_posedge_hook,
                )
        except _FinishSignal:
            self._result.finished = True

        return self._result

    # ------------------------------------------------------------------
    # Internal: one full clock cycle
    # ------------------------------------------------------------------

    def _run_cycle(
        self,
        cycle_idx: int,
        pre_posedge_hook: Optional[Callable] = None,
        post_posedge_hook: Optional[Callable] = None,
        posedge_only: bool = False,   # §18: stop after posedge (sub-cycle timing)
        negedge_only: bool = False,   # §18: skip posedge, only run negedge (resume)
    ) -> bool:
        """Run one clock cycle.  Returns True if both posedge and negedge ran."""
        hp = self.half_period
        cycle_num = cycle_idx + 1

        # --- Run initial blocks up to posedge time ---
        posedge_time = cycle_idx * 2 * hp + hp

        # §18.1.1 negedge_only: skip posedge phase entirely (resuming mid-cycle)
        if negedge_only:
            negedge_time = (cycle_idx + 1) * 2 * hp
            if self._initial_scheduler:
                self._initial_scheduler.advance_to(negedge_time - 1, self)
            if self._clk is not None:
                prev_clk = self.signals.get(self._clk, 0)
                self._prev[self._clk] = prev_clk
                self._time = negedge_time
                self._write(self._clk, 0)
                if self._initial_scheduler:
                    self._initial_scheduler.notify_edge(
                        self._clk, prev_clk, 0, self._time, self
                    )
                nba = self._collect_nba_for_edge(self._clk, 1, 0)
                self._apply_nba(nba)
                self._eval_comb_loop()
            else:
                self._time = negedge_time
            if self.vcd_writer:
                self.vcd_writer.record(self._time, self.signals)
            return True  # both phases done (posedge was done in previous call)

        if self._initial_scheduler:
            self._initial_scheduler.advance_to(posedge_time - 1, self)

        # --- Reset control ---
        if self._rst is not None:
            rst_asserted = cycle_idx < self.reset_cycles
            new_rst = (0 if self._rst_active_low else 1) if rst_asserted \
                      else (1 if self._rst_active_low else 0)
            old_rst = self.signals.get(self._rst, 0)
            if old_rst != new_rst:
                self._prev[self._rst] = old_rst
                self._write(self._rst, new_rst)
                if self._initial_scheduler:
                    self._initial_scheduler.notify_edge(
                        self._rst, old_rst, new_rst, self._time, self
                    )
                nba = self._collect_nba_for_edge(self._rst, old_rst, new_rst)
                self._apply_nba(nba)
                self._eval_comb_loop()

        # --- Pre-posedge hook (stimulus injection) ---
        if pre_posedge_hook:
            pre_posedge_hook(cycle_num, self.signals)
            # Re-evaluate combinational logic after input changes so that
            # always_ff sees the settled combinational outputs (e.g., FSM next-state).
            self._eval_comb_loop()

        # --- Posedge clk ---
        if self._clk is not None:
            prev_clk = self.signals.get(self._clk, 0)
            self._prev[self._clk] = prev_clk
            self._time = posedge_time
            self._write(self._clk, 1)
            if self._initial_scheduler:
                self._initial_scheduler.notify_edge(
                    self._clk, prev_clk, 1, self._time, self
                )
            nba = self._collect_nba_for_edge(self._clk, 0, 1)
            self._apply_nba(nba)
            self._eval_comb_loop()
        else:
            # No clock: advance initial blocks to posedge_time (may drive clk/signals)
            self._time = posedge_time
            if self._initial_scheduler:
                self._initial_scheduler.advance_to(posedge_time, self)
            self._eval_comb_loop()

        if self.vcd_writer:
            self.vcd_writer.record(self._time, self.signals)

        # --- Record posedge state ---
        self._result.history.append(dict(self.signals))

        # --- Post-posedge hook (check values) ---
        if post_posedge_hook:
            post_posedge_hook(cycle_num, dict(self.signals))

        # §18: Stop after posedge if requested (sub-cycle timing)
        if posedge_only:
            _engine_check_monitor(self)
            return False  # negedge not yet run

        # --- Run initial blocks between posedge and negedge ---
        negedge_time = (cycle_idx + 1) * 2 * hp
        if self._initial_scheduler:
            self._initial_scheduler.advance_to(negedge_time - 1, self)

        # --- Negedge clk ---
        if self._clk is not None:
            prev_clk = self.signals.get(self._clk, 0)
            self._prev[self._clk] = prev_clk
            self._time = negedge_time
            self._write(self._clk, 0)
            if self._initial_scheduler:
                self._initial_scheduler.notify_edge(
                    self._clk, prev_clk, 0, self._time, self
                )
            nba = self._collect_nba_for_edge(self._clk, 1, 0)
            self._apply_nba(nba)
            self._eval_comb_loop()
        else:
            self._time = negedge_time

        if self.vcd_writer:
            self.vcd_writer.record(self._time, self.signals)
        # §18.5: $monitor — タイムステップ終了後に変化を確認して出力
        _engine_check_monitor(self)
        return True  # both posedge and negedge ran

    # ------------------------------------------------------------------
    # Internal: NBA collection for a single edge event
    # ------------------------------------------------------------------

    def _collect_nba_for_edge(
        self, sig_name: str, old_val: int, new_val: int
    ) -> List[Tuple[str, int]]:
        nba: List[Tuple[str, int]] = []
        for proc in self.design.seq_procs:
            if self._is_triggered(proc.sensitivity, sig_name, old_val, new_val):
                ex = _Executor(self.signals, self._widths, self._params, self._ctx,
                               max_loop=self.max_loop, time_ns=self._time,
                               bp_check=getattr(self, "_active_bp_check", None),
                                              monitor_state=getattr(self, "_monitor_state", {}))
                try:
                    ex.execute(proc.body)
                except _FinishSignal:
                    raise
                nba.extend(ex.nba_queue)
                # §FIX: Apply blocking-assignment (=) results back to engine signals.
                # ex.signals starts as a copy of self.signals; only blocking assignments
                # modify it in-place during execution.  Without this the blocking '='
                # inside always_ff silently drops its value (only NBA '<=' was returned).
                for name, new_v in ex.signals.items():
                    if new_v != self.signals.get(name):
                        self.signals[name] = new_v
                for line in ex.display_log:
                    self._result.log.append(line)
                    if self.verbose:
                        print(line)

        return nba

    @staticmethod
    def _is_triggered(
        sens: SensitivityList, sig: str, old: int, new: int
    ) -> bool:
        for item in sens.items:
            sig_name = (item.signal.name
                        if isinstance(item.signal, Identifier)
                        else _lhs_name(item.signal))
            if sig_name != sig:
                continue
            if item.edge == "posedge" and old == 0 and new == 1:
                return True
            if item.edge == "negedge" and old == 1 and new == 0:
                return True
            if item.edge is None and old != new:
                return True
        return False

    # ------------------------------------------------------------------
    # Internal: apply NBA queue
    # ------------------------------------------------------------------

    def _apply_nba(self, nba: List[tuple]) -> None:
        """Apply NBA queue items (signal or array writes)."""
        for item in nba:
            if len(item) == 2:
                lhs_name, raw_val = item
                self._write(lhs_name, raw_val)
            elif len(item) == 3:
                # Array element write: (arr_name, idx, val)
                arr_name, idx, raw_val = item
                arr = self._ctx.arrays.get(arr_name)
                if arr is not None and 0 <= idx < len(arr):
                    elem_w = self._ctx.arrays_elem_width.get(arr_name, 1)
                    if raw_val is None:
                        arr[idx] = None
                    else:
                        arr[idx] = raw_val & ((1 << elem_w) - 1)
    # ------------------------------------------------------------------
    # Internal: combinational convergence loop (delta cycles)
    # ------------------------------------------------------------------

    def _eval_comb_loop(self) -> None:
        prev_signals: Optional[Dict[str, int]] = None
        for _ in range(self.max_delta):
            changed = False

            for proc in self.design.assign_procs:
                # §18: bp_check for assign lines (deferred, fires after delta settles)
                _bp = getattr(self, "_active_bp_check", None)
                if _bp is not None:
                    sl = getattr(proc, "source_line", 0)
                    sf = getattr(proc, "source_file", "")
                    if sl:
                        _bp(sf, sl)
                v, w = eval_expr(proc.rhs, self.signals, self._widths, self._params, self._ctx)
                changed |= _assign_to_lhs(proc.lhs, v, w, self.signals, self._widths, self._ctx)

            for proc in self.design.comb_procs:
                ex = _Executor(self.signals, self._widths, self._params, self._ctx,
                               max_loop=self.max_loop, time_ns=self._time,
                               bp_check=getattr(self, "_active_bp_check", None),
                                              monitor_state=getattr(self, "_monitor_state", {}))
                try:
                    ex.execute(proc.body)
                except _FinishSignal:
                    raise
                for lhs_name, raw_val in ex.nba_queue:
                    changed |= _write_signal(self.signals, lhs_name, raw_val, self._widths)
                for name, val in ex.signals.items():
                    if self.signals.get(name, 0) != val:
                        self.signals[name] = val
                        changed = True
                for line in ex.display_log:
                    self._result.log.append(line)
                    if self.verbose:
                        print(line)


            if not changed:
                break
            prev_signals = dict(self.signals)
        else:
            # Find a signal that keeps changing for a helpful hint
            # Check both assign_procs and comb_procs (always_comb)
            hint = ""
            if prev_signals is not None:
                # assign procs
                for proc in self.design.assign_procs:
                    v, w = eval_expr(proc.rhs, self.signals, self._widths,
                                     self._params, self._ctx)
                    tmp: Dict[str, int] = dict(self.signals)
                    _assign_to_lhs(proc.lhs, v, w, tmp, self._widths, self._ctx)
                    changed_sigs = [n for n in tmp if tmp[n] != prev_signals.get(n)]
                    if changed_sigs:
                        hint = changed_sigs[0]
                        break
                # comb_procs (always_comb) — if no hint yet
                if not hint:
                    for proc in self.design.comb_procs:
                        ex = _Executor(self.signals, self._widths, self._params,
                                       self._ctx, max_loop=self.max_loop,
                                       time_ns=self._time,
                                                      monitor_state=getattr(self, "_monitor_state", {}))
                        try:
                            ex.execute(proc.body)
                        except Exception:
                            pass
                        for lhs_name, raw_val in ex.nba_queue:
                            if raw_val != prev_signals.get(lhs_name):
                                hint = lhs_name
                                break
                        if not hint:
                            for name, val in ex.signals.items():
                                if val != prev_signals.get(name):
                                    hint = name
                                    break
                        if hint:
                            break
            raise DeltaLimitError(self._time, signal_hint=hint, max_delta=self.max_delta)

    # ------------------------------------------------------------------
    # Internal: write a signal value with masking
    # ------------------------------------------------------------------

    def _write(self, name: str, raw_val) -> None:
        """Write a value to a signal with masking.

        §M6: If the signal is in _force_overrides, the write is ignored so
        that force() semantics take precedence over NBA and comb outputs.
        """
        if name in self._force_overrides:
            return   # forced signal — ignore any RTL-driven write
        _write_signal(self.signals, name, raw_val, self._widths)

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def set_clk(self, name: str) -> None:
        self._clk = name

    def set_rst(self, name: str, active_low: bool = True) -> None:
        self._rst = name
        self._rst_active_low = active_low


# ---------------------------------------------------------------------------
# Module-level helpers for signal writing and LHS assignment
# ---------------------------------------------------------------------------

def _write_signal(
    signals: Dict[str, Optional[int]],
    name: str,
    raw_val,
    widths: Dict[str, int],
) -> bool:
    """Write *raw_val* to *signals[name]* with width masking. Returns True if changed."""
    from deltasim.result import Z_VALUE
    old = signals.get(name, 0)

    if raw_val is None:
        if old is not None:
            signals[name] = None
            return True
        return False
    if raw_val is Z_VALUE:
        if old is not Z_VALUE:
            signals[name] = Z_VALUE
            return True
        return False

    # §18.7: FourState 値の書き込み
    try:
        from deltasim.fourstate import FourState as _FS
        if isinstance(raw_val, _FS):
            w = widths.get(name, 1)
            new_fs = _FS(raw_val.aval & ((1 << w) - 1),
                         raw_val.bval & ((1 << w) - 1), w)
            if new_fs.bval == 0:
                # 純2値に落とす
                new_int = new_fs.aval
                if old != new_int:
                    signals[name] = new_int
                    return True
                return False
            if old != new_fs:
                signals[name] = new_fs
                return True
            return False
    except ImportError:
        pass

    w = widths.get(name, 1)
    new_val = raw_val & ((1 << w) - 1)
    if old != new_val:
        signals[name] = new_val
        return True
    return False


def _assign_to_lhs(
    lhs_expr,
    value: Optional[int],
    width: Optional[int],
    signals: Dict[str, Optional[int]],
    widths: Dict[str, int],
    ctx: Optional[EvalContext] = None,
) -> bool:
    """Assign *value* to *lhs_expr* in *signals*. Returns True if any signal changed."""
    from deltasim.parser.ast_nodes import Identifier, Concat, BitSelect, PartSelect

    if isinstance(lhs_expr, Identifier):
        return _write_signal(signals, lhs_expr.name, value, widths)

    if isinstance(lhs_expr, Concat):
        # Distribute bits from MSB to LSB across the concat parts
        # Compute widths of each part
        part_widths = []
        for part in lhs_expr.parts:
            if isinstance(part, Identifier):
                pw = widths.get(part.name, 1)
            elif isinstance(part, PartSelect):
                # rough estimate — actual eval would need params
                pw = 1
            elif isinstance(part, BitSelect):
                pw = 1
            else:
                pw = _eff_width(None)
            part_widths.append(pw)
        total_w = sum(part_widths)

        if value is None:
            changed = False
            for part in lhs_expr.parts:
                if isinstance(part, Identifier):
                    changed |= _write_signal(signals, part.name, None, widths)
            return changed

        changed = False
        offset = total_w
        for part, pw in zip(lhs_expr.parts, part_widths):
            offset -= pw
            bits = (value >> offset) & ((1 << pw) - 1)
            if isinstance(part, Identifier):
                changed |= _write_signal(signals, part.name, bits, widths)
        return changed

    if isinstance(lhs_expr, BitSelect):
        # base_name[idx] = value
        if isinstance(lhs_expr.base, Identifier):
            name = lhs_expr.base.name
            # idx would need eval — skip for now
        return False

    if isinstance(lhs_expr, PartSelect):
        return False

    return False
