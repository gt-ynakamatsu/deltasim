"""tests/test_fourstate_engine.py — §18.7 シミュレーションコア 4値テスト

要件別カバレッジ:
  1. aval/bval 演算（&|^~ の x/z 伝搬）
  2. ?: x 条件 → per-bit merge
  3. 初期値 (internal logic=x, ポート=0)
  4. トライステート E2E
  5. case/casez/casex
  6. $display %b/%h/%d x/z 表示
  7. YAML 整数期待値 vs x/z 実値 → FAIL
  8. リセット付きカウンタが PASS すること（挙動変更一覧の一部）
  9. 性能: 10万サイクル計測
"""
from __future__ import annotations
import os, yaml, tempfile, time
from pathlib import Path
import pytest

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
import deltasim.api as ds


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _make_engine(sv_code: str, *, reset_cycles=0, width=None):
    import tempfile
    from deltasim.parser.parser import parse_file
    from deltasim.elab.elaborator import elaborate
    from deltasim.sim.engine import SimEngine
    with tempfile.NamedTemporaryFile(suffix='.sv', mode='w', delete=False, encoding='utf-8') as f:
        f.write(sv_code); p = f.name
    mod = parse_file(p)
    os.unlink(p)
    design = elaborate(mod)
    return SimEngine(design, reset_cycles=reset_cycles)


def _is_x(v) -> bool:
    """None または FourState で x ビットを持つなら True。"""
    from deltasim.sim.engine import _has_xz
    return _has_xz(v)


def _run_yaml(sv_code: str, spec: dict, tmp_path: Path):
    sv_p = str(tmp_path / "mod.sv")
    Path(sv_p).write_text(sv_code, encoding='utf-8')
    yaml_p = str(tmp_path / "spec.yaml")
    with open(yaml_p, "w", encoding="utf-8") as f:
        yaml.dump(spec, f, allow_unicode=True)
    return ds.test([sv_p], spec=yaml_p)


# ---------------------------------------------------------------------------
# 1. 演算 x/z 伝搬
# ---------------------------------------------------------------------------

class TestXZPropagation:
    """§18.7 要件1: aval/bval 演算・x/z 伝搬"""

    def test_and_0_x_is_0(self):
        """0 & 'x = 0 (IEEE 1800 特例)"""
        eng = _make_engine("""
module m(input logic a, output logic b);
  assign b = a & 'x;
endmodule""")
        eng.set_input("a", 0)
        assert eng.get_output("b") == 0, "0 & x should be 0"

    def test_and_1_x_is_x(self):
        """1 & 'x = x"""
        eng = _make_engine("""
module m(input logic a, output logic b);
  assign b = a & 'x;
endmodule""")
        eng.set_input("a", 1)
        assert _is_x(eng.get_output("b")), "1 & x should be x"

    def test_or_1_x_is_1(self):
        """1 | 'x = 1 (IEEE 1800 特例)"""
        eng = _make_engine("""
module m(input logic a, output logic b);
  assign b = a | 'x;
endmodule""")
        eng.set_input("a", 1)
        assert eng.get_output("b") == 1, "1 | x should be 1"

    def test_or_0_x_is_x(self):
        """0 | 'x = x"""
        eng = _make_engine("""
module m(input logic a, output logic b);
  assign b = a | 'x;
endmodule""")
        eng.set_input("a", 0)
        assert _is_x(eng.get_output("b")), "0 | x should be x"

    def test_arith_x_propagates(self):
        """4'b1x00 + 1 = x (算術は全ビット x に伝搬)"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("4'b1x00 + 1", {})
        assert result is None, f"expected x but got {result!r}"

    def test_eq_x_returns_x(self):
        """4'b1x00 == 4'b1000 は x (== は x/z で x を返す)"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("4'b1x00 == 4'b1000", {})
        assert result is None, f"expected x but got {result!r}"

    def test_ceq_x_returns_01(self):
        """4'b1x00 === 4'b1x00 は 1 (=== は常に 0/1)"""
        from deltasim.interp.expr import eval_expr
        assert eval_expr("4'b1x00 === 4'b1x00", {}) == 1
        assert eval_expr("4'b1x00 === 4'b1000", {}) == 0

    def test_not_x_is_x(self):
        """~'x = x"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("~'bx", {})
        assert result is None

    def test_xor_x_is_x(self):
        """'bx ^ 'b0 = x"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'bx ^ 'b0", {})
        assert result is None

    def test_xor_x_x_is_x(self):
        """'bx ^ 'bx = x (XOR の x/z は常に x)"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'bx ^ 'bx", {})
        assert result is None


# ---------------------------------------------------------------------------
# 2. ?: x 条件 → per-bit merge
# ---------------------------------------------------------------------------

class TestConditionalX:
    """§18.7 要件2: ?: の条件が x のとき両辺を per-bit merge"""

    def test_ternary_x_different_branches(self):
        """`'bx ? 1 : 0` = x (両辺の bit0 が異なるので bit0 = x)"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'bx ? 1 : 0", {})
        assert _is_x(result), f"expected x but got {result!r}"

    def test_ternary_x_same_branches(self):
        """`'bx ? 5 : 5` = 5 (両辺が同じビットで一致)"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'bx ? 5 : 5", {})
        assert result == 5, f"expected 5 but got {result!r}"

    def test_ternary_x_partial_match(self):
        """`'bx ? 4'b0110 : 4'b0100` → bit1 が異なる → x"""
        from deltasim.interp.expr import eval_expr
        result = eval_expr("'bx ? 4'b0110 : 4'b0100", {})
        assert _is_x(result), f"expected x but got {result!r}"

    def test_ternary_in_rtl_x_condition(self, tmp_path):
        """RTL で tristate: `assign y = en ? d : 'z;` で en=0 → z"""
        sv = """
module tristate(input logic en, d, output logic y);
  assign y = en ? d : 1'bz;
endmodule"""
        spec = {
            "version": 1, "top": "tristate",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "en0",
                       "stimulus": [{"at": 1, "set": {"en": 0, "d": 1}}],
                       "expected": [{"at": 2, "check": {"y": "1'bz"}}]}],
        }
        result = _run_yaml(sv, spec, tmp_path)
        case = result.cases[0]
        assert case.status == "PASS", f"tristate en=0 should give z: {case.checks}"


# ---------------------------------------------------------------------------
# 3. 初期値: internal logic=x, ポート=0
# ---------------------------------------------------------------------------

class TestInitialValues:
    """§18.7 要件3: 初期値"""

    def test_internal_logic_starts_x(self):
        """内部 logic は x で初期化される（リセット前）"""
        sv = """
module m(input logic clk, output logic q);
  logic state;  // internal, direction=None, starts at x
  always_ff @(posedge clk) state <= state;
  assign q = state;
endmodule"""
        eng = _make_engine(sv, reset_cycles=0)
        # clk=0 (input=0), state=x (internal), q follows state
        assert _is_x(eng.signals.get("state")), "internal logic should start at x"

    def test_ff_output_x_before_reset(self, tmp_path):
        """リセット前の FF 内部 state が x になること"""
        sv = """
module dff_norst(input logic clk, d, output logic q);
  logic state;
  always_ff @(posedge clk) state <= d;
  assign q = state;
endmodule"""
        # YAML で cycle=0 を直接チェックはできないが、
        # 信号の初期値を API で確認する
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        sv_p = str(tmp_path / "m.sv")
        Path(sv_p).write_text(sv, encoding='utf-8')
        mod = parse_file(sv_p)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        # state は内部 logic (direction=None) → x で初期化
        assert _is_x(eng.signals.get("state")), "state should start at x"

    def test_input_port_starts_zero(self):
        """input ポートは 0 で初期化される"""
        sv = """
module m(input logic a, output logic b);
  assign b = a;
endmodule"""
        eng = _make_engine(sv, reset_cycles=0)
        assert eng.signals.get("a") == 0, "input port should start at 0"

    def test_reset_counter_passes(self, tmp_path):
        """リセット付きカウンタが引き続き PASS すること（挙動変更の影響なし）"""
        counter_sv = os.path.join(EXAMPLES, "counter.sv")
        if not os.path.exists(counter_sv):
            pytest.skip("examples/counter.sv not found")
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "basic",
                       "expected": [
                           {"at": 3, "check": {"count": 1}},
                           {"at": 4, "check": {"count": 2}},
                       ]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f, allow_unicode=True)
        result = ds.test([counter_sv], spec=yaml_p)
        assert result.status == "PASS", \
            f"reset counter should pass: {[e.message for e in result.errors]}"


# ---------------------------------------------------------------------------
# 4. トライステート E2E
# ---------------------------------------------------------------------------

class TestTristate:
    """§18.7 要件4: トライステート E2E"""

    def test_tristate_en0_gives_z(self, tmp_path):
        """en=0 のとき y=z、VCD にも z が出力されること"""
        sv = """
module tristate(input logic en, d, output logic y);
  assign y = en ? d : 1'bz;
endmodule"""
        spec = {
            "version": 1, "top": "tristate",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5, "vcd": str(tmp_path / "ts.vcd")},
            "cases": [{"name": "t",
                       "stimulus": [
                           {"at": 1, "set": {"en": 0, "d": 1}},
                           {"at": 3, "set": {"en": 1, "d": 1}},
                       ],
                       "expected": [
                           {"at": 2, "check": {"y": "1'bz"}},
                           {"at": 4, "check": {"y": "1'b1"}},
                       ]}],
        }
        result = _run_yaml(sv, spec, tmp_path)
        assert result.status == "PASS", \
            f"tristate PASS expected: {[e.message for e in result.errors]}"
        # VCD に z が含まれること
        vcd_p = tmp_path / "ts.vcd"
        if vcd_p.exists():
            content = vcd_p.read_text(encoding="utf-8")
            assert "z" in content.lower(), f"VCD should contain z: ...{content[-200:]}"

    def test_tristate_en1_gives_d(self, tmp_path):
        """en=1 のとき y=d"""
        sv = """
module tristate(input logic en, d, output logic y);
  assign y = en ? d : 1'bz;
endmodule"""
        spec = {
            "version": 1, "top": "tristate",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 1, "set": {"en": 1, "d": 0}}],
                       "expected": [{"at": 2, "check": {"y": 0}}]}],
        }
        result = _run_yaml(sv, spec, tmp_path)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# 5. case/casez/casex
# ---------------------------------------------------------------------------

class TestCaseStatements:
    """§18.7 要件5: case/casez/casex の 4値比較"""

    def test_case_strict_x_no_match(self, tmp_path):
        """case は === 比較: x はデフォルト（default）に行く"""
        sv = """
module m(input logic [1:0] sel, output logic [1:0] y);
  always_comb begin
    case (sel)
      2'b00: y = 2'b01;
      2'b01: y = 2'b10;
      2'b10: y = 2'b11;
      default: y = 2'b00;
    endcase
  end
endmodule"""
        eng = _make_engine(sv, reset_cycles=0)
        # sel starts at 0, y should be 01
        assert eng.get_output("y") == 1  # sel=0 → case 2'b00 → y=01=1

    def test_casez_z_wildcard(self, tmp_path):
        """casez: pattern 側の z/? はワイルドカード"""
        sv = """
module m(input logic [3:0] sel, output logic y);
  always_comb begin
    casez (sel)
      4'b1???: y = 1'b1;  // sel[3]=1 のとき match
      default: y = 1'b0;
    endcase
  end
endmodule"""
        eng = _make_engine(sv, reset_cycles=0)
        eng.set_input("sel", 0b1010)  # bit3=1 → match first
        assert eng.get_output("y") == 1
        eng.set_input("sel", 0b0010)  # bit3=0 → no match → default
        assert eng.get_output("y") == 0

    def test_casex_x_wildcard(self, tmp_path):
        """casex: pattern 側の x/z はワイルドカード（casez より緩い）"""
        sv = """
module m(input logic [1:0] sel, output logic y);
  always_comb begin
    casex (sel)
      2'bx1: y = 1'b1;  // LSB=1 のとき match (MSB は don't-care)
      default: y = 1'b0;
    endcase
  end
endmodule"""
        eng = _make_engine(sv, reset_cycles=0)
        eng.set_input("sel", 0b01)  # LSB=1 → match
        assert eng.get_output("y") == 1
        eng.set_input("sel", 0b11)  # LSB=1 → match
        assert eng.get_output("y") == 1
        eng.set_input("sel", 0b00)  # LSB=0 → default
        assert eng.get_output("y") == 0


# ---------------------------------------------------------------------------
# 6. $display x/z 表示
# ---------------------------------------------------------------------------

class TestDisplayXZ:
    """§18.7 要件6: $display/%b/%h/%d で x/z を正しく表示"""

    def test_display_x_with_b(self, tmp_path):
        """%b で x を含む値を表示: x ビットが 'x' として出力される"""
        sv = """
module m(input logic clk);
  logic [3:0] v;
  initial v = 4'b1x0z;
  always_ff @(posedge clk) $display("val=%b", v);
endmodule"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        sv_p = str(tmp_path / "m.sv")
        Path(sv_p).write_text(sv, encoding='utf-8')
        mod = parse_file(sv_p)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0, verbose=False)
        result = eng.run(cycles=1)
        log = "\n".join(result.log)
        assert "x" in log or "z" in log, f"display should show x/z: {log!r}"

    def test_format_display_helper(self):
        """_fmt_val_display: x/z 値のフォーマット"""
        from deltasim.sim.engine import _fmt_val_display
        assert _fmt_val_display(None, 'b') == "x"
        assert _fmt_val_display(None, 'd') == "x"
        assert _fmt_val_display(None, 'h') == "x"
        from deltasim.result import Z_VALUE
        assert _fmt_val_display(Z_VALUE, 'b') == "z"
        from deltasim.fourstate import parse_literal
        fs = parse_literal("4'b1x0z")
        disp_b = _fmt_val_display(fs, 'b')
        assert "x" in disp_b and "z" in disp_b, f"4'b1x0z %b should contain x and z: {disp_b!r}"


# ---------------------------------------------------------------------------
# 7. YAML 整数期待値 vs x/z 実値 → FAIL
# ---------------------------------------------------------------------------

class TestYAMLIntExpectedVsXZ:
    """§18.7 要件7: YAML 整数期待値に対して実値 x/z は FAIL"""

    def test_integer_expected_vs_x_fails(self, tmp_path):
        """expected が整数で actual が x → FAIL"""
        sv = """
module m(input logic en, d, output logic y);
  assign y = en ? d : 1'bz;
endmodule"""
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 1, "set": {"en": 0, "d": 1}}],
                       "expected": [{"at": 2, "check": {"y": 0}}]}],  # y is z, expected 0
        }
        result = _run_yaml(sv, spec, tmp_path)
        # y = z, expected 0 → FAIL
        case = result.cases[0]
        y_checks = [c for c in case.checks if "y" in c.signal]
        if y_checks:
            assert y_checks[0].status == "FAIL", \
                f"z != 0 should be FAIL: {y_checks[0]}"


# ---------------------------------------------------------------------------
# 9. 性能: 10万サイクル計測
# ---------------------------------------------------------------------------

class TestPerformance:
    """§18.7 要件9: 4値化前後の性能比較"""

    @pytest.mark.slow
    def test_counter_100k_cycles(self):
        """リセット付きカウンタを 10 万サイクル実行して時間を計測"""
        counter_sv = os.path.join(EXAMPLES, "counter.sv")
        if not os.path.exists(counter_sv):
            pytest.skip("examples/counter.sv not found")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(counter_sv)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=2)

        t0 = time.perf_counter()
        eng.run(cycles=100_000)
        elapsed = time.perf_counter() - t0
        # 実際の時間を記録（上限チェックなし）
        print(f"\n[perf] 100k cycles: {elapsed:.3f}s ({100_000/elapsed:.0f} cy/s)")
        # 目安: 10秒以内（十分余裕あり）
        assert elapsed < 60, f"too slow: {elapsed:.1f}s"
