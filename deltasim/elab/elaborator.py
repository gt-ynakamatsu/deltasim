"""Elaborator: transforms a parsed Module AST into an ElabDesign.

M3 additions
------------
* Module catalog: ``elaborate()`` accepts a dict of all parsed modules so
  that hierarchical instantiations can be resolved recursively.
* Hierarchical flattening: ``InstDecl`` items are elaborated by renaming
  every identifier in the sub-module's processes and merging them into the
  parent's flat process lists.  Port connections are expressed as ``assign``
  statements between parent and child signals.
* Generate unrolling: ``GenerateFor`` loops are unrolled at compile time;
  ``GenerateIf`` selects the live branch.
* typedef enum: enum member names are added to the ``params`` dict so they
  can be used as integer constants in expressions.
* Struct packed (partial): width is computed from total field bits; field
  access via ``.name`` is not yet supported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from deltasim.parser.ast_nodes import (
    Module, ParamDecl, PortDecl, NetDecl, Dims,
    AssignItem, AlwaysItem, InitialItem,
    SensitivityList, SensitivityItem,
    Block, IfStmt, CaseStmt, CaseItem,
    BlockingAssign, NonBlockingAssign, NullStmt, SysTaskStmt,
    TimeDelay, ForeverStmt, RepeatStmt, WaitEventStmt, ForStmt, WhileStmt,
    InstDecl, PortConn, GenVarDecl,
    GenerateBlock, GenerateFor, GenerateIf,
    TypedefDecl, EnumTypeDef, StructTypeDef, ImportDecl,
    NumberLiteral, FillLiteral, Identifier,
    BinOp, UnaryOp, Conditional, BitSelect, PartSelect, Concat, Replicate,
    SysFunc,
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
    width: int
    direction: Optional[str]     # 'input' | 'output' | 'inout' | None
    signed: bool = False


# ---------------------------------------------------------------------------
# Process representations
# ---------------------------------------------------------------------------

@dataclass
class AssignProc:
    lhs: object
    rhs: object


@dataclass
class SeqProc:
    sensitivity: SensitivityList
    body: object


@dataclass
class CombProc:
    body: object


# ---------------------------------------------------------------------------
# ElabDesign
# ---------------------------------------------------------------------------

@dataclass
class ElabDesign:
    module_name: str
    params: Dict[str, int]
    signals: Dict[str, SignalInfo]
    assign_procs: List[AssignProc]
    seq_procs: List[SeqProc]
    comb_procs: List[CombProc]
    initial_blocks: List[object] = field(default_factory=list)
    # M4 additions
    struct_fields: Dict[str, list] = field(default_factory=dict)   # type_name → [StructFieldEntry]
    signal_types: Dict[str, str] = field(default_factory=dict)     # sig_name → type_name
    array_decls: Dict[str, tuple] = field(default_factory=dict)    # array_name → (depth, elem_width)
    typedefs: Dict[str, int] = field(default_factory=dict)         # type_name → bit-width


# ---------------------------------------------------------------------------
# AST expression renaming  (used by hierarchical flattening)
# ---------------------------------------------------------------------------

def _re(expr, m: Dict[str, str]):
    """Rename identifiers in expr according to mapping m."""
    if isinstance(expr, Identifier):
        return Identifier(m.get(expr.name, expr.name))
    if isinstance(expr, (NumberLiteral, FillLiteral)):
        return expr
    if isinstance(expr, BinOp):
        return BinOp(expr.op, _re(expr.left, m), _re(expr.right, m))
    if isinstance(expr, UnaryOp):
        return UnaryOp(expr.op, _re(expr.operand, m))
    if isinstance(expr, Conditional):
        return Conditional(_re(expr.cond, m), _re(expr.then_expr, m), _re(expr.else_expr, m))
    if isinstance(expr, BitSelect):
        return BitSelect(_re(expr.base, m), _re(expr.index, m))
    if isinstance(expr, PartSelect):
        return PartSelect(_re(expr.base, m), _re(expr.hi, m), _re(expr.lo, m))
    if isinstance(expr, Concat):
        return Concat([_re(p, m) for p in expr.parts])
    if isinstance(expr, Replicate):
        return Replicate(_re(expr.count, m), _re(expr.value, m))
    if isinstance(expr, SysFunc):
        return SysFunc(expr.name, [_re(a, m) for a in expr.args])
    return expr


def _rs(stmt, m: Dict[str, str]):
    """Rename identifiers in stmt according to mapping m."""
    if isinstance(stmt, Block):
        return Block([_rs(s, m) for s in stmt.stmts])
    if isinstance(stmt, IfStmt):
        return IfStmt(_re(stmt.cond, m), _rs(stmt.then_body, m),
                      _rs(stmt.else_body, m) if stmt.else_body else None)
    if isinstance(stmt, CaseStmt):
        items = [CaseItem(
            [_re(e, m) for e in item.exprs] if item.exprs else None,
            _rs(item.body, m)
        ) for item in stmt.items]
        return CaseStmt(stmt.keyword, _re(stmt.expr, m), items)
    if isinstance(stmt, BlockingAssign):
        return BlockingAssign(_re(stmt.lhs, m), _re(stmt.rhs, m))
    if isinstance(stmt, NonBlockingAssign):
        return NonBlockingAssign(_re(stmt.lhs, m), _re(stmt.rhs, m))
    if isinstance(stmt, NullStmt):
        return stmt
    if isinstance(stmt, SysTaskStmt):
        return SysTaskStmt(stmt.name, [_re(a, m) for a in stmt.args])
    if isinstance(stmt, TimeDelay):
        return TimeDelay(_re(stmt.delay, m), _rs(stmt.body, m) if stmt.body else None)
    if isinstance(stmt, ForeverStmt):
        return ForeverStmt(_rs(stmt.body, m))
    if isinstance(stmt, RepeatStmt):
        return RepeatStmt(_re(stmt.count, m), _rs(stmt.body, m))
    if isinstance(stmt, WaitEventStmt):
        items = [SensitivityItem(i.edge, _re(i.signal, m)) for i in stmt.sensitivity.items]
        return WaitEventStmt(SensitivityList(items), _rs(stmt.body, m) if stmt.body else None)
    if isinstance(stmt, ForStmt):
        return ForStmt(_rs(stmt.init, m), _re(stmt.cond, m),
                       _rs(stmt.update, m), _rs(stmt.body, m))
    if isinstance(stmt, WhileStmt):
        return WhileStmt(_re(stmt.cond, m), _rs(stmt.body, m))
    return stmt


def _rs_sens(sens: SensitivityList, m: Dict[str, str]) -> SensitivityList:
    return SensitivityList([SensitivityItem(i.edge, _re(i.signal, m)) for i in sens.items])


# ---------------------------------------------------------------------------
# Constant expression evaluator
# ---------------------------------------------------------------------------

def eval_const(expr, params: Dict[str, int]) -> int:
    """Evaluate a compile-time constant expression."""
    if isinstance(expr, NumberLiteral):
        return expr.value
    if isinstance(expr, FillLiteral):
        return 0 if expr.fill == "0" else -1
    if isinstance(expr, Identifier):
        if expr.name in params:
            return params[expr.name]
        raise ElabError(f"Unknown identifier in const expr: {expr.name!r}")
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
            raise ElabError(f"Unsupported op in const expr: {expr.op!r}")
        return fn(l, r)
    if isinstance(expr, UnaryOp):
        v = eval_const(expr.operand, params)
        if expr.op == "-": return -v
        if expr.op == "+": return v
        if expr.op == "~": return ~v
        if expr.op == "!": return int(not v)
        raise ElabError(f"Unsupported unary op in const expr: {expr.op!r}")
    if isinstance(expr, Conditional):
        c = eval_const(expr.cond, params)
        return eval_const(expr.then_expr if c else expr.else_expr, params)
    if isinstance(expr, SysFunc):
        if expr.name == "clog2":
            v = eval_const(expr.args[0], params)
            return max(1, math.ceil(math.log2(max(v, 2))))
        if expr.name in ("signed", "unsigned"):
            return eval_const(expr.args[0], params)
        raise ElabError(f"Unsupported sys func in const expr: ${expr.name}")
    raise ElabError(f"Cannot evaluate const expr: {type(expr).__name__}")


def _dims_to_width(dims: Optional[Dims], params: Dict[str, int]) -> int:
    if dims is None:
        return 1
    hi = eval_const(dims.hi, params)
    lo = eval_const(dims.lo, params)
    return max(hi, lo) - min(hi, lo) + 1


# ---------------------------------------------------------------------------
# Main elaboration entry point
# ---------------------------------------------------------------------------

def elaborate(
    module: Module,
    param_overrides: Optional[Dict[str, int]] = None,
    catalog: Optional[Dict[str, "Module"]] = None,
) -> ElabDesign:
    """Elaborate *module* with optional parameter overrides and module catalog.

    Parameters
    ----------
    module:
        Top module AST.
    param_overrides:
        External parameter values (e.g. from CLI ``--param WIDTH=4``).
    catalog:
        Dict of all parsed modules, keyed by name.  Required to resolve
        hierarchical instantiations.
    """
    overrides = param_overrides or {}
    cat = catalog or {}

    # ------------------------------------------------------------------
    # 1. Resolve parameters + typedef enum members → params dict
    # ------------------------------------------------------------------
    params: Dict[str, int] = {}
    typedefs: Dict[str, int] = {}  # type_name → bit-width

    for decl in module.params:
        if decl.name in overrides:
            params[decl.name] = overrides[decl.name]
        else:
            try:
                params[decl.name] = eval_const(decl.value, params)
            except ElabError as e:
                raise ElabError(f"Cannot resolve param '{decl.name}': {e}") from e

    # Pre-scan module body for parameters and typedefs
    for item in module.items:
        if isinstance(item, ParamDecl):
            if item.name not in overrides and item.name not in params:
                try:
                    params[item.name] = eval_const(item.value, params)
                except ElabError:
                    pass
        elif isinstance(item, TypedefDecl):
            _resolve_typedef(item, params, typedefs)
        elif isinstance(item, ImportDecl):
            _process_import(item, cat, params, typedefs)

    # ------------------------------------------------------------------
    # 2. Build signal table from ports
    # ------------------------------------------------------------------
    signals: Dict[str, SignalInfo] = {}
    for port in module.ports:
        w = _resolve_width(port.net_type, port.dims, params, typedefs)
        signed = port.signed or _is_signed_type(port.net_type)
        signals[port.name] = SignalInfo(width=w, direction=port.direction, signed=signed)

    # ------------------------------------------------------------------
    # 3. Process module body items
    # ------------------------------------------------------------------
    procs = dict(assign=[], seq=[], comb=[], initial=[])
    struct_fields: Dict[str, list] = {}
    signal_types: Dict[str, str] = {}
    array_decls: Dict[str, tuple] = {}

    _process_items(module.items, params, typedefs, signals, procs, cat,
                   struct_fields, signal_types, array_decls)

    return ElabDesign(
        module_name=module.name,
        params=params,
        signals=signals,
        assign_procs=procs["assign"],
        seq_procs=procs["seq"],
        comb_procs=procs["comb"],
        initial_blocks=procs["initial"],
        struct_fields=struct_fields,
        signal_types=signal_types,
        array_decls=array_decls,
        typedefs=typedefs,
    )


# ---------------------------------------------------------------------------
# Internal: process a list of module items
# ---------------------------------------------------------------------------

def _process_items(items, params, typedefs, signals, procs, cat,
                   struct_fields=None, signal_types=None, array_decls=None):
    for item in items:
        _process_item(item, params, typedefs, signals, procs, cat,
                      struct_fields, signal_types, array_decls)


def _process_item(item, params, typedefs, signals, procs, cat,
                  struct_fields=None, signal_types=None, array_decls=None):
    from deltasim.parser.ast_nodes import ParamDecl as AstParamDecl

    if isinstance(item, NetDecl):
        w = _resolve_width(item.net_type, item.dims, params, typedefs)
        signed = item.signed or _is_signed_type(item.net_type)
        # Check for unpacked array dimension: unpacked_dims stored in item.unpacked_dims (M4)
        unpacked_dims = getattr(item, 'unpacked_dims', None)
        for name in item.names:
            if name not in signals:
                if unpacked_dims is not None:
                    # Array declaration
                    try:
                        hi = eval_const(unpacked_dims.hi, params)
                        lo = eval_const(unpacked_dims.lo, params)
                    except ElabError:
                        hi, lo = 15, 0
                    depth = abs(hi - lo) + 1
                    if array_decls is not None:
                        array_decls[name] = (depth, w)
                    # Register as a signal with width=w (element width)
                    signals[name] = SignalInfo(width=w, direction=None, signed=signed)
                else:
                    signals[name] = SignalInfo(width=w, direction=None, signed=signed)
                    # Track signal type for struct field access
                    if item.net_type and item.net_type in typedefs and signal_types is not None:
                        signal_types[name] = item.net_type

    elif isinstance(item, AssignItem):
        procs["assign"].append(AssignProc(lhs=item.lhs, rhs=item.rhs))

    elif isinstance(item, AlwaysItem):
        kw = item.keyword
        if kw == "always_comb" or (
            kw in ("always", "always_latch") and
            item.sensitivity is not None and
            len(item.sensitivity.items) == 0
        ):
            procs["comb"].append(CombProc(body=item.body))
        elif item.sensitivity is None:
            if kw == "always":
                procs["initial"].append(ForeverStmt(body=item.body))
            else:
                procs["comb"].append(CombProc(body=item.body))
        else:
            procs["seq"].append(SeqProc(sensitivity=item.sensitivity, body=item.body))

    elif isinstance(item, InitialItem):
        procs["initial"].append(item.body)

    elif isinstance(item, InstDecl):
        _flatten_instance(item, params, typedefs, signals, procs, cat,
                          struct_fields, signal_types, array_decls)

    elif isinstance(item, GenerateBlock):
        _process_items(item.items, params, typedefs, signals, procs, cat,
                       struct_fields, signal_types, array_decls)

    elif isinstance(item, GenerateFor):
        _elaborate_gen_for(item, params, typedefs, signals, procs, cat,
                           struct_fields, signal_types, array_decls)

    elif isinstance(item, GenerateIf):
        _elaborate_gen_if(item, params, typedefs, signals, procs, cat,
                          struct_fields, signal_types, array_decls)

    elif isinstance(item, TypedefDecl):
        _resolve_typedef(item, params, typedefs, struct_fields)

    elif isinstance(item, (AstParamDecl, GenVarDecl, ImportDecl)):
        pass  # already handled or skip


# ---------------------------------------------------------------------------
# Width resolution (handles typedef names)
# ---------------------------------------------------------------------------

def _resolve_width(
    net_type: Optional[str],
    dims: Optional[Dims],
    params: Dict[str, int],
    typedefs: Dict[str, int],
) -> int:
    """Compute bit-width for a signal declaration."""
    if dims is not None:
        return _dims_to_width(dims, params)
    if net_type and net_type in typedefs:
        return typedefs[net_type]
    # Built-in integer types
    if net_type in ("int", "integer"):
        return 32   # 32-bit signed
    if net_type == "byte":
        return 8
    if net_type == "shortint":
        return 16
    if net_type == "longint":
        return 64
    return 1  # 1-bit default for logic/wire/reg/bit/etc.


def _is_signed_type(net_type: Optional[str]) -> bool:
    """Return True if the given type keyword implies signed semantics."""
    return net_type in ("int", "integer", "shortint", "longint", "byte", "signed")


# ---------------------------------------------------------------------------
# typedef resolution
# ---------------------------------------------------------------------------

def _resolve_typedef(typedef_decl: TypedefDecl, params: Dict[str, int],
                     typedefs: Dict[str, int],
                     struct_fields: Optional[Dict[str, list]] = None):
    from deltasim.sim.context import StructFieldEntry
    if isinstance(typedef_decl.type_def, EnumTypeDef):
        enum_def = typedef_decl.type_def
        # Compute width
        if enum_def.dims is not None:
            width = _dims_to_width(enum_def.dims, params)
        elif enum_def.base_kw in ("int", "integer"):
            width = 32
        else:
            # Auto width from number of members
            n = len(enum_def.members)
            width = max(1, math.ceil(math.log2(max(n, 2))))
        typedefs[typedef_decl.name] = width
        # Add member values to params
        next_val = 0
        for member in enum_def.members:
            if member.value is not None:
                try:
                    next_val = eval_const(member.value, params)
                except ElabError:
                    pass
            params[member.name] = next_val
            next_val += 1

    elif isinstance(typedef_decl.type_def, StructTypeDef):
        struct_def = typedef_decl.type_def
        if struct_def.packed:
            # Compute total width and field offsets (MSB first)
            field_widths = [
                _resolve_width(f.net_type, f.dims, params, typedefs)
                for f in struct_def.fields
            ]
            total = sum(field_widths)
            typedefs[typedef_decl.name] = total
            # Build field entries: fields are packed MSB-first
            if struct_fields is not None:
                entries = []
                bit_offset = total  # start from MSB
                for f, fw in zip(struct_def.fields, field_widths):
                    hi = bit_offset - 1
                    lo = bit_offset - fw
                    entries.append(StructFieldEntry(
                        name=f.name, hi=hi, lo=lo, width=fw
                    ))
                    bit_offset -= fw
                struct_fields[typedef_decl.name] = entries
        else:
            total = sum(
                _resolve_width(f.net_type, f.dims, params, typedefs)
                for f in struct_def.fields
            )
            typedefs[typedef_decl.name] = max(total, 1)

    elif isinstance(typedef_decl.type_def, str):
        # Alias to existing type
        existing = typedef_decl.type_def
        if existing in typedefs:
            typedefs[typedef_decl.name] = typedefs[existing]
        elif existing in ("int", "integer"):
            typedefs[typedef_decl.name] = 32
        elif existing == "byte":
            typedefs[typedef_decl.name] = 8
        else:
            typedefs[typedef_decl.name] = 1


def _process_import(imp: ImportDecl, cat, params, typedefs):
    """Copy parameters/typedefs from an imported package module."""
    # Look for a 'package' module in the catalog
    pkg_mod = cat.get(imp.pkg)
    if pkg_mod is None:
        return  # package not in catalog – silently skip
    # Extract parameters and typedefs from package body
    pkg_params: Dict[str, int] = {}
    pkg_types: Dict[str, int] = {}
    for item in pkg_mod.items:
        if isinstance(item, ParamDecl):
            try:
                pkg_params[item.name] = eval_const(item.value, pkg_params)
            except ElabError:
                pass
        elif isinstance(item, TypedefDecl):
            _resolve_typedef(item, pkg_params, pkg_types)
    if imp.item is None:  # wildcard import
        params.update(pkg_params)
        typedefs.update(pkg_types)
    else:
        if imp.item in pkg_params:
            params[imp.item] = pkg_params[imp.item]
        if imp.item in pkg_types:
            typedefs[imp.item] = pkg_types[imp.item]


# ---------------------------------------------------------------------------
# generate unrolling
# ---------------------------------------------------------------------------

def _elaborate_gen_for(gen_for: GenerateFor, params, typedefs, signals, procs, cat,
                       struct_fields=None, signal_types=None, array_decls=None):
    var = gen_for.var
    i = eval_const(gen_for.init, params)
    limit = 10_000
    while limit > 0:
        new_params = dict(params)
        new_params[var] = i
        cond = eval_const(gen_for.cond, new_params)
        if not cond:
            break
        _process_items(gen_for.body, new_params, typedefs, signals, procs, cat,
                       struct_fields, signal_types, array_decls)
        # Evaluate update
        upd = gen_for.update
        if isinstance(upd, BinOp) and upd.op == "=":
            # i = expr
            i = eval_const(upd.right, new_params)
        else:
            try:
                i = eval_const(upd, new_params)
            except ElabError:
                i += 1  # default increment
        limit -= 1


def _elaborate_gen_if(gen_if: GenerateIf, params, typedefs, signals, procs, cat,
                      struct_fields=None, signal_types=None, array_decls=None):
    try:
        cond = eval_const(gen_if.cond, params)
    except ElabError:
        cond = 0
    if cond:
        _process_items(gen_if.then_items, params, typedefs, signals, procs, cat,
                       struct_fields, signal_types, array_decls)
    elif gen_if.else_items:
        _process_items(gen_if.else_items, params, typedefs, signals, procs, cat,
                       struct_fields, signal_types, array_decls)


# ---------------------------------------------------------------------------
# Hierarchical instance flattening
# ---------------------------------------------------------------------------

def _flatten_instance(
    inst: InstDecl,
    parent_params: Dict[str, int],
    parent_typedefs: Dict[str, int],
    parent_signals: Dict[str, SignalInfo],
    parent_procs: Dict,
    cat: Dict[str, "Module"],
    parent_struct_fields=None,
    parent_signal_types=None,
    parent_array_decls=None,
):
    if inst.module_name not in cat:
        # Unknown module — skip gracefully (external black-box)
        return

    sub_mod = cat[inst.module_name]

    # Compute parameter values for this instance
    sub_param_overrides: Dict[str, int] = {}
    param_decls = list(sub_mod.params)
    for i, (port_name, val_expr) in enumerate(inst.param_overrides):
        try:
            v = eval_const(val_expr, parent_params)
        except ElabError:
            continue
        if port_name is not None:
            sub_param_overrides[port_name] = v
        elif i < len(param_decls):
            sub_param_overrides[param_decls[i].name] = v

    # Recursively elaborate sub-module
    sub_design = elaborate(sub_mod, param_overrides=sub_param_overrides, catalog=cat)

    # Build signal name mapping:
    #   child_port_name  →  parent_signal_name (from port connections)
    #   child_internal   →  inst.inst_name + "." + child_internal
    inst_prefix = inst.inst_name + "."
    mapping: Dict[str, str] = {}

    # Port connections
    port_decls = sub_mod.ports
    for i, conn in enumerate(inst.port_conns):
        if conn.port is not None:
            port_name = conn.port
        elif i < len(port_decls):
            port_name = port_decls[i].name
        else:
            continue
        if conn.signal is None:
            continue  # unconnected
        # Resolve parent signal name from connection expression
        if isinstance(conn.signal, Identifier):
            parent_sig = conn.signal.name
        else:
            # Complex expression: create a temporary signal
            parent_sig = f"__conn_{inst.inst_name}_{port_name}"
            w = sub_design.signals.get(port_name, SignalInfo(1, None)).width
            parent_signals[parent_sig] = SignalInfo(width=w, direction=None)
            # Create assign: parent_sig = conn.signal (for inputs)
            #               conn.signal = parent_sig (for outputs — tricky)
            # Simplified: just use parent_sig as proxy
        mapping[port_name] = parent_sig

    # Internal signals of sub-module → prefixed names
    for sig_name, info in sub_design.signals.items():
        if info.direction is None:
            new_name = inst_prefix + sig_name
            mapping[sig_name] = new_name
            if new_name not in parent_signals:
                parent_signals[new_name] = SignalInfo(
                    width=info.width, direction=None, signed=info.signed
                )
        else:
            # Port: should already be in mapping via port_conns
            if sig_name not in mapping:
                # Unconnected port: create an internal wire
                new_name = inst_prefix + sig_name
                mapping[sig_name] = new_name
                if new_name not in parent_signals:
                    parent_signals[new_name] = SignalInfo(
                        width=info.width, direction=None
                    )

    # Add renamed processes from sub-module
    for proc in sub_design.assign_procs:
        parent_procs["assign"].append(AssignProc(
            lhs=_re(proc.lhs, mapping),
            rhs=_re(proc.rhs, mapping),
        ))

    for proc in sub_design.seq_procs:
        parent_procs["seq"].append(SeqProc(
            sensitivity=_rs_sens(proc.sensitivity, mapping),
            body=_rs(proc.body, mapping),
        ))

    for proc in sub_design.comb_procs:
        parent_procs["comb"].append(CombProc(body=_rs(proc.body, mapping)))

    for ib_body in sub_design.initial_blocks:
        parent_procs["initial"].append(_rs(ib_body, mapping))
