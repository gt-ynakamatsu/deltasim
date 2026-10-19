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
        if cond_v is None:
            return (None, None)
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
        if base_v is None or idx_v is None:
            return (None, 1)
        return ((base_v >> idx_v) & 1, 1)

    if isinstance(expr, PartSelect):
        base_v, _ = eval_expr(expr.base, signals, widths, params, ctx)
        hi_v, _ = eval_expr(expr.hi, signals, widths, params, ctx)
        lo_v, _ = eval_expr(expr.lo, signals, widths, params, ctx)
        if base_v is None or hi_v is None or lo_v is None:
            return (None, 1)
        w = hi_v - lo_v + 1
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


def _eval_binop(expr, signals, widths, params, ctx=None) -> Tuple[Optional[int], Optional[int]]:
    lv_raw, lw = eval_expr(expr.left, signals, widths, params, ctx)
    rv_raw, rw = eval_expr(expr.right, signals, widths, params, ctx)
    # §12.4: Z treated as X for arithmetic
    lv = _to_x(lv_raw)
    rv = _to_x(rv_raw)
    op = expr.op
    w = max(_eff_width(lw), _eff_width(rw))
    mask = (1 << w) - 1

    # X/Z propagation with special cases per IEEE 4-state logic
    if lv is None or rv is None:
        # 0 & X = 0,  X | 1 = 1
        if op == "&":
            if lv == 0 or rv == 0:
                return (0, w)
        elif op == "|":
            if (lv is not None and lv & mask == mask) or (rv is not None and rv & mask == mask):
                return (mask, w)
        elif op == "&&":
            if (lv is not None and not lv) or (rv is not None and not rv):
                return (0, 1)
        elif op == "||":
            if (lv is not None and lv) or (rv is not None and rv):
                return (1, 1)
        return (None, w)

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
    if v is None:
        return (None, w)
    ew = _eff_width(w)
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
    for part in expr.parts:
        v, w = eval_expr(part, signals, widths, params, ctx)
        pw = _eff_width(w)
        if v is None:
            return (None, total_w + pw)
        result = (result << pw) | (v & ((1 << pw) - 1))
        total_w += pw
    return (result, total_w)


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
            if spec in ('d', 'D'):
                out.append(str(val))
            elif spec in ('h', 'H', 'x', 'X'):
                out.append(hex(val))
            elif spec in ('b', 'B'):
                out.append(bin(val))
            elif spec in ('o', 'O'):
                out.append(oct(val))
            elif spec == '0':
                # %0d / %0h etc.
                if i + 2 < len(fmt):
                    spec2 = fmt[i + 2]
                    if spec2 in ('d', 'D'):
                        out.append(str(val))
                    elif spec2 in ('h', 'H', 'x', 'X'):
                        out.append(hex(val)[2:])
                    else:
                        out.append(str(val))
                    i += 3
                    continue
                out.append(str(val))
            elif spec == 's':
                out.append(str(val))
            elif spec == 't':
                out.append(str(val))
            else:
                out.append(f'%{spec}')
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
    ):
        self.signals = dict(signals)
        self.widths = widths
        self.params = params
        self.ctx = ctx or _EMPTY_CTX
        self.max_loop = max_loop
        self.time_ns = time_ns
        # NBA items: (name, val) for signals, (arr_name, idx, val) for arrays
        self.nba_queue: List[tuple] = []
        self.display_log: List[str] = []

    def execute(self, stmt) -> None:
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
            expr_v = eval_val(stmt.expr, self.signals, self.widths, self.params, self.ctx)
            matched = False
            default_body = None
            for item in stmt.items:
                if item.exprs is None:
                    default_body = item.body
                else:
                    for e in item.exprs:
                        if eval_val(e, self.signals, self.widths, self.params, self.ctx) == expr_v:
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
            if v is None:
                self.signals[lhs_name] = None
            else:
                sig_w = self.widths.get(lhs_name, _eff_width(w))
                self.signals[lhs_name] = v & ((1 << sig_w) - 1)

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
        if name in ("display", "write", "monitor", "strobe"):
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


class _FinishSignal(Exception):
    """Raised when $finish / $stop is executed."""


def _lhs_name(lhs) -> str:
    if isinstance(lhs, Identifier):
        return lhs.name
    if isinstance(lhs, (BitSelect, PartSelect)):
        return _lhs_name(lhs.base)
    raise SimError(f"Unsupported LHS expression type: {type(lhs).__name__}")


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
        expr_v = eval_val(stmt.expr, engine.signals, engine._widths, engine._params)
        matched = False
        for item in stmt.items:
            if item.exprs is None:
                yield from _exec_initial_gen(item.body, engine)
                break
            for e in item.exprs:
                if eval_val(e, engine.signals, engine._widths, engine._params) == expr_v:
                    yield from _exec_initial_gen(item.body, engine)
                    matched = True
                    break
            if matched:
                break

    elif isinstance(stmt, SysTaskStmt):
        name = stmt.name
        if name in ("finish", "stop"):
            raise _FinishSignal()
        if name in ("display", "write", "monitor", "strobe", "dumpfile", "dumpvars"):
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

        self.signals: Dict[str, int] = {name: 0 for name in design.signals}
        self._prev: Dict[str, int] = dict(self.signals)

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
    ) -> None:
        hp = self.half_period
        cycle_num = cycle_idx + 1

        # --- Run initial blocks up to posedge time ---
        posedge_time = cycle_idx * 2 * hp + hp
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
                               max_loop=self.max_loop, time_ns=self._time)
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
                v, w = eval_expr(proc.rhs, self.signals, self._widths, self._params, self._ctx)
                changed |= _assign_to_lhs(proc.lhs, v, w, self.signals, self._widths, self._ctx)

            for proc in self.design.comb_procs:
                ex = _Executor(self.signals, self._widths, self._params, self._ctx,
                               max_loop=self.max_loop, time_ns=self._time)
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
                                       time_ns=self._time)
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
    if raw_val is None:
        old = signals.get(name, 0)
        if old is not None:
            signals[name] = None
            return True
        return False
    if isinstance(raw_val, type(Z_VALUE)):   # Z_VALUE sentinel
        old = signals.get(name)
        if not isinstance(old, type(Z_VALUE)):
            signals[name] = Z_VALUE
            return True
        return False
    w = widths.get(name, 1)
    new_val = raw_val & ((1 << w) - 1)
    if signals.get(name, 0) != new_val:
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
