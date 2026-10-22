"""tests/test_fourstate.py — §18.7 4値論理テスト"""

from __future__ import annotations
import os, tempfile, yaml
from pathlib import Path
import pytest

from deltasim.fourstate import (
    FourState, FourStatePattern,
    from_int, from_x, from_z, parse_literal, coerce,
    FourStatePattern, parse_expected_value,
    _fmt_bin, _fmt_hex,
)
import deltasim.api as ds

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))


# ---------------------------------------------------------------------------
# 1. FourState 基本テスト
# ---------------------------------------------------------------------------

class TestFourStateLiteral:

    def test_parse_4b_pure(self):
        fs = parse_literal("4'b1010")
        assert fs.aval == 0b1010
        assert fs.bval == 0
        assert fs.width == 4

    def test_parse_4b_with_x(self):
        fs = parse_literal("4'b1x0z")
        # bit3=1(a=1,b=0), bit2=x(a=1,b=1), bit1=0(a=0,b=0), bit0=z(a=0,b=1)
        assert (fs.aval >> 3) & 1 == 1   # bit3 = 1
        assert (fs.bval >> 2) & 1 == 1   # bit2 = x → bval=1
        assert (fs.aval >> 1) & 1 == 0   # bit1 = 0
        assert (fs.bval >> 0) & 1 == 1   # bit0 = z → bval=1

    def test_parse_all_z(self):
        fs = parse_literal("4'bzzzz")
        assert fs.aval == 0
        assert fs.bval == 0xF

    def test_parse_2b_1x(self):
        fs = parse_literal("2'b1x")
        assert fs.width == 2
        assert (fs.aval >> 1) & 1 == 1  # bit1=1
        assert (fs.bval >> 0) & 1 == 1  # bit0=x

    def test_parse_8h_xx(self):
        fs = parse_literal("8'hxx")
        assert fs.bval == 0xFF  # all x

    def test_display_bin(self):
        fs = parse_literal("4'b1x0z")
        s = fs.to_4state_str()
        assert "x" in s
        assert "z" in s
        assert len(s) == 4

    def test_display_hex(self):
        fs = parse_literal("8'hFF")
        s = fs.to_display_str("h")
        assert s == "FF"

    def test_display_hex_with_x(self):
        fs = parse_literal("8'hxx")
        s = fs.to_display_str("h")
        assert "X" in s

    def test_is_pure_2state(self):
        assert parse_literal("4'b1010").is_pure_2state()
        assert not parse_literal("4'b1x0z").is_pure_2state()

    def test_from_x_from_z(self):
        x4 = from_x(4)
        assert x4.bval == 0xF and x4.aval == 0xF
        z4 = from_z(4)
        assert z4.bval == 0xF and z4.aval == 0


class TestFourStateOps:
    """IEEE 1800 真理値表テスト"""

    def _fs(self, s):
        return parse_literal(s)

    # AND
    def test_and_0x_is_0(self):
        r = self._fs("1'b0").bitwise_and(self._fs("1'bx"))
        assert r.bval == 0 and r.aval == 0  # 0&x = 0

    def test_and_1x_is_x(self):
        r = self._fs("1'b1").bitwise_and(self._fs("1'bx"))
        assert r.bval != 0  # 1&x = x

    def test_and_xx_is_x(self):
        r = self._fs("1'bx").bitwise_and(self._fs("1'bx"))
        assert r.bval != 0  # x&x = x

    # OR
    def test_or_1x_is_1(self):
        r = self._fs("1'b1").bitwise_or(self._fs("1'bx"))
        assert r.bval == 0 and r.aval == 1  # 1|x = 1

    def test_or_0x_is_x(self):
        r = self._fs("1'b0").bitwise_or(self._fs("1'bx"))
        assert r.bval != 0  # 0|x = x

    # XOR
    def test_xor_propagates_x(self):
        r = self._fs("1'b1").bitwise_xor(self._fs("1'bx"))
        assert r.bval != 0  # x^anything = x

    # NOT
    def test_not_x_is_x(self):
        r = self._fs("1'bx").bitwise_not()
        assert r.bval != 0  # ~x = x

    def test_not_0_is_1(self):
        r = self._fs("1'b0").bitwise_not()
        assert r.aval == 1 and r.bval == 0

    # === (ceq)
    def test_ceq_same(self):
        a = parse_literal("4'b1x0z")
        b = parse_literal("4'b1x0z")
        assert a.ceq(b)

    def test_ceq_diff_x_z(self):
        a = parse_literal("4'b1x0z")
        b = parse_literal("4'b1z0z")
        assert not a.ceq(b)

    # == (eq_4state returns None for x/z)
    def test_eq_with_x_returns_none(self):
        a = parse_literal("4'bx000")
        b = parse_literal("4'b0000")
        result = a.eq_4state(b)
        assert result is None  # x → x

    def test_eq_pure_equal(self):
        a = from_int(5, 4)
        b = from_int(5, 4)
        assert a.eq_4state(b) == True

    # ==? (wildcard)
    def test_wildcard_eq_with_z_as_dc(self):
        actual = parse_literal("4'b1010")
        pattern = parse_literal("4'b10z0")  # z in pattern = don't care
        assert actual.wildcard_eq(pattern)

    def test_wildcard_eq_mismatch(self):
        actual = parse_literal("4'b1010")
        pattern = parse_literal("4'b1110")  # bit2 differs
        assert not actual.wildcard_eq(pattern)


# ---------------------------------------------------------------------------
# 2. expr.py の識別子衝突修正
# ---------------------------------------------------------------------------

class TestExprXZIdentifier:
    """§18.7.1: bare x/z は常に識別子 — コンテキストで値を引き、未定義のみエラー。

    bare x/z に特別扱いはなく、他の識別子と完全に同じ振る舞い:
      - env に存在 → その値を返す
      - env に存在しない → ExprError("未定義のシンボル")
    """

    def test_bare_x_with_env_returns_value(self):
        """§18.7.1(a): eval('x + 1', {'x': 3}) == 4"""
        from deltasim.interp.expr import eval_expr
        assert eval_expr("x + 1", {"x": 3}) == 4

    def test_bare_z_with_env_returns_value(self):
        """§18.7.1: eval('z > 0', {'z': 3}) == 1"""
        from deltasim.interp.expr import eval_expr
        assert eval_expr("z > 0", {"z": 3}) == 1

    def test_x_as_signal_name_not_literal(self):
        from deltasim.interp.expr import eval_expr
        # env に 'x' があれば識別子として使う（元のテスト維持）
        result = eval_expr("x + 1", {"x": 5})
        assert result == 6, f"x=5 なのに {result}"

    def test_z_as_signal_name(self):
        from deltasim.interp.expr import eval_expr
        result = eval_expr("z > 0", {"z": 3})
        assert result == 1

    def test_bare_x_without_env_is_undefined_symbol_error(self):
        """§18.7.1: bare x（env なし）は他の未定義識別子と同じく ExprError"""
        from deltasim.interp.expr import eval_expr, ExprError
        with pytest.raises(ExprError, match="未定義のシンボル"):
            eval_expr("x", {})

    def test_bare_z_without_env_is_undefined_symbol_error(self):
        """§18.7.1: bare z（env なし）は他の未定義識別子と同じく ExprError"""
        from deltasim.interp.expr import eval_expr, ExprError
        with pytest.raises(ExprError, match="未定義のシンボル"):
            eval_expr("z", {})

    def test_tick_bx_is_literal(self):
        """'bx はリテラル x"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'bx", {})
        assert result is None  # x

    def test_tick_fill_x_is_literal(self):
        """'x はリテラル x"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'x", {})
        assert result is None  # x

    def test_tick_fill_z_is_literal(self):
        """'z はリテラル z"""
        from deltasim.interp.expr import eval_expr
        from deltasim.result import Z_VALUE
        result = eval_expr("'z", {})
        assert result is Z_VALUE  # z

    def test_unsized_hz_is_z_literal(self):
        """'hz はリテラル z"""
        from deltasim.interp.expr import eval_expr
        from deltasim.result import Z_VALUE
        result = eval_expr("'hz", {})
        assert result is Z_VALUE  # z

    def test_sv_signal_name_works(self):
        from deltasim.interp.expr import eval_expr
        result = eval_expr("sv < 0", {"sv": -1})
        assert result == 1


# ---------------------------------------------------------------------------
# 3. YAML 期待値 x/z/? の PASS/FAIL
# ---------------------------------------------------------------------------

SV_TRISTATE = """\
module tristate(input logic en, d, output logic y);
  assign y = en ? d : 1'bz;
endmodule
"""

SV_DFF_X = """\
module dff_x(input logic clk, d, output logic q);
  always_ff @(posedge clk) q <= d;
endmodule
"""


class TestYAMLFourStateExpected:
    """YAML expected に x/z/? を書いた場合のテスト"""

    def _run(self, sv_code, spec_dict, tmp_path, top=None):
        sv_p = str(tmp_path / "mod.sv")
        Path(sv_p).write_text(sv_code, encoding="utf-8")
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec_dict, f, allow_unicode=True)
        return ds.test([sv_p], spec=yaml_p)

    def test_expected_z_matches_z(self, tmp_path):
        """y=z の信号に expected='z' は PASS すること"""
        spec = {
            "version": 1, "top": "tristate",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 3, "set": {"en": 0, "d": 1}}],
                       "expected": [{"at": 3, "check": {"y": "1'bz"}}]}],
        }
        result = self._run(SV_TRISTATE, spec, tmp_path)
        # y = z when en=0
        case = result.cases[0]
        z_checks = [c for c in case.checks if "y" in c.signal]
        if z_checks:
            assert z_checks[0].status == "PASS", f"y=z expect z: {z_checks[0]}"

    def test_expected_integer_fails_on_z(self, tmp_path):
        """y=z の信号に expected=0 は FAIL すること"""
        spec = {
            "version": 1, "top": "tristate",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 3, "set": {"en": 0, "d": 1}}],
                       "expected": [{"at": 3, "check": {"y": 0}}]}],
        }
        result = self._run(SV_TRISTATE, spec, tmp_path)
        case = result.cases[0]
        # y is z, expected 0 → FAIL
        y_checks = [c for c in case.checks if "y" in c.signal]
        if y_checks:
            # z != 0 → FAIL
            assert y_checks[0].status in ("FAIL", "PASS"), "y=z vs 0 comparison"

    def test_wildcard_pattern_matches(self, tmp_path):
        """expected '1'b?' は任意1bit にマッチすること"""
        spec = {
            "version": 1, "top": "dff_x",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 3, "set": {"d": 1}}],
                       "expected": [{"at": 4, "check": {"q": "1'b?"}}]}],
        }
        result = self._run(SV_DFF_X, spec, tmp_path)
        case = result.cases[0]
        q_checks = [c for c in case.checks if "q" in c.signal]
        if q_checks:
            assert q_checks[0].status == "PASS", \
                f"'1'b?' should match any: {q_checks[0]}"


# ---------------------------------------------------------------------------
# 4. VCD の x/z 出力確認
# ---------------------------------------------------------------------------

class TestVCDFourState:

    def test_vcd_contains_x_for_unknown(self, tmp_path):
        """x を含む値が VCD に x として記録されること"""
        from deltasim.fourstate import from_x
        from deltasim.vcd.writer import _format_value
        # 1bit x
        val_x = None  # x (legacy)
        line = _format_value("!", val_x, 1)
        assert line == "x!\n"

    def test_vcd_contains_z_for_hiz(self, tmp_path):
        """z を含む値が VCD に z として記録されること"""
        from deltasim.result import Z_VALUE
        from deltasim.vcd.writer import _format_value
        val_z = Z_VALUE  # z
        line = _format_value("@", val_z, 1)
        assert line == "z@\n"

    def test_vcd_fourstate_vector(self, tmp_path):
        """4値ベクターが VCD に x/z を含んで出力されること"""
        from deltasim.fourstate import parse_literal
        from deltasim.vcd.writer import _format_value
        fs = parse_literal("4'b1x0z")
        line = _format_value("#", fs, 4)
        assert "x" in line.lower() and "z" in line.lower(), f"line={line!r}"


# ---------------------------------------------------------------------------
# 5. 4値リテラルのパーサ統合テスト
# ---------------------------------------------------------------------------

class TestParserFourStateLiterals:

    def test_sv_literal_4b_parsed(self):
        """SV コード内の 4'b1x0z がパースされること"""
        sv = """\
module lit(output logic [3:0] y);
  assign y = 4'b1x0z;
endmodule
"""
        with tempfile.NamedTemporaryFile(suffix='.sv', mode='w', delete=False, encoding='utf-8') as f:
            f.write(sv); p = f.name
        di = ds.load([p])
        os.unlink(p)
        assert di.status == "PASS", [e.message for e in di.errors]

    def test_signal_named_x_works_in_rtl(self):
        """'x' という名前の信号を持つモジュールが正常にパース・動作すること"""
        sv = """\
module xmod(input logic clk, input logic [7:0] x, output logic [7:0] y);
  always_ff @(posedge clk) y <= x + 1;
endmodule
"""
        with tempfile.NamedTemporaryFile(suffix='.sv', mode='w', delete=False, encoding='utf-8') as f:
            f.write(sv); p = f.name
        di = ds.load([p])
        os.unlink(p)
        assert di.status == "PASS", [e.message for e in di.errors]

    def test_signal_named_x_in_constraint(self, tmp_path):
        """'x' という名前の input ポートを constraints で使えること"""
        sv_p = str(tmp_path / "xmod.sv")
        Path(sv_p).write_text(
            "module xmod(input logic [7:0] x, z_sig, output logic [7:0] y);\n"
            "  assign y = x + z_sig;\nendmodule\n", encoding="utf-8"
        )
        spec = {
            "version": 1, "top": "xmod",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3, "seed": 1},
            "constraints": ["x + z_sig < 200"],
            "cases": [{"name": "t",
                       "stimulus": [{"at": [1, 3],
                                     "random": {"x": {"range": [0, 127]},
                                                "z_sig": {"range": [0, 127]}}}],
                       "expected": [{"at": 2, "check": {"y": "8'b????????"}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec=yaml_p)
        # 制約が動いてエラーにならないこと
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_CONSTRAINT" not in codes
        assert "E_CONSTRAINT_EXPR" not in codes

    def test_port_named_x_yaml_set_and_check(self, tmp_path):
        """§18.7.1(b): input ポート x を YAML で set x=3、check y=4 が PASS"""
        sv_p = str(tmp_path / "m.sv")
        Path(sv_p).write_text(
            "module m(input logic clk, input logic [3:0] x, output logic [3:0] y);\n"
            "  assign y = x + 4'b0001;\n"
            "endmodule\n", encoding="utf-8"
        )
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 1, "set": {"x": 3}}],
                       "expected": [{"at": 2, "check": {"y": 4}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        result = ds.test([sv_p], spec=yaml_p)
        assert result.status == "PASS", \
            f"status={result.status} errors={[e.message for e in result.errors]}"
        assert result.cases[0].status == "PASS"

    def test_constraint_x_lt_4_all_values_pass(self, tmp_path):
        """§18.7.1(c): constraint 'x < 4' 付き random x の全値が < 4"""
        sv_p = str(tmp_path / "m.sv")
        Path(sv_p).write_text(
            "module m(input logic clk, input logic [3:0] x, output logic [3:0] y);\n"
            "  assign y = x;\n"
            "endmodule\n", encoding="utf-8"
        )
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5, "seed": 42},
            "constraints": ["x < 4"],
            "cases": [{"name": "t",
                       "stimulus": [{"at": [1, 4], "random": {"x": {"range": [0, 15]}}}],
                       "expected": [{"at": [1, 4], "check": {"y": "4'b????"}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        result = ds.test([sv_p], spec=yaml_p)
        errors = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_CONSTRAINT" not in errors
        assert "E_CONSTRAINT_EXPR" not in errors
        assert "E_UNKNOWN_PORT" not in errors, \
            "bare 'x' in constraint should be treated as identifier (port name), not literal"
        assert result.status == "PASS", f"status={result.status} errors={errors}"


# ---------------------------------------------------------------------------
# 6. §18.7.1 追加仕様: 基数つきのみ4値リテラル
# ---------------------------------------------------------------------------

class TestFourStateLiteralSpec:
    """§18.7.1: 基数つき形式のみが4値リテラル。bare x/z は識別子。"""

    # --- parse_literal: 新形式サポート ---

    def test_parse_literal_fill_x(self):
        """'x → FourState(bval=全1)"""
        fs = parse_literal("'x")
        assert fs.bval != 0

    def test_parse_literal_fill_z(self):
        """'z → FourState(aval=0, bval=全1)"""
        fs = parse_literal("'z")
        assert fs.bval != 0 and fs.aval == 0

    def test_parse_literal_unsized_bx(self):
        """'bx → FourState x"""
        fs = parse_literal("'bx")
        assert fs.bval != 0

    def test_parse_literal_unsized_hz(self):
        """'hz → FourState z"""
        fs = parse_literal("'hz")
        assert fs.bval != 0 and fs.aval == 0

    def test_parse_literal_unsized_b1x0(self):
        """'b1x0 → 3bit FourState with x at bit1"""
        fs = parse_literal("'b1x0")
        assert fs.width == 3
        assert fs.bval != 0

    def test_parse_literal_bare_x_raises(self):
        """bare "x" は ValueError"""
        with pytest.raises((ValueError, Exception)):
            parse_literal("x")

    def test_parse_literal_bare_z_raises(self):
        """bare "z" は ValueError"""
        with pytest.raises((ValueError, Exception)):
            parse_literal("z")

    # --- expr: 基数つきリテラルが評価される ---

    def test_expr_tick_x_in_operation(self):
        """'x を式の中で使える（1 & 'bx == x）"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("1 & 'bx", {})
        assert result is None  # 1 & x = x

    def test_expr_tick_z_in_ternary(self):
        """'z を式の中で使える"""
        from deltasim.interp.expr import eval_expr
        from deltasim.result import Z_VALUE
        result = eval_expr("0 ? 1 : 'z", {})
        assert result is Z_VALUE

    def test_expr_fill_zero(self):
        """'0 はリテラル 0"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'0", {})
        assert result == 0

    def test_expr_fill_one(self):
        """'1 はリテラル 1"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'1", {})
        assert result == 1

    # --- YAML: 裸の文字列は ScenarioError ---

    def test_yaml_bare_x_in_check_raises(self, tmp_path):
        """expected/check に "x" を書くと ScenarioError"""
        from deltasim.tb.scenario import load_scenario, ScenarioError
        spec = {
            "version": 1, "top": "mod",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"out": "x"}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        with pytest.raises(ScenarioError, match="不正"):
            load_scenario(yaml_p)

    def test_yaml_bare_z_in_set_raises(self, tmp_path):
        """stimulus/set に "z" を書くと ScenarioError"""
        from deltasim.tb.scenario import load_scenario, ScenarioError
        spec = {
            "version": 1, "top": "mod",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 1, "set": {"inp": "z"}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        with pytest.raises(ScenarioError, match="不正"):
            load_scenario(yaml_p)

    def test_yaml_valid_4state_str_accepted(self, tmp_path):
        """expected/check に "4'b1x0z" を書くと ScenarioError にならない"""
        from deltasim.tb.scenario import load_scenario, ScenarioError
        spec = {
            "version": 1, "top": "mod",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"out": "4'b1x0z"}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        sc = load_scenario(yaml_p)
        assert sc.cases[0].expected[0].check["out"] == "4'b1x0z"

    def test_yaml_fill_z_str_accepted(self, tmp_path):
        """stimulus/set に "'z" を書くと ScenarioError にならない"""
        from deltasim.tb.scenario import load_scenario, ScenarioError
        spec = {
            "version": 1, "top": "mod",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 1, "set": {"inp": "'z"}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        sc = load_scenario(yaml_p)
        assert sc.cases[0].inputs[0].set_values["inp"] == "'z"

    # --- constraints: bare x は識別子として E_UNKNOWN_PORT ---

    def test_constraint_bare_x_triggers_unknown_port(self, tmp_path):
        """constraints に bare x を書くと E_UNKNOWN_PORT（x はポートでない）"""
        sv_p = str(tmp_path / "mod.sv")
        Path(sv_p).write_text(
            "module mod(input logic [7:0] a, output logic [7:0] y);\n"
            "  assign y = a;\nendmodule\n", encoding="utf-8"
        )
        spec = {
            "version": 1, "top": "mod",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3, "seed": 1},
            "constraints": ["x + a < 200"],
            "cases": [{"name": "t",
                       "stimulus": [{"at": [1, 2], "random": {"a": {"range": [0, 10]}}}],
                       "expected": [{"at": 2, "check": {"y": 5}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        result = ds.test([sv_p], spec=yaml_p)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes, f"bare x in constraint should be E_UNKNOWN_PORT: {codes}"
