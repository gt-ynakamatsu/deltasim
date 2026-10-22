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
    TimeDelay, ForeverStmt, RepeatStmt, WaitEventStmt, ForStmt, WhileStmt,
    # M3 additions
    PortConn, InstDecl, GenVarDecl,
    GenerateFor, GenerateIf, GenerateBlock,
    EnumMember, EnumTypeDef, StructField, StructTypeDef, TypedefDecl, ImportDecl,
    # M4 additions
    MemberAccess,
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
        mod_line = tok.line
        name = self._consume(expected_type="ID").value
        params = self._parse_param_port_list() if self._at("#") else []
        # Port list is optional (TB modules may have no ports: module foo;)
        ports = self._parse_port_list() if self._at("(") else []
        self._consume(";")
        items = []
        while not self._at("endmodule", "EOF"):
            prev_pos = self._pos
            items.extend(self._parse_module_item())
            # §16 safety: if no token was consumed, force-advance to prevent
            # infinite loops on unrecognised / EOF tokens.
            if self._pos == prev_pos:
                if self._at("EOF"):
                    break
                self._consume()  # skip one unrecognised token
        if self._at("EOF"):
            eof_tok = self._peek()
            self._error(
                f"endmodule がありません（module '{name}' は {mod_line} 行目で開始）"
            )
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
        while not self._at(")", "EOF"):
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
            ports.extend(self._parse_port_decl_multi())
            while self._try_consume(","):
                if self._at(")"):
                    break
                ports.extend(self._parse_port_decl_multi())
        self._consume(")")
        return ports

    def _parse_port_decl_multi(self) -> List[PortDecl]:
        """Parse one ANSI port declaration, returning one or more PortDecl.

        IEEE 1800: multiple names may share a single direction/type/dims, e.g.:
          input logic clk, rst, d
          output logic [3:0] a, b
        """
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

        # First name (mandatory)
        name = self._consume(expected_type="ID").value
        result = [PortDecl(direction=direction, net_type=net_type, dims=dims,
                           name=name, signed=signed)]

        # Additional names sharing the same direction/type/dims:
        # peek ahead — if the next token is "," and the token after that is an
        # identifier (not a direction keyword), consume them as extra names.
        while self._at(","):
            saved_pos = self._pos
            self._pos += 1  # tentatively skip ","
            if self._at("input", "output", "inout") or self._at(")"):
                # Next is a new full port decl or end of list — put "," back
                self._pos = saved_pos
                break
            if self._peek().type != "ID":
                # Not an identifier — put "," back
                self._pos = saved_pos
                break
            extra_name = self._consume(expected_type="ID").value
            result.append(PortDecl(direction=direction, net_type=net_type,
                                   dims=dims, name=extra_name, signed=signed))

        return result

    def _parse_port_decl(self) -> PortDecl:  # kept for compat (unused internally)
        return self._parse_port_decl_multi()[0]

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
                    "supply0", "supply1", "tri", "wand", "wor",
                    "byte", "shortint", "longint"):
            return self._parse_net_decl()
        if self._at("generate"):
            return [self._parse_generate_block()]
        if self._at("genvar"):
            return [self._parse_genvar_decl()]
        if self._at("typedef"):
            return [self._parse_typedef()]
        if self._at("import"):
            return [self._parse_import()]
        # Check for module instantiation: ID [#(...)] ID (...)
        if self._peek().type == "ID":
            return self._try_parse_inst_decl()
        tok = self._peek()
        if tok.type == "EOF" or tok.value == "endmodule":
            return []
        # Skip unknown tokens gracefully
        self._consume()
        return []

    # ------------------------------------------------------------------
    # Module instantiation  (M3)
    # ------------------------------------------------------------------

    def _try_parse_inst_decl(self):
        """Attempt to parse a module instantiation or typedef'd net decl."""
        # Lookahead: ID [#(...)] ID ( → instantiation
        j = self._pos
        # skip module name
        j += 1
        # skip optional #(...)
        if j < len(self._tokens) and self._tokens[j].value == '#':
            j += 1
            if j < len(self._tokens) and self._tokens[j].value == '(':
                depth, j = 1, j + 1
                while depth and j < len(self._tokens):
                    if self._tokens[j].value == '(':
                        depth += 1
                    elif self._tokens[j].value == ')':
                        depth -= 1
                    j += 1
        # expect instance name (ID) then '('
        if (j < len(self._tokens) and self._tokens[j].type == 'ID' and
                j + 1 < len(self._tokens) and self._tokens[j + 1].value == '('):
            return [self._parse_inst_decl()]
        # Otherwise treat as typedef'd net decl
        return self._parse_typedef_net_decl()

    def _parse_inst_decl(self) -> InstDecl:
        module_name = self._consume(expected_type="ID").value
        # Optional parameter override: #(.P(v), ...) or #(v, ...)
        param_overrides: List[Tuple[Optional[str], object]] = []
        if self._at("#"):
            self._consume("#")
            self._consume("(")
            while not self._at(")", "EOF"):
                if self._at("."):
                    self._consume(".")
                    pname = self._consume(expected_type="ID").value
                    self._consume("(")
                    pval = self._parse_expression()
                    self._consume(")")
                    param_overrides.append((pname, pval))
                else:
                    pval = self._parse_expression()
                    param_overrides.append((None, pval))
                if not self._try_consume(","):
                    break
            self._consume(")")
        inst_name = self._consume(expected_type="ID").value
        self._consume("(")
        port_conns: List[PortConn] = []
        while not self._at(")", "EOF"):
            if self._at("."):
                self._consume(".")
                pname = self._consume(expected_type="ID").value
                self._consume("(")
                sig = None if self._at(")") else self._parse_expression()
                self._consume(")")
                port_conns.append(PortConn(port=pname, signal=sig))
            else:
                sig = self._parse_expression()
                port_conns.append(PortConn(port=None, signal=sig))
            if not self._try_consume(","):
                break
        self._consume(")")
        self._consume(";")
        return InstDecl(module_name=module_name, inst_name=inst_name,
                        param_overrides=param_overrides, port_conns=port_conns)

    def _parse_typedef_net_decl(self):
        """Parse typedef'd type variable declaration: type_name var_name [dims];"""
        type_name = self._consume(expected_type="ID").value
        names = []
        # Might have packed dims
        dims = None
        if self._at("["):
            dims = self._parse_dims()
        if self._peek().type == "ID":
            names.append(self._consume(expected_type="ID").value)
            while self._try_consume(","):
                names.append(self._consume(expected_type="ID").value)
        self._try_consume(";")
        if names:
            from deltasim.parser.ast_nodes import NetDecl
            return [NetDecl(net_type=type_name, dims=dims, names=names)]
        return []

    # ------------------------------------------------------------------
    # generate block  (M3)
    # ------------------------------------------------------------------

    def _parse_generate_block(self) -> GenerateBlock:
        self._consume("generate")
        items = []
        while not self._at("endgenerate", "EOF"):
            items.extend(self._parse_generate_item())
        self._consume("endgenerate")
        return GenerateBlock(items=items)

    def _parse_generate_item(self):
        if self._at("for"):
            return [self._parse_generate_for()]
        if self._at("if"):
            return [self._parse_generate_if()]
        if self._at("begin"):
            return self._parse_generate_begin()
        # Regular module item
        return self._parse_module_item()

    def _parse_generate_for(self) -> GenerateFor:
        self._consume("for")
        self._consume("(")
        # init: [genvar] i = expr
        if self._at("genvar"):
            self._consume()
        var = self._consume(expected_type="ID").value
        self._consume("=")
        init = self._parse_expression()
        self._consume(";")
        cond = self._parse_expression()
        self._consume(";")
        # update: i++ or i = i + 1
        update = self._parse_expression()
        if self._at("="):
            # i = expr form
            self._consume("=")
            rhs = self._parse_expression()
            update = BinOp("=", update, rhs)  # hack: store as BinOp
        self._consume(")")
        body = self._parse_generate_begin_items()
        return GenerateFor(var=var, init=init, cond=cond, update=update, body=body)

    def _parse_generate_if(self) -> GenerateIf:
        self._consume("if")
        self._consume("(")
        cond = self._parse_expression()
        self._consume(")")
        then_items = self._parse_generate_begin_items()
        else_items = None
        if self._try_consume("else"):
            else_items = self._parse_generate_begin_items()
        return GenerateIf(cond=cond, then_items=then_items, else_items=else_items)

    def _parse_generate_begin_items(self):
        if self._at("begin"):
            return self._parse_generate_begin()
        return self._parse_generate_item()

    def _parse_generate_begin(self):
        self._consume("begin")
        # optional named label: : label_name
        if self._at(":"):
            self._consume(":")
            self._try_consume()  # label name
        items = []
        while not self._at("end", "EOF"):
            items.extend(self._parse_generate_item())
        self._consume("end")
        # optional : label
        if self._at(":"):
            self._consume(":")
            self._try_consume()
        return items

    # ------------------------------------------------------------------
    # genvar  (M3)
    # ------------------------------------------------------------------

    def _parse_genvar_decl(self) -> GenVarDecl:
        self._consume("genvar")
        names = [self._consume(expected_type="ID").value]
        while self._try_consume(","):
            names.append(self._consume(expected_type="ID").value)
        self._consume(";")
        return GenVarDecl(names=names)

    # ------------------------------------------------------------------
    # typedef  (M3)
    # ------------------------------------------------------------------

    def _parse_typedef(self) -> TypedefDecl:
        self._consume("typedef")
        if self._at("enum"):
            return self._parse_typedef_enum()
        if self._at("struct", "union"):
            return self._parse_typedef_struct()
        # typedef existing_type new_name;
        type_name = self._consume().value
        dims = None
        if self._at("["):
            dims = self._parse_dims()
        new_name = self._consume(expected_type="ID").value
        self._try_consume(";")
        return TypedefDecl(type_def=type_name, name=new_name)

    def _parse_typedef_enum(self) -> TypedefDecl:
        self._consume("enum")
        # optional base type: logic [N:0], int, bit, etc.
        base_kw = None
        dims = None
        if self._at("logic", "bit", "int", "integer", "byte",
                    "shortint", "longint", "signed", "unsigned"):
            base_kw = self._consume().value
            if self._at("["):
                dims = self._parse_dims()
        self._consume("{")
        members: List[EnumMember] = []
        while not self._at("}", "EOF"):
            name = self._consume(expected_type="ID").value
            val = None
            if self._at("="):
                self._consume("=")
                val = self._parse_expression()
            members.append(EnumMember(name=name, value=val))
            if not self._try_consume(","):
                break
        self._consume("}")
        type_name = self._consume(expected_type="ID").value
        self._try_consume(";")
        return TypedefDecl(
            type_def=EnumTypeDef(base_kw=base_kw, dims=dims, members=members),
            name=type_name,
        )

    def _parse_typedef_struct(self) -> TypedefDecl:
        kw = self._consume().value  # 'struct' or 'union'
        packed = bool(self._try_consume("packed"))
        self._consume("{")
        fields: List[StructField] = []
        while not self._at("}", "EOF"):
            net_type = None
            if self._at("logic", "bit", "int", "integer", "byte",
                        "reg", "wire", "signed", "unsigned",
                        "shortint", "longint"):
                net_type = self._consume().value
            dims = None
            if self._at("["):
                dims = self._parse_dims()
            name = self._consume(expected_type="ID").value
            # May have multiple names
            names = [name]
            while self._try_consume(","):
                names.append(self._consume(expected_type="ID").value)
            self._try_consume(";")
            for n in names:
                fields.append(StructField(net_type=net_type, dims=dims, name=n))
        self._consume("}")
        type_name = self._consume(expected_type="ID").value
        self._try_consume(";")
        return TypedefDecl(type_def=StructTypeDef(packed=packed, fields=fields), name=type_name)

    # ------------------------------------------------------------------
    # import  (M3)
    # ------------------------------------------------------------------

    def _parse_import(self) -> ImportDecl:
        self._consume("import")
        pkg = self._consume(expected_type="ID").value
        # :: is two : tokens in our lexer
        self._consume(":")
        self._consume(":")
        if self._at("*"):
            self._consume("*")
            item = None
        else:
            item = self._consume(expected_type="ID").value
        self._try_consume(";")
        return ImportDecl(pkg=pkg, item=item)

    def _parse_assign_item(self) -> AssignItem:
        _line = self._peek().line
        self._consume("assign")
        lhs = self._parse_lvalue()
        self._consume("=")
        rhs = self._parse_expression()
        self._consume(";")
        return AssignItem(lhs=lhs, rhs=rhs, source_line=_line, source_file=self._filename)

    def _parse_always_item(self) -> AlwaysItem:
        keyword = self._consume().value
        sens: Optional[SensitivityList] = None

        if keyword in ("always", "always_ff"):
            if self._peek().value == "@":
                sens = self._parse_sensitivity_list()
            # else: bare always — body may start with #n or a statement
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
        # M4: optional unpacked array dimension after names: name [lo:hi] or name [depth]
        unpacked_dims: Optional[Dims] = None
        if self._at("["):
            self._consume("[")
            hi = self._parse_expression()
            if self._at(":"):
                self._consume(":")
                lo = self._parse_expression()
                self._consume("]")
                unpacked_dims = Dims(hi=hi, lo=lo)
            else:
                self._consume("]")
                # [N] shorthand: depth = N, treated as [N-1:0]
                from deltasim.parser.ast_nodes import BinOp as _BinOp, NumberLiteral as _NL
                unpacked_dims = Dims(
                    hi=_BinOp("-", hi, _NL(1, None)),
                    lo=_NL(0, None),
                )
        self._consume(";")
        return [NetDecl(net_type=net_type, dims=dims, names=names,
                        signed=signed, unpacked_dims=unpacked_dims)]

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
        while not self._at(")", "EOF"):
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
        _line = self._peek().line  # §18: ソース行番号を記録
        _file = self._filename
        if self._at("begin"):
            return self._parse_block(_line, _file)
        if self._at("if"):
            return self._parse_if(_line, _file)
        if self._at("case", "casex", "casez"):
            return self._parse_case(_line, _file)
        if self._at(";"):
            self._consume()
            return NullStmt(source_line=_line, source_file=_file)
        # System task statement: $display, $monitor, $finish, …
        if self._at_type("SYSID"):
            return self._parse_sys_task_stmt()
        # Time delay: #expression [statement]
        if self._peek().value == "#":
            return self._parse_time_delay()
        # forever / repeat / for / while / @wait
        if self._at("forever"):
            return self._parse_forever()
        if self._at("repeat"):
            return self._parse_repeat()
        if self._at("for"):
            return self._parse_for_stmt(_line, _file)
        if self._at("while"):
            return self._parse_while_stmt(_line, _file)
        if self._peek().value == "@":
            return self._parse_wait_event_stmt()
        # Assignment statement (blocking or non-blocking)
        return self._parse_assign_stmt(_line, _file)

    def _parse_time_delay(self) -> "Stmt":
        self._consume("#")
        delay = self._parse_expression()
        # Optional following statement
        if self._at(";"):
            self._consume()
            return TimeDelay(delay=delay, body=NullStmt())
        if self._at("begin", "if", "forever", "repeat", "for", "while") or \
           (not self._at("end", "endmodule", "else", "EOF") and not self._at_type("EOF")):
            # Check if the next token starts a valid statement
            next_v = self._peek().value
            if next_v in ("begin", "if", "forever", "repeat", "for", "while") or \
               self._peek().type == "ID" or next_v == "#" or \
               self._peek().type == "SYSID" or next_v == "@":
                body = self._parse_statement()
                return TimeDelay(delay=delay, body=body)
        return TimeDelay(delay=delay, body=NullStmt())

    def _parse_forever(self) -> "Stmt":
        self._consume("forever")
        body = self._parse_statement()
        return ForeverStmt(body=body)

    def _parse_repeat(self) -> "Stmt":
        self._consume("repeat")
        self._consume("(")
        count = self._parse_expression()
        self._consume(")")
        body = self._parse_statement()
        return RepeatStmt(count=count, body=body)

    def _parse_for_stmt(self, source_line: int = 0, source_file: str = "") -> "Stmt":
        self._consume("for")
        self._consume("(")
        # Init: may include type declaration (int i = 0) or just assign
        if self._at("integer", "int", "logic", "bit", "byte"):
            self._consume()  # skip type
        init = self._parse_assign_stmt()  # i = 0;
        cond = self._parse_expression()
        self._consume(";")
        # Update: blocking assign without trailing ;
        lhs = self._parse_lvalue()
        if self._at("="):
            self._consume()
            rhs = self._parse_expression()
            update = BlockingAssign(lhs=lhs, rhs=rhs)
        elif self._at("<="):
            self._consume()
            rhs = self._parse_expression()
            update = NonBlockingAssign(lhs=lhs, rhs=rhs)
        else:
            update = NullStmt()
        self._consume(")")
        body = self._parse_statement()
        return ForStmt(init=init, cond=cond, update=update, body=body,
                       source_line=source_line, source_file=source_file)

    def _parse_while_stmt(self, source_line: int = 0, source_file: str = "") -> "Stmt":
        self._consume("while")
        self._consume("(")
        cond = self._parse_expression()
        self._consume(")")
        body = self._parse_statement()
        return WhileStmt(cond=cond, body=body,
                         source_line=source_line, source_file=source_file)

    def _parse_wait_event_stmt(self) -> "Stmt":
        """@(sensitivity_list) [statement]"""
        self._consume("@")
        if self._at("*"):
            self._consume()
            sens = SensitivityList(items=[])
        else:
            self._consume("(")
            if self._at("*"):
                self._consume()
                self._consume(")")
                sens = SensitivityList(items=[])
            else:
                items: List[SensitivityItem] = []
                while not self._at(")", "EOF"):
                    edge: Optional[str] = None
                    if self._at("posedge", "negedge"):
                        edge = self._consume().value
                    sig = self._parse_expression()
                    items.append(SensitivityItem(edge=edge, signal=sig))
                    if self._at("or"):
                        self._consume("or")
                    elif self._try_consume(","):
                        pass
                    elif not self._at(")"):
                        self._try_consume("or")
                self._consume(")")
                sens = SensitivityList(items=items)
        # Optional following statement
        body: Optional["Stmt"] = None
        if not self._at("begin", "end", "else", "endmodule", "endcase",
                         "forever", "repeat", "for", "while", ";", "EOF"):
            nxt = self._peek()
            if nxt.type in ("ID", "SYSID") or nxt.value in ("#",):
                body = self._parse_statement()
        elif self._at(";"):
            self._consume()
        return WaitEventStmt(sensitivity=sens, body=body)

    def _parse_block(self, source_line: int = 0, source_file: str = "") -> Block:
        self._consume("begin")
        stmts = []
        while not self._at("end"):
            if self._at("EOF"):
                self._error("unexpected EOF inside begin…end block")
            stmts.append(self._parse_statement())
        self._consume("end")
        return Block(stmts=stmts, source_line=source_line, source_file=source_file)

    def _parse_if(self, source_line: int = 0, source_file: str = "") -> IfStmt:
        self._consume("if")
        self._consume("(")
        cond = self._parse_expression()
        self._consume(")")
        then_body = self._parse_statement()
        else_body: Optional["Stmt"] = None
        if self._try_consume("else"):
            else_body = self._parse_statement()
        return IfStmt(cond=cond, then_body=then_body, else_body=else_body,
                      source_line=source_line, source_file=source_file)

    def _parse_case(self, source_line: int = 0, source_file: str = "") -> CaseStmt:
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
        return CaseStmt(keyword=keyword, expr=expr, items=items,
                        source_line=source_line, source_file=source_file)

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

    def _parse_assign_stmt(self, source_line: int = 0, source_file: str = "") -> "Stmt":
        """Parse a blocking (=) or non-blocking (<=) assignment statement."""
        if source_line == 0:
            source_line = self._peek().line
            source_file = self._filename
        lhs = self._parse_lvalue()
        if self._at("="):
            self._consume()
            rhs = self._parse_expression()
            self._consume(";")
            return BlockingAssign(lhs=lhs, rhs=rhs,
                                  source_line=source_line, source_file=source_file)
        if self._at("<="):
            self._consume()
            rhs = self._parse_expression()
            self._consume(";")
            return NonBlockingAssign(lhs=lhs, rhs=rhs,
                                     source_line=source_line, source_file=source_file)
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
        """Parse bit-select / part-select / member-access after a primary."""
        while True:
            if self._at("["):
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
            elif self._at("."):
                # Member access: expr.field_name
                self._consume(".")
                if self._peek().type == "ID":
                    fname = self._consume(expected_type="ID").value
                    base = MemberAccess(base=base, field=fname)
                else:
                    break
            else:
                break
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
        # §18.7: x/z/? digits → parse as FourState if present
        has_xz = any(d.lower() in ("x", "z") or d == "?" for d in digits)
        if has_xz and base in (2, 8, 16):
            from deltasim.fourstate import _parse_xz_digits, _mask
            eff_width = width if width is not None else 32
            aval, bval = _parse_xz_digits(digits, base, eff_width)
            return NumberLiteral(value=aval, width=width, signed=signed, xz_mask=bval)
        digits_clean = digits.replace("x", "0").replace("z", "0").replace("X", "0").replace("Z", "0").replace("?", "0")
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
    """Parse a .sv file and return a Module AST.
    
    The file is read as UTF-8 with CRLF normalisation (Windows compatible).
    """
    from pathlib import Path
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    mod = parse_string(source, filename=str(p))
    mod.source_file = str(p)
    return mod
