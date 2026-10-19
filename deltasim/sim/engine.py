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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from deltasim.elab.elaborator import ElabDesign, SignalInfo, SeqProc, CombProc, AssignProc
from deltasim.parser.ast_nodes import (
    SensitivityList, SensitivityItem,
    Block, IfStmt, CaseStmt, CaseItem,
    BlockingAssign, NonBlockingAssign, NullStmt, SysTaskStmt,
    NumberLiteral, FillLiteral, StringLiteral, Identifier,
    BinOp, UnaryOp, Conditional, BitSelect, PartSelect, Concat, Replicate,
    SysFunc,
)


MAX_DELTA = 1000   # delta-cycle convergence limit

# ---------------------------------------------------------------------------
# SimError
# ---------------------------------------------------------------------------

class SimError(Exception):
    pass


# ---------------------------------------------------------------------------
# Expression evaluator
# ---------------------------------------------------------------------------

def eval_expr(
    expr,
    signals: Dict[str, int],
    widths: Dict[str, int],
    params: Dict[str, int],
) -> Tuple[int, Optional[int]]:
    """Evaluate *expr* and return (value, width).

    *width* is ``None`` for unsized literals (treated as 32-bit when a
    specific width is required).
    """
    if isinstance(expr, NumberLiteral):
        return (expr.value, expr.width)

    if isinstance(expr, FillLiteral):
        # '0 → 0,  '1 → all-ones (mask at assignment),  'x/'z → 0
        val = 0 if expr.fill in ("0", "x", "z") else -1
        return (val, None)

    if isinstance(expr, StringLiteral):
        # Strings only appear as $display args; return 0 here
        return (0, None)

    if isinstance(expr, Identifier):
        name = expr.name
        if name in params:
            return (params[name], 32)
        val = signals.get(name, 0)
        w = widths.get(name, 1)
        return (val, w)

    if isinstance(expr, BinOp):
        return _eval_binop(expr, signals, widths, params)

    if isinstance(expr, UnaryOp):
        return _eval_unaryop(expr, signals, widths, params)

    if isinstance(expr, Conditional):
        cond_v, _ = eval_expr(expr.cond, signals, widths, params)
        if cond_v:
            return eval_expr(expr.then_expr, signals, widths, params)
        else:
            return eval_expr(expr.else_expr, signals, widths, params)

    if isinstance(expr, BitSelect):
        base_v, _ = eval_expr(expr.base, signals, widths, params)
        idx_v, _ = eval_expr(expr.index, signals, widths, params)
        return ((base_v >> idx_v) & 1, 1)

    if isinstance(expr, PartSelect):
        base_v, _ = eval_expr(expr.base, signals, widths, params)
        hi_v, _ = eval_expr(expr.hi, signals, widths, params)
        lo_v, _ = eval_expr(expr.lo, signals, widths, params)
        w = hi_v - lo_v + 1
        mask = (1 << w) - 1
        return ((base_v >> lo_v) & mask, w)

    if isinstance(expr, Concat):
        return _eval_concat(expr, signals, widths, params)

    if isinstance(expr, Replicate):
        cnt_v, _ = eval_expr(expr.count, signals, widths, params)
        val_v, val_w = eval_expr(expr.value, signals, widths, params)
        if val_w is None:
            val_w = 32
        result = 0
        for _ in range(cnt_v):
            result = (result << val_w) | (val_v & ((1 << val_w) - 1))
        return (result, cnt_v * val_w)

    if isinstance(expr, SysFunc):
        return _eval_sysfunc(expr, signals, widths, params)

    raise SimError(f"Unknown expression type: {type(expr).__name__}")


def _eff_width(w: Optional[int]) -> int:
    return w if w is not None else 32


def _eval_binop(expr, signals, widths, params) -> Tuple[int, Optional[int]]:
    lv, lw = eval_expr(expr.left, signals, widths, params)
    rv, rw = eval_expr(expr.right, signals, widths, params)
    op = expr.op

    # Comparison → 1-bit result
    if op == "==":
        return (int(lv == rv), 1)
    if op == "!=":
        return (int(lv != rv), 1)
    if op == "===":
        return (int(lv == rv), 1)
    if op == "!==":
        return (int(lv != rv), 1)
    if op == "<":
        return (int(lv < rv), 1)
    if op == ">":
        return (int(lv > rv), 1)
    if op == "<=":
        return (int(lv <= rv), 1)
    if op == ">=":
        return (int(lv >= rv), 1)

    # Logical
    if op == "&&":
        return (int(bool(lv) and bool(rv)), 1)
    if op == "||":
        return (int(bool(lv) or bool(rv)), 1)

    # Arithmetic / bitwise – result width = max of operands
    w = max(_eff_width(lw), _eff_width(rw))
    mask = (1 << w) - 1
    if op == "+":
        return (lv + rv, w)
    if op == "-":
        return (lv - rv, w)
    if op == "*":
        return (lv * rv, w)
    if op == "/":
        return ((lv // rv if rv != 0 else 0), w)
    if op == "%":
        return ((lv % rv if rv != 0 else 0), w)
    if op == "&":
        return (lv & rv, w)
    if op == "|":
        return (lv | rv, w)
    if op == "^":
        return (lv ^ rv, w)
    if op in ("^~", "~^"):
        return (~(lv ^ rv) & mask, w)
    if op == "<<":
        return (lv << rv, w)
    if op == ">>":
        return ((lv >> rv), w)
    if op == "<<<":
        return (lv << rv, w)
    if op == ">>>":
        # Arithmetic right shift – sign-extend using lw
        wl = _eff_width(lw)
        sign = (lv >> (wl - 1)) & 1
        if sign:
            sign_ext = (-1 << wl) | lv
            return (sign_ext >> rv, w)
        return (lv >> rv, w)
    if op == "**":
        return (lv ** rv, w)

    raise SimError(f"Unknown binary operator: {op!r}")


def _eval_unaryop(expr, signals, widths, params) -> Tuple[int, Optional[int]]:
    v, w = eval_expr(expr.operand, signals, widths, params)
    op = expr.op
    ew = _eff_width(w)
    mask = (1 << ew) - 1

    if op == "!":
        return (int(not v), 1)
    if op == "~":
        return (~v & mask, ew)
    if op == "-":
        return (-v, ew)
    if op == "+":
        return (v, ew)

    # Reduction operators → 1-bit
    if op == "&":   # AND reduction
        return (int(v & mask == mask), 1)
    if op == "~&":  # NAND reduction
        return (int(v & mask != mask), 1)
    if op == "|":   # OR reduction
        return (int(v != 0), 1)
    if op == "~|":  # NOR reduction
        return (int(v == 0), 1)
    if op == "^":   # XOR reduction (parity)
        parity = 0
        tmp = v & mask
        while tmp:
            parity ^= tmp & 1
            tmp >>= 1
        return (parity, 1)
    if op in ("^~", "~^"):  # XNOR reduction
        parity = 0
        tmp = v & mask
        while tmp:
            parity ^= tmp & 1
            tmp >>= 1
        return (parity ^ 1, 1)

    raise SimError(f"Unknown unary operator: {op!r}")


def _eval_concat(expr, signals, widths, params) -> Tuple[int, int]:
    result = 0
    total_w = 0
    for part in expr.parts:
        v, w = eval_expr(part, signals, widths, params)
        pw = _eff_width(w)
        result = (result << pw) | (v & ((1 << pw) - 1))
        total_w += pw
    return (result, total_w)


def _eval_sysfunc(expr, signals, widths, params) -> Tuple[int, Optional[int]]:
    name = expr.name
    if name == "clog2":
        v, _ = eval_expr(expr.args[0], signals, widths, params)
        return (max(1, math.ceil(math.log2(max(v, 2)))), 32)
    if name == "signed":
        return eval_expr(expr.args[0], signals, widths, params)
    if name == "unsigned":
        return eval_expr(expr.args[0], signals, widths, params)
    # Unknown system function → 0
    return (0, 32)


def eval_val(expr, signals, widths, params) -> int:
    """Evaluate and return only the integer value."""
    v, _ = eval_expr(expr, signals, widths, params)
    return v


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
    ):
        # Local copy for blocking assignments (doesn't affect outer signals)
        self.signals = dict(signals)
        self.widths = widths
        self.params = params
        self.nba_queue: List[Tuple[str, int]] = []  # (lhs_name, raw_value)
        self.display_log: List[str] = []

    def execute(self, stmt) -> None:
        if isinstance(stmt, Block):
            for s in stmt.stmts:
                self.execute(s)

        elif isinstance(stmt, IfStmt):
            cond = eval_val(stmt.cond, self.signals, self.widths, self.params)
            if cond:
                self.execute(stmt.then_body)
            elif stmt.else_body is not None:
                self.execute(stmt.else_body)

        elif isinstance(stmt, CaseStmt):
            expr_v = eval_val(stmt.expr, self.signals, self.widths, self.params)
            matched = False
            default_body = None
            for item in stmt.items:
                if item.exprs is None:
                    default_body = item.body
                else:
                    for e in item.exprs:
                        if eval_val(e, self.signals, self.widths, self.params) == expr_v:
                            self.execute(item.body)
                            matched = True
                            break
                if matched:
                    break
            if not matched and default_body is not None:
                self.execute(default_body)

        elif isinstance(stmt, BlockingAssign):
            v, w = eval_expr(stmt.rhs, self.signals, self.widths, self.params)
            lhs_name = _lhs_name(stmt.lhs)
            sig_w = self.widths.get(lhs_name, _eff_width(w))
            mask = (1 << sig_w) - 1
            self.signals[lhs_name] = v & mask

        elif isinstance(stmt, NonBlockingAssign):
            v, _ = eval_expr(stmt.rhs, self.signals, self.widths, self.params)
            lhs_name = _lhs_name(stmt.lhs)
            self.nba_queue.append((lhs_name, v))

        elif isinstance(stmt, NullStmt):
            pass

        elif isinstance(stmt, SysTaskStmt):
            self._handle_sys_task(stmt)

        else:
            raise SimError(f"Unknown statement: {type(stmt).__name__}")

    def _handle_sys_task(self, stmt: SysTaskStmt) -> None:
        name = stmt.name
        if name in ("finish", "stop"):
            raise _FinishSignal()
        if name in ("display", "write", "monitor", "strobe"):
            parts = []
            for arg in stmt.args:
                v, _ = eval_expr(arg, self.signals, self.widths, self.params)
                parts.append(str(v))
            self.display_log.append(" ".join(parts))
        # Other system tasks silently ignored


class _FinishSignal(Exception):
    """Raised when $finish / $stop is executed."""


def _lhs_name(lhs) -> str:
    """Extract the base signal name from an LHS expression."""
    if isinstance(lhs, Identifier):
        return lhs.name
    if isinstance(lhs, (BitSelect, PartSelect)):
        return _lhs_name(lhs.base)
    raise SimError(f"Unsupported LHS expression type: {type(lhs).__name__}")


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
        """Return signal value after the *cycle*-th posedge (1-indexed).

        cycle=0 returns the initial state before any clock edge.
        """
        idx = cycle  # history[0] = initial, history[1] = after cycle 1
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
    ):
        self.design = design
        self.half_period = half_period_ns
        self.reset_cycles = reset_cycles
        self.verbose = verbose

        self._widths: Dict[str, int] = {
            name: info.width for name, info in design.signals.items()
        }
        self._params = design.params

        # Current signal state
        self.signals: Dict[str, int] = {name: 0 for name in design.signals}
        # Previous signal state (for edge detection)
        self._prev: Dict[str, int] = dict(self.signals)

        # VCD writer (injected externally)
        self.vcd_writer = None

        # Auto-detect clock and reset
        self._clk: Optional[str] = _detect_clk(design.signals)
        self._rst, self._rst_active_low = _detect_rst(design.signals)

        self._time: int = 0
        self._result: SimResult = SimResult()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_input(self, name: str, value: int) -> None:
        """Drive an input port to *value* and propagate combinationally."""
        self._write(name, value)
        self._eval_comb_loop()

    def get_output(self, name: str) -> int:
        """Read current value of a signal."""
        return self.signals.get(name, 0)

    def evaluate(self) -> None:
        """Evaluate all combinational processes (no clock needed)."""
        self._eval_comb_loop()

    def run(
        self,
        cycles: int,
        reset_cycles: Optional[int] = None,
    ) -> SimResult:
        """Run simulation for *cycles* clock cycles.

        Returns a :class:`SimResult` with per-cycle signal snapshots.
        """
        if reset_cycles is not None:
            self.reset_cycles = reset_cycles

        self._time = 0
        self._result = SimResult()

        # Assert reset if reset signal found
        if self._rst is not None:
            rst_assert_val = 0 if self._rst_active_low else 1
            self._write(self._rst, rst_assert_val)

        # Initial combinational evaluation
        self._eval_comb_loop()

        # Record initial state (cycle 0)
        self._result.history.append(dict(self.signals))
        if self.vcd_writer:
            self.vcd_writer.record(self._time, self.signals)

        try:
            for cycle_idx in range(cycles):
                self._run_cycle(cycle_idx)
        except _FinishSignal:
            self._result.finished = True

        return self._result

    # ------------------------------------------------------------------
    # Internal: one full clock cycle
    # ------------------------------------------------------------------

    def _run_cycle(self, cycle_idx: int) -> None:
        hp = self.half_period

        # Update reset at the start of each cycle
        if self._rst is not None:
            rst_asserted = cycle_idx < self.reset_cycles
            if self._rst_active_low:
                new_rst = 0 if rst_asserted else 1
            else:
                new_rst = 1 if rst_asserted else 0

            old_rst = self.signals.get(self._rst, 0)
            if old_rst != new_rst:
                self._prev[self._rst] = old_rst
                self._write(self._rst, new_rst)
                # Evaluate sequential processes triggered by rst edge
                nba = self._collect_nba_for_edge(self._rst, old_rst, new_rst)
                self._apply_nba(nba)
                self._eval_comb_loop()

        # ---- Posedge clk ----
        if self._clk is not None:
            self._prev[self._clk] = self.signals.get(self._clk, 0)
            self._time = cycle_idx * 2 * hp + hp
            self._write(self._clk, 1)
            nba = self._collect_nba_for_edge(self._clk, 0, 1)
            self._apply_nba(nba)
            self._eval_comb_loop()
        else:
            self._time = cycle_idx * 2 * hp + hp

        if self.vcd_writer:
            self.vcd_writer.record(self._time, self.signals)

        # Snapshot after posedge
        self._result.history.append(dict(self.signals))

        # ---- Negedge clk ----
        if self._clk is not None:
            self._prev[self._clk] = self.signals.get(self._clk, 0)
            self._time = (cycle_idx + 1) * 2 * hp
            self._write(self._clk, 0)
            nba = self._collect_nba_for_edge(self._clk, 1, 0)
            self._apply_nba(nba)
            self._eval_comb_loop()
        else:
            self._time = (cycle_idx + 1) * 2 * hp

        if self.vcd_writer:
            self.vcd_writer.record(self._time, self.signals)

    # ------------------------------------------------------------------
    # Internal: NBA collection for a single edge event
    # ------------------------------------------------------------------

    def _collect_nba_for_edge(
        self, sig_name: str, old_val: int, new_val: int
    ) -> List[Tuple[str, int]]:
        """Run all SeqProcs triggered by the given edge, return NBA list."""
        nba: List[Tuple[str, int]] = []
        for proc in self.design.seq_procs:
            if self._is_triggered(proc.sensitivity, sig_name, old_val, new_val):
                ex = _Executor(self.signals, self._widths, self._params)
                try:
                    ex.execute(proc.body)
                except _FinishSignal:
                    raise
                nba.extend(ex.nba_queue)
                if ex.display_log:
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
            sig_name = _lhs_name(item.signal) if not isinstance(item.signal, Identifier) \
                else item.signal.name
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

    def _apply_nba(self, nba: List[Tuple[str, int]]) -> None:
        for lhs_name, raw_val in nba:
            self._write(lhs_name, raw_val)

    # ------------------------------------------------------------------
    # Internal: combinational convergence loop (delta cycles)
    # ------------------------------------------------------------------

    def _eval_comb_loop(self) -> None:
        for delta in range(MAX_DELTA):
            changed = False

            # Continuous assigns
            for proc in self.design.assign_procs:
                v, w = eval_expr(proc.rhs, self.signals, self._widths, self._params)
                lhs_name = _lhs_name(proc.lhs)
                sig_w = self._widths.get(lhs_name, _eff_width(w))
                mask = (1 << sig_w) - 1
                new_val = v & mask
                if self.signals.get(lhs_name, 0) != new_val:
                    self.signals[lhs_name] = new_val
                    changed = True

            # always_comb processes
            for proc in self.design.comb_procs:
                ex = _Executor(self.signals, self._widths, self._params)
                try:
                    ex.execute(proc.body)
                except _FinishSignal:
                    raise
                for lhs_name, raw_val in ex.nba_queue:
                    sig_w = self._widths.get(lhs_name, 32)
                    mask = (1 << sig_w) - 1
                    new_val = raw_val & mask
                    if self.signals.get(lhs_name, 0) != new_val:
                        self.signals[lhs_name] = new_val
                        changed = True
                # blocking assigns already updated ex.signals; mirror them
                for name, val in ex.signals.items():
                    if self.signals.get(name, 0) != val:
                        self.signals[name] = val
                        changed = True
                if ex.display_log:
                    for line in ex.display_log:
                        self._result.log.append(line)
                        if self.verbose:
                            print(line)

            if not changed:
                break
        else:
            raise SimError(
                f"Combinational loop detected: did not converge in {MAX_DELTA} delta cycles"
            )

    # ------------------------------------------------------------------
    # Internal: write a signal value with masking
    # ------------------------------------------------------------------

    def _write(self, name: str, raw_val: int) -> None:
        w = self._widths.get(name, 1)
        mask = (1 << w) - 1
        self.signals[name] = raw_val & mask

    # ------------------------------------------------------------------
    # Overrides for external configuration
    # ------------------------------------------------------------------

    def set_clk(self, name: str) -> None:
        self._clk = name

    def set_rst(self, name: str, active_low: bool = True) -> None:
        self._rst = name
        self._rst_active_low = active_low
