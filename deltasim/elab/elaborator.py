"""Elaborator: transforms a parsed Module AST into an ElabDesign.

Responsibilities
----------------
1. Evaluate parameter default values and override them with user-supplied
   values (e.g. ``--param WIDTH=4`` from the CLI).
2. Compute the bit-width of every port and internal signal by evaluating
   constant dimension expressions such as ``[WIDTH-1:0]``.
3. Collect the three kinds of concurrent processes:
   * ``AssignProc``   – continuous assignments (``assign lhs = rhs``)
   * ``SeqProc``      – registered processes (``always_ff / always``)
   * ``CombProc``     – combinational processes (``always_comb``)
4. Build a flat ``ElabDesign`` that the simulation engine consumes.

Extension points
----------------
* ``struct`` / ``enum`` typedefs → currently unsupported; a future pass
  would expand them before this stage.
* Hierarchical instantiation → a future pass would inline sub-modules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from deltasim.parser.ast_nodes import (
    Module, ParamDecl, PortDecl, NetDecl,
    AssignItem, AlwaysItem, InitialItem,
    SensitivityList, SensitivityItem,
    NumberLiteral, FillLiteral, Identifier, BinOp, UnaryOp,
    Conditional, BitSelect, PartSelect, Concat, Replicate, SysFunc,
    Dims,
)


# ---------------------------------------------------------------------------
# ElabError
# ---------------------------------------------------------------------------

class ElabError(Exception):
    pass


# ---------------------------------------------------------------------------
# Signal metadata
# ---------------------------------------------------------------------------

@dataclass
class SignalInfo:
    width: int                   # bit-width (≥1)
    direction: Optional[str]     # 'input' | 'output' | 'inout' | None (internal)
    signed: bool = False


# ---------------------------------------------------------------------------
# Process representations
# ---------------------------------------------------------------------------

@dataclass
class AssignProc:
    """Continuous assignment: ``assign lhs = rhs``."""
    lhs: object   # Expr AST node
    rhs: object   # Expr AST node


@dataclass
class SeqProc:
    """Sequential process from always_ff / always @(edge …)."""
    sensitivity: SensitivityList
    body: object   # Stmt AST node


@dataclass
class CombProc:
    """Combinational process from always_comb / always @(*)."""
    body: object   # Stmt AST node


# ---------------------------------------------------------------------------
# ElabDesign
# ---------------------------------------------------------------------------

@dataclass
class ElabDesign:
    module_name: str
    params: Dict[str, int]            # resolved parameter values
    signals: Dict[str, SignalInfo]    # all signals (ports + internals)
    assign_procs: List[AssignProc]
    seq_procs: List[SeqProc]
    comb_procs: List[CombProc]
    initial_blocks: List[object] = field(default_factory=list)  # Stmt AST nodes (M2)


# ---------------------------------------------------------------------------
# Constant expression evaluator (used during elaboration only)
# ---------------------------------------------------------------------------

def eval_const(expr, params: Dict[str, int]) -> int:
    """Evaluate a compile-time constant expression given parameter values."""
    if isinstance(expr, NumberLiteral):
        return expr.value
    if isinstance(expr, FillLiteral):
        return 0 if expr.fill == "0" else -1
    if isinstance(expr, Identifier):
        name = expr.name
        if name in params:
            return params[name]
        raise ElabError(f"Unknown identifier in constant expression: {name!r}")
    if isinstance(expr, BinOp):
        l = eval_const(expr.left, params)
        r = eval_const(expr.right, params)
        ops = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a // b if b != 0 else 0,
            "%": lambda a, b: a % b if b != 0 else 0,
            "<<": lambda a, b: a << b,
            ">>": lambda a, b: a >> b,
            "**": lambda a, b: a ** b,
            "&": lambda a, b: a & b,
            "|": lambda a, b: a | b,
            "^": lambda a, b: a ^ b,
            "==": lambda a, b: int(a == b),
            "!=": lambda a, b: int(a != b),
            "<":  lambda a, b: int(a < b),
            ">":  lambda a, b: int(a > b),
            "<=": lambda a, b: int(a <= b),
            ">=": lambda a, b: int(a >= b),
        }
        fn = ops.get(expr.op)
        if fn is None:
            raise ElabError(f"Unsupported operator in constant expression: {expr.op!r}")
        return fn(l, r)
    if isinstance(expr, UnaryOp):
        v = eval_const(expr.operand, params)
        if expr.op == "-":
            return -v
        if expr.op == "+":
            return v
        if expr.op == "~":
            return ~v
        if expr.op == "!":
            return int(not v)
        raise ElabError(f"Unsupported unary op in constant expr: {expr.op!r}")
    if isinstance(expr, Conditional):
        c = eval_const(expr.cond, params)
        return eval_const(expr.then_expr if c else expr.else_expr, params)
    if isinstance(expr, SysFunc):
        if expr.name == "clog2":
            v = eval_const(expr.args[0], params)
            return max(1, math.ceil(math.log2(max(v, 2))))
        if expr.name in ("signed", "unsigned"):
            return eval_const(expr.args[0], params)
        raise ElabError(f"Unsupported system function in constant expr: ${expr.name}")
    raise ElabError(f"Cannot evaluate constant expression: {type(expr).__name__}")


def _dims_to_width(dims: Optional[Dims], params: Dict[str, int]) -> int:
    """Return bit-width from a Dims node (hi:lo inclusive)."""
    if dims is None:
        return 1
    hi = eval_const(dims.hi, params)
    lo = eval_const(dims.lo, params)
    return max(hi, lo) - min(hi, lo) + 1


# ---------------------------------------------------------------------------
# Main elaboration entry point
# ---------------------------------------------------------------------------

def elaborate(module: Module, param_overrides: Optional[Dict[str, int]] = None) -> ElabDesign:
    """Elaborate *module* with optional parameter overrides.

    Parameters
    ----------
    module:
        Parsed Module AST.
    param_overrides:
        Dict of parameter name → integer value.  Overrides the module's
        default values.  For example ``{"WIDTH": 4}``.
    """
    overrides = param_overrides or {}

    # ------------------------------------------------------------------
    # 1. Resolve parameters
    # ------------------------------------------------------------------
    params: Dict[str, int] = {}
    for decl in module.params:
        if decl.name in overrides:
            params[decl.name] = overrides[decl.name]
        else:
            try:
                params[decl.name] = eval_const(decl.value, params)
            except ElabError as e:
                raise ElabError(
                    f"Cannot resolve parameter '{decl.name}': {e}"
                ) from e

    # Also collect inline parameter declarations from module body
    from deltasim.parser.ast_nodes import ParamDecl as AstParamDecl
    for item in module.items:
        if isinstance(item, AstParamDecl):
            if item.name not in overrides and item.name not in params:
                try:
                    params[item.name] = eval_const(item.value, params)
                except ElabError:
                    pass

    # ------------------------------------------------------------------
    # 2. Build signal table from ports
    # ------------------------------------------------------------------
    signals: Dict[str, SignalInfo] = {}
    for port in module.ports:
        w = _dims_to_width(port.dims, params)
        signals[port.name] = SignalInfo(
            width=w, direction=port.direction, signed=port.signed
        )

    # ------------------------------------------------------------------
    # 3. Process module body items
    # ------------------------------------------------------------------
    assign_procs: List[AssignProc] = []
    seq_procs_list: List[SeqProc] = []
    comb_procs_list: List[CombProc] = []
    initial_blocks_list: List[object] = []

    from deltasim.parser.ast_nodes import (
        AssignItem, AlwaysItem, InitialItem, NetDecl as AstNetDecl
    )

    for item in module.items:
        if isinstance(item, AstNetDecl):
            w = _dims_to_width(item.dims, params)
            for name in item.names:
                if name not in signals:
                    signals[name] = SignalInfo(
                        width=w, direction=None, signed=item.signed
                    )

        elif isinstance(item, AssignItem):
            assign_procs.append(AssignProc(lhs=item.lhs, rhs=item.rhs))

        elif isinstance(item, AlwaysItem):
            kw = item.keyword
            if kw == "always_comb" or (
                kw in ("always", "always_latch") and
                item.sensitivity is not None and
                len(item.sensitivity.items) == 0
            ):
                comb_procs_list.append(CombProc(body=item.body))
            elif item.sensitivity is None:
                if kw == "always":
                    # always without sensitivity (e.g., always #5 clk = ~clk)
                    # → treat as initial forever loop for time-driven processes
                    from deltasim.parser.ast_nodes import ForeverStmt
                    initial_blocks_list.append(ForeverStmt(body=item.body))
                else:
                    # always_ff without sensitivity → comb (unusual but valid)
                    comb_procs_list.append(CombProc(body=item.body))
            else:
                seq_procs_list.append(
                    SeqProc(sensitivity=item.sensitivity, body=item.body)
                )

        elif isinstance(item, InitialItem):
            initial_blocks_list.append(item.body)  # collect for M2 simulation

        elif isinstance(item, AstParamDecl):
            pass

    # ------------------------------------------------------------------
    # 4. Infer missing internal signal widths from assigns/body
    # ------------------------------------------------------------------
    # (Simplistic: any identifier referenced on RHS that's not in signals
    #  gets a default width of 1.  Proper type inference is future work.)

    return ElabDesign(
        module_name=module.name,
        params=params,
        signals=signals,
        assign_procs=assign_procs,
        seq_procs=seq_procs_list,
        comb_procs=comb_procs_list,
        initial_blocks=initial_blocks_list,
    )
