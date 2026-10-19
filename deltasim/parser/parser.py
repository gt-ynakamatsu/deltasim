"""Recursive-descent parser for the synthesizable SystemVerilog subset.

Parses a token stream (from lexer.tokenize) into a Module AST node.

Design boundaries
-----------------
* Only single-module files are accepted in M1.
* ANSI-style port declarations are supported.
* Non-ANSI (legacy) port style, interfaces, modport, SVA, classes, and
  generate blocks are left for future milestones.
* Expression parsing uses precedence climbing (Pratt-style) so adding
  new operators is trivial.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from deltasim.parser.lexer import Token, tokenize
from deltasim.parser.ast_nodes import (
    Module, ParamDecl, PortDecl, NetDecl, Dims,
    AssignItem, AlwaysItem, InitialItem,
    SensitivityList, SensitivityItem,
    Block, IfStmt, CaseStmt, CaseItem,
    BlockingAssign, NonBlockingAssign, NullStmt, SysTaskStmt,
    NumberLiteral, FillLiteral, StringLiteral, Identifier,
    BinOp, UnaryOp, Conditional, BitSelect, PartSelect, Concat, Replicate,
    SysFunc,
)


# ---------------------------------------------------------------------------
# ParseError
# ---------------------------------------------------------------------------

class ParseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Operator precedence table (higher number = tighter binding)
# ---------------------------------------------------------------------------

_BINOP_PREC = {
    "||":  2,
    "&&":  3,
    "|":   4,
    "^":   5, "^~": 5, "~^": 5,
    "&":   6,
    "==":  7, "!=": 7, "===": 7, "!==": 7,
    "<":   8, ">":  8, "<=":  8, ">=":  8,
    "<<":  9, ">>":  9, "<<<": 9, ">>>": 9,
    "+":  10, "-":  10,
    "*":  11, "/":  11, "%":  11,
}

# Right-associative operators
_RIGHT_ASSOC = {"?"}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class Parser:
    def __init__(self, tokens: List[Token], filename: str = "<string>"):
        self._tokens = tokens
        self._pos = 0
        self._filename = filename

    # ------------------------------------------------------------------
    # Token stream helpers
    # ------------------------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        idx = self._pos + offset
        if idx < len(self._tokens):
            return self._tokens[idx]
        return self._tokens[-1]  # EOF

    def _peek_val(self, offset: int = 0) -> str:
        return self._peek(offset).value

    def _at(self, *values: str) -> bool:
        return self._peek().value in values

    def _at_type(self, *types: str) -> bool:
        return self._peek().type in types

    def _consume(self, expected_value: Optional[str] = None,
                 expected_type: Optional[str] = None) -> Token:
        tok = self._peek()
        if expected_value is not None and tok.value != expected_value:
            self._error(f"expected {expected_value!r}, got {tok.value!r}")
        if expected_type is not None and tok.type != expected_type:
            self._error(f"expected token type {expected_type}, got {tok.type} ({tok.value!r})")
        self._pos += 1
        return tok

    def _try_consume(self, *values: str) -> Optional[Token]:
        if self._peek().value in values:
            return self._consume()
        return None

    def _error(self, msg: str) -> None:
        tok = self._peek()
        raise ParseError(f"{self._filename}:{tok.line}:{tok.col}: {msg}")

    # ------------------------------------------------------------------
    # Top-level: one module per file
    # ------------------------------------------------------------------

    def parse_module(self) -> Module:
        tok = self._consume("module")
        name = self._consume(expected_type="ID").value
        params = self._parse_param_port_list() if self._at("#") else []
        ports = self._parse_port_list()
        self._consume(";")
        items = []
        while not self._at("endmodule", "EOF"):
            items.extend(self._parse_module_item())
        self._consume("endmodule")
        return Module(name=name, params=params, ports=ports, items=items,
                      source_file=self._filename)

    # ------------------------------------------------------------------
    # Parameter port list:  #( parameter A=1, parameter B=8 )
    # ------------------------------------------------------------------

    def _parse_param_port_list(self) -> List[ParamDecl]:
        self._consume("#")
        self._consume("(")
        params: List[ParamDecl] = []
        while not self._at(")"):
            params.append(self._parse_param_decl())
            if not self._try_consume(","):
                break
        self._consume(")")
        return params

    def _parse_param_decl(self, expect_kw: bool = True) -> ParamDecl:
        is_local = False
        if self._at("parameter", "localparam"):
            is_local = self._consume().value == "localparam"
        elif expect_kw:
            self._error("expected 'parameter' or 'localparam'")

        type_kw = None
        if self._at("integer", "int", "logic", "bit", "byte",
                    "shortint", "longint", "signed", "unsigned"):
            type_kw = self._consume().value

        dims: Optional[Dims] = None
        if self._at("["):
            dims = self._parse_dims()

        name = self._consume(expected_type="ID").value
        self._consume("=")
        value = self._parse_expression()
        return ParamDecl(name=name, type_kw=type_kw, dims=dims,
                         value=value, is_local=is_local)

    # ------------------------------------------------------------------
    # Port list:  ( input logic clk, output logic [7:0] count )
    # ------------------------------------------------------------------

    def _parse_port_list(self) -> List[PortDecl]:
        self._consume("(")
        ports: List[PortDecl] = []
        if not self._at(")"):
            ports.append(self._parse_port_decl())
            while self._try_consume(","):
                if self._at(")"):
                    break
                ports.append(self._parse_port_decl())
        self._consume(")")
        return ports

    def _parse_port_decl(self) -> PortDecl:
        if not self._at("input", "output", "inout"):
            self._error(f"expected port direction (input/output/inout), got {self._peek().value!r}")
        direction = self._consume().value

        net_type: Optional[str] = None
        if self._at("logic", "wire", "reg", "bit"):
            net_type = self._consume().value

        signed = bool(self._try_consume("signed"))
        if not signed:
            self._try_consume("unsigned")

        dims: Optional[Dims] = None
        if self._at("["):
            dims = self._parse_dims()

        name = self._consume(expected_type="ID").value
        return PortDecl(direction=direction, net_type=net_type, dims=dims,
                        name=name, signed=signed)

    # ------------------------------------------------------------------
    # Dimensions: [hi : lo]
    # ------------------------------------------------------------------

    def _parse_dims(self) -> Dims:
        self._consume("[")
        hi = self._parse_expression()
        self._consume(":")
        lo = self._parse_expression()
        self._consume("]")
        return Dims(hi=hi, lo=lo)

    # ------------------------------------------------------------------
    # Module body items
    # ------------------------------------------------------------------

    def _parse_module_item(self):
        """Returns a list (usually 1 item) to handle multi-name declarations."""
        if self._at("assign"):
            return [self._parse_assign_item()]
        if self._at("always", "always_ff", "always_comb", "always_latch"):
            return [self._parse_always_item()]
        if self._at("initial"):
            return [self._parse_initial_item()]
        if self._at("parameter", "localparam"):
            return [self._parse_param_decl_item()]
        if self._at("logic", "wire", "reg", "bit", "integer", "int",
                    "supply0", "supply1", "tri", "wand", "wor"):
            return self._parse_net_decl()
        # Skip unknown items (pragmas, etc.) by consuming until ';'
        tok = self._peek()
        # Try to skip gracefully
        if tok.type == "EOF" or tok.value == "endmodule":
            return []
        self._error(f"unexpected token in module body: {tok.value!r}")

    def _parse_assign_item(self) -> AssignItem:
        self._consume("assign")
        lhs = self._parse_lvalue()
        self._consume("=")
        rhs = self._parse_expression()
        self._consume(";")
        return AssignItem(lhs=lhs, rhs=rhs)

    def _parse_always_item(self) -> AlwaysItem:
        keyword = self._consume().value
        sens: Optional[SensitivityList] = None
        if keyword in ("always", "always_ff"):
            sens = self._parse_sensitivity_list()
        elif keyword == "always_comb":
            sens = SensitivityList(items=[])
        elif keyword == "always_latch":
            sens = SensitivityList(items=[])
        body = self._parse_statement()
        return AlwaysItem(keyword=keyword, sensitivity=sens, body=body)

    def _parse_initial_item(self) -> InitialItem:
        self._consume("initial")
        body = self._parse_statement()
        return InitialItem(body=body)

    def _parse_param_decl_item(self) -> ParamDecl:
        decl = self._parse_param_decl(expect_kw=True)
        self._consume(";")
        return decl

    def _parse_net_decl(self) -> List[NetDecl]:
        net_type = self._consume().value
        signed = bool(self._try_consume("signed"))
        if not signed:
            self._try_consume("unsigned")
        dims: Optional[Dims] = None
        if self._at("["):
            dims = self._parse_dims()
        names = [self._consume(expected_type="ID").value]
        while self._try_consume(","):
            names.append(self._consume(expected_type="ID").value)
        self._consume(";")
        return [NetDecl(net_type=net_type, dims=dims, names=names, signed=signed)]

    # ------------------------------------------------------------------
    # Sensitivity list:  @(posedge clk or negedge rst_n) | @(*) | @(*)
    # ------------------------------------------------------------------

    def _parse_sensitivity_list(self) -> SensitivityList:
        self._consume("@")
        # @* or @(*)
        if self._try_consume("*"):
            return SensitivityList(items=[])
        self._consume("(")
        if self._at("*"):
            self._consume("*")
            self._consume(")")
            return SensitivityList(items=[])
        items: List[SensitivityItem] = []
        while not self._at(")"):
            edge: Optional[str] = None
            if self._at("posedge", "negedge"):
                edge = self._consume().value
            sig = self._parse_expression()
            items.append(SensitivityItem(edge=edge, signal=sig))
            # separator: 'or' or ','
            if self._at("or") or self._try_consume(","):
                self._try_consume("or")
            elif not self._at(")"):
                self._try_consume("or")
        self._consume(")")
        return SensitivityList(items=items)

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------

    def _parse_statement(self) -> "Stmt":
        if self._at("begin"):
            return self._parse_block()
        if self._at("if"):
            return self._parse_if()
        if self._at("case", "casex", "casez"):
            return self._parse_case()
        if self._at(";"):
            self._consume()
            return NullStmt()
        # System task statement: $display, $monitor, $finish, …
        if self._at_type("SYSID"):
            return self._parse_sys_task_stmt()
        # Assignment statement (blocking or non-blocking)
        return self._parse_assign_stmt()

    def _parse_block(self) -> Block:
        self._consume("begin")
        stmts = []
        while not self._at("end"):
            if self._at("EOF"):
                self._error("unexpected EOF inside begin…end block")
            stmts.append(self._parse_statement())
        self._consume("end")
        return Block(stmts=stmts)

    def _parse_if(self) -> IfStmt:
        self._consume("if")
        self._consume("(")
        cond = self._parse_expression()
        self._consume(")")
        then_body = self._parse_statement()
        else_body: Optional["Stmt"] = None
        if self._try_consume("else"):
            else_body = self._parse_statement()
        return IfStmt(cond=cond, then_body=then_body, else_body=else_body)

    def _parse_case(self) -> CaseStmt:
        keyword = self._consume().value
        self._consume("(")
        expr = self._parse_expression()
        self._consume(")")
        items: List[CaseItem] = []
        while not self._at("endcase"):
            if self._at("EOF"):
                self._error("unexpected EOF inside case statement")
            items.append(self._parse_case_item())
        self._consume("endcase")
        return CaseStmt(keyword=keyword, expr=expr, items=items)

    def _parse_case_item(self) -> CaseItem:
        if self._at("default"):
            self._consume()
            self._consume(":")
            body = self._parse_statement()
            return CaseItem(exprs=None, body=body)
        exprs = [self._parse_expression()]
        while self._try_consume(","):
            exprs.append(self._parse_expression())
        self._consume(":")
        body = self._parse_statement()
        return CaseItem(exprs=exprs, body=body)

    def _parse_sys_task_stmt(self) -> SysTaskStmt:
        name = self._consume(expected_type="SYSID").value.lstrip("$")
        args = []
        if self._try_consume("("):
            if not self._at(")"):
                args.append(self._parse_expression())
                while self._try_consume(","):
                    args.append(self._parse_expression())
            self._consume(")")
        self._consume(";")
        return SysTaskStmt(name=name, args=args)

    def _parse_assign_stmt(self) -> "Stmt":
        """Parse a blocking (=) or non-blocking (<=) assignment statement."""
        lhs = self._parse_lvalue()
        if self._at("="):
            self._consume()
            rhs = self._parse_expression()
            self._consume(";")
            return BlockingAssign(lhs=lhs, rhs=rhs)
        if self._at("<="):
            self._consume()
            rhs = self._parse_expression()
            self._consume(";")
            return NonBlockingAssign(lhs=lhs, rhs=rhs)
        self._error(
            f"expected '=' or '<=' after lvalue, got {self._peek().value!r}"
        )

    # ------------------------------------------------------------------
    # LValue  (left-hand side of assignment)
    # ------------------------------------------------------------------

    def _parse_lvalue(self) -> "Expr":
        if self._at("{"):
            # Concatenation LHS: {a, b}
            return self._parse_concat()
        name = self._consume(expected_type="ID").value
        expr: "Expr" = Identifier(name=name)
        while self._at("["):
            self._consume("[")
            idx = self._parse_expression()
            if self._at(":"):
                self._consume(":")
                lo = self._parse_expression()
                self._consume("]")
                expr = PartSelect(base=expr, hi=idx, lo=lo)
            else:
                self._consume("]")
                expr = BitSelect(base=expr, index=idx)
        return expr

    # ------------------------------------------------------------------
    # Expressions (Pratt / precedence climbing)
    # ------------------------------------------------------------------

    def _parse_expression(self, min_prec: int = 0) -> "Expr":
        left = self._parse_unary()

        while True:
            op = self._peek().value

            # Ternary conditional: lower precedence than all binary ops (prec=1)
            if op == "?" and min_prec <= 1:
                self._consume()
                then_e = self._parse_expression(0)
                self._consume(":")
                else_e = self._parse_expression(1)   # right-associative
                left = Conditional(cond=left, then_expr=then_e, else_expr=else_e)
                continue

            prec = _BINOP_PREC.get(op)
            if prec is None or prec < min_prec:
                break

            self._consume()
            # Left-associative: recurse at prec+1
            right = self._parse_expression(prec + 1)
            left = BinOp(op=op, left=left, right=right)

        return left

    def _parse_unary(self) -> "Expr":
        if self._at("~", "!", "-", "+", "&", "|", "^", "~&", "~|", "~^", "^~"):
            op = self._consume().value
            operand = self._parse_unary()
            return UnaryOp(op=op, operand=operand)
        return self._parse_primary()

    def _parse_primary(self) -> "Expr":
        tok = self._peek()

        # Parenthesized expression
        if tok.value == "(":
            self._consume("(")
            expr = self._parse_expression()
            self._consume(")")
            return self._parse_postfix(expr)

        # Concatenation / replication: {…}
        if tok.value == "{":
            return self._parse_concat_or_replicate()

        # Number literal (sized or unsized)
        if tok.type == "NUMBER":
            return self._parse_number()

        # Fill literal: '0 '1 'x 'z
        if tok.type == "FILL":
            self._consume()
            return FillLiteral(fill=tok.value.lstrip("'"))

        # String
        if tok.type == "STRING":
            self._consume()
            return StringLiteral(text=tok.value.strip('"'))

        # System function: $clog2(...), $signed(...), etc.
        if tok.type == "SYSID":
            return self._parse_sys_func()

        # Identifier
        if tok.type == "ID":
            self._consume()
            expr: "Expr" = Identifier(name=tok.value)
            return self._parse_postfix(expr)

        self._error(f"unexpected token in expression: {tok.value!r} ({tok.type})")

    def _parse_postfix(self, base: "Expr") -> "Expr":
        """Parse bit-select / part-select after a primary."""
        while self._at("["):
            self._consume("[")
            idx = self._parse_expression()
            if self._at(":"):
                self._consume(":")
                lo = self._parse_expression()
                self._consume("]")
                base = PartSelect(base=base, hi=idx, lo=lo)
            else:
                self._consume("]")
                base = BitSelect(base=base, index=idx)
        return base

    def _parse_concat_or_replicate(self) -> "Expr":
        """Parse { expr {…} } (replicate) or { expr, … } (concat)."""
        self._consume("{")
        first = self._parse_expression()
        if self._at("{"):
            # Replication: { count { value } }
            self._consume("{")
            parts = [self._parse_expression()]
            while self._try_consume(","):
                parts.append(self._parse_expression())
            self._consume("}")
            self._consume("}")
            val = parts[0] if len(parts) == 1 else Concat(parts=parts)
            return Replicate(count=first, value=val)
        # Concatenation
        parts = [first]
        while self._try_consume(","):
            parts.append(self._parse_expression())
        self._consume("}")
        return Concat(parts=parts)

    def _parse_concat(self) -> "Expr":
        """Parse a concatenation (for LHS of assignments)."""
        self._consume("{")
        parts = [self._parse_expression()]
        while self._try_consume(","):
            parts.append(self._parse_expression())
        self._consume("}")
        return Concat(parts=parts)

    def _parse_number(self) -> NumberLiteral:
        tok = self._consume(expected_type="NUMBER")
        return _parse_number_token(tok.value)

    def _parse_sys_func(self) -> SysFunc:
        name = self._consume(expected_type="SYSID").value.lstrip("$")
        args = []
        if self._try_consume("("):
            if not self._at(")"):
                args.append(self._parse_expression())
                while self._try_consume(","):
                    args.append(self._parse_expression())
            self._consume(")")
        return SysFunc(name=name, args=args)


# ---------------------------------------------------------------------------
# Number token parsing helpers
# ---------------------------------------------------------------------------

def _parse_number_token(s: str) -> NumberLiteral:
    """Convert a NUMBER token string into a NumberLiteral."""
    s_clean = s.replace("_", "")
    signed = False

    # Check for base prefix: [size]'[s]<base><digits>
    tick = s_clean.find("'")
    if tick >= 0:
        size_str = s_clean[:tick]
        rest = s_clean[tick + 1:]
        width: Optional[int] = int(size_str) if size_str else None
        if rest and rest[0] in "sS":
            signed = True
            rest = rest[1:]
        base_char = rest[0].lower()
        digits = rest[1:]
        base_map = {"b": 2, "o": 8, "d": 10, "h": 16}
        base = base_map.get(base_char, 10)
        # X/Z digits → treat as 0 for binary/X, ignored for now (4-val future work)
        digits_clean = digits.replace("x", "0").replace("z", "0").replace("X", "0").replace("Z", "0")
        if not digits_clean:
            digits_clean = "0"
        value = int(digits_clean, base)
        if width is not None:
            value = value & ((1 << width) - 1)
        return NumberLiteral(value=value, width=width, signed=signed)
    else:
        # Plain decimal
        value = int(s_clean)
        return NumberLiteral(value=value, width=None, signed=False)


# ---------------------------------------------------------------------------
# Public convenience functions
# ---------------------------------------------------------------------------

def parse_string(source: str, filename: str = "<string>") -> Module:
    """Parse SV source string and return a Module AST."""
    tokens = tokenize(source, filename)
    parser = Parser(tokens, filename)
    # Skip to 'module' keyword
    while not parser._at("module", "EOF"):
        parser._consume()
    if parser._at("EOF"):
        raise ParseError("No module declaration found")
    return parser.parse_module()


def parse_file(path: str) -> Module:
    """Parse a .sv file and return a Module AST."""
    with open(path, encoding="utf-8") as f:
        source = f.read()
    mod = parse_string(source, filename=path)
    mod.source_file = path
    return mod
