"""AST node definitions for the SystemVerilog subset supported by deltasim.

Covers synthesizable SV and basic TB constructs: modules, ports, parameters,
logic/wire/reg, assign, always_ff/always_comb/always, initial, if/case,
blocking/non-blocking assignments, basic operators, bit-widths, concatenation,
time delays (#n), forever/repeat/for/while loops, and wait-event statements.

Struct/enum/interface are excluded from M1-M2 but space is reserved for
future extension via the `ModuleItem` union type.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

@dataclass
class Module:
    name: str
    params: List[ParamDecl]
    ports: List[PortDecl]
    items: List[ModuleItem]
    source_file: str = ""


# ---------------------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------------------

@dataclass
class Dims:
    """Packed dimension [hi:lo]."""
    hi: Expr
    lo: Expr


@dataclass
class ParamDecl:
    name: str
    type_kw: Optional[str]   # 'integer', 'logic', 'int', None
    dims: Optional[Dims]
    value: Expr
    is_local: bool = False   # localparam


@dataclass
class PortDecl:
    direction: str            # 'input' | 'output' | 'inout'
    net_type: Optional[str]   # 'logic' | 'wire' | 'reg' | None
    dims: Optional[Dims]
    name: str
    signed: bool = False


@dataclass
class NetDecl:
    """Internal wire/logic/reg declaration inside a module body."""
    net_type: str             # 'logic' | 'wire' | 'reg'
    dims: Optional[Dims]
    names: List[str]
    signed: bool = False


# ---------------------------------------------------------------------------
# Module body items  (ModuleItem = union)
# ---------------------------------------------------------------------------

@dataclass
class AssignItem:
    """Continuous assignment: assign lhs = rhs;"""
    lhs: Expr
    rhs: Expr


@dataclass
class AlwaysItem:
    """always / always_ff / always_comb / always_latch block."""
    keyword: str              # 'always' | 'always_ff' | 'always_comb' | 'always_latch'
    sensitivity: Optional[SensitivityList]
    body: Stmt


@dataclass
class InitialItem:
    """initial block (skipped in synthesis, kept for future TB gen)."""
    body: Stmt


# ModuleItem is a union – extend here for struct/generate/etc. in future
ModuleItem = NetDecl | AssignItem | AlwaysItem | InitialItem | ParamDecl


# ---------------------------------------------------------------------------
# Sensitivity list
# ---------------------------------------------------------------------------

@dataclass
class SensitivityItem:
    edge: Optional[str]   # 'posedge' | 'negedge' | None (level/any)
    signal: Expr


@dataclass
class SensitivityList:
    items: List[SensitivityItem]   # empty = @(*) implicit


# ---------------------------------------------------------------------------
# Statements  (Stmt = union)
# ---------------------------------------------------------------------------

@dataclass
class Block:
    """begin … end"""
    stmts: List[Stmt]


@dataclass
class IfStmt:
    cond: Expr
    then_body: Stmt
    else_body: Optional[Stmt]


@dataclass
class CaseItem:
    exprs: Optional[List[Expr]]   # None → default
    body: Stmt


@dataclass
class CaseStmt:
    keyword: str   # 'case' | 'casex' | 'casez'
    expr: Expr
    items: List[CaseItem]


@dataclass
class BlockingAssign:
    lhs: Expr
    rhs: Expr


@dataclass
class NonBlockingAssign:
    lhs: Expr
    rhs: Expr


@dataclass
class NullStmt:
    pass


@dataclass
class SysTaskStmt:
    """$display, $monitor, $finish, etc."""
    name: str
    args: List[Expr]


# ---------- TB / procedural control statements (M2) ----------

@dataclass
class TimeDelay:
    """#expression [statement]  —  advance simulation time."""
    delay: Expr
    body: Optional["Stmt"] = None


@dataclass
class ForeverStmt:
    """forever statement"""
    body: "Stmt"


@dataclass
class RepeatStmt:
    """repeat (count) statement"""
    count: Expr
    body: "Stmt"


@dataclass
class WaitEventStmt:
    """@(sensitivity) [statement]  —  wait for an edge/event."""
    sensitivity: SensitivityList
    body: Optional["Stmt"] = None


@dataclass
class ForStmt:
    """for (init; cond; update) body"""
    init: "Stmt"
    cond: Expr
    update: "Stmt"
    body: "Stmt"


@dataclass
class WhileStmt:
    """while (cond) body"""
    cond: Expr
    body: "Stmt"


Stmt = (Block | IfStmt | CaseStmt | BlockingAssign | NonBlockingAssign
        | NullStmt | SysTaskStmt
        | TimeDelay | ForeverStmt | RepeatStmt | WaitEventStmt
        | ForStmt | WhileStmt)


# ---------------------------------------------------------------------------
# Module-body items: hierarchy + generate + typedef  (M3)
# ---------------------------------------------------------------------------

@dataclass
class PortConn:
    """One port connection in a module instantiation.

    Named:      .port_name(expr)  →  port='port_name', signal=expr
    Positional: expr              →  port=None, signal=expr
    Unconnected: .port_name()    →  port='port_name', signal=None
    """
    port: Optional[str]
    signal: Optional[Expr]


@dataclass
class InstDecl:
    """Module instantiation: ModName [#(params)] instName (ports);"""
    module_name: str
    inst_name: str
    param_overrides: List[Tuple[Optional[str], Expr]]  # [(port_name, value), ...]
    port_conns: List[PortConn]


@dataclass
class GenVarDecl:
    """genvar i, j;"""
    names: List[str]


@dataclass
class GenerateFor:
    """generate for (genvar i = lo; i < hi; i++) begin ... end"""
    var: str
    init: Expr
    cond: Expr
    update: Expr            # full expression: i = i + 1 or i++
    body: List[object]      # list of ModuleItem (or sub-generate items)


@dataclass
class GenerateIf:
    """generate if (cond) begin ... end [else begin ... end]"""
    cond: Expr
    then_items: List[object]
    else_items: Optional[List[object]] = None


@dataclass
class GenerateBlock:
    """generate ... endgenerate wrapper."""
    items: List[object]


# ---------- Typedef / enum / struct  (M3) ----------

@dataclass
class EnumMember:
    name: str
    value: Optional[Expr]   # explicit value; None → auto-increment


@dataclass
class EnumTypeDef:
    base_kw: Optional[str]  # 'logic', 'bit', 'int', None (implicit)
    dims: Optional[Dims]    # e.g., [1:0] for 2-bit enum
    members: List[EnumMember]


@dataclass
class StructField:
    net_type: Optional[str]
    dims: Optional[Dims]
    name: str


@dataclass
class StructTypeDef:
    packed: bool
    fields: List[StructField]


@dataclass
class TypedefDecl:
    """typedef <type_def> <name>;"""
    type_def: "EnumTypeDef | StructTypeDef | str"  # str = alias to existing type
    name: str


@dataclass
class ImportDecl:
    """import pkg::*;  or  import pkg::name;"""
    pkg: str
    item: Optional[str]   # None → wildcard (*)


# ---------------------------------------------------------------------------
# Expressions  (Expr = union)
# ---------------------------------------------------------------------------

@dataclass
class NumberLiteral:
    value: int
    width: Optional[int]   # None = unsized (treated as 32-bit in context)
    signed: bool = False


@dataclass
class FillLiteral:
    """'0 | '1 | 'x | 'z  — width inherited from assignment target."""
    fill: str   # '0', '1', 'x', 'z'


@dataclass
class StringLiteral:
    text: str


@dataclass
class Identifier:
    name: str


@dataclass
class BinOp:
    op: str
    left: Expr
    right: Expr


@dataclass
class UnaryOp:
    op: str
    operand: Expr


@dataclass
class Conditional:
    """cond ? then_expr : else_expr"""
    cond: Expr
    then_expr: Expr
    else_expr: Expr


@dataclass
class BitSelect:
    """base[index]"""
    base: Expr
    index: Expr


@dataclass
class PartSelect:
    """base[hi:lo]"""
    base: Expr
    hi: Expr
    lo: Expr


@dataclass
class Concat:
    """{a, b, c}"""
    parts: List[Expr]


@dataclass
class Replicate:
    """{n{val}}"""
    count: Expr
    value: Expr


@dataclass
class SysFunc:
    """$clog2(...), $signed(...), etc."""
    name: str
    args: List[Expr]


Expr = (
    NumberLiteral | FillLiteral | StringLiteral | Identifier |
    BinOp | UnaryOp | Conditional |
    BitSelect | PartSelect | Concat | Replicate | SysFunc
)
