"""tests/test_hierarchy.py — M3 階層インスタンスのテスト"""

import os
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def _parse_elab(files, top, params=None):
    from deltasim.parser.parser import parse_file
    from deltasim.elab.elaborator import elaborate
    catalog = {}
    for f in files:
        mod = parse_file(os.path.join(EXAMPLES, f))
        catalog[mod.name] = mod
    return elaborate(catalog[top], param_overrides=params or {}, catalog=catalog)


class TestAdderFlat:
    """adder.sv: 単一モジュールのシミュレーション（Concat LHS 含む）"""

    def test_parse(self):
        from deltasim.parser.parser import parse_file
        mod = parse_file(os.path.join(EXAMPLES, "adder.sv"))
        assert mod.name == "adder"
        assert len(mod.ports) == 4

    def test_basic_add(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv"], "adder")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 5)
        eng.set_input("b", 3)
        assert eng.get_output("sum") == 8
        assert eng.get_output("carry_out") == 0

    def test_carry(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv"], "adder")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 200)
        eng.set_input("b", 100)
        assert eng.get_output("sum") == 44    # 300 & 0xFF
        assert eng.get_output("carry_out") == 1

    def test_overflow_max(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv"], "adder")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 255)
        eng.set_input("b", 1)
        assert eng.get_output("sum") == 0
        assert eng.get_output("carry_out") == 1


class TestALUHierarchy:
    """alu.sv + adder.sv: 2 レベル階層のシミュレーション"""

    def test_elab_creates_internal_signals(self):
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        # add_sum と add_carry が内部信号として存在すること
        assert "add_sum" in design.signals
        assert "add_carry" in design.signals

    def test_elab_flattened_assign(self):
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        # adder のプロセスが ALU に flatten されていること
        assert len(design.assign_procs) >= 1

    def test_add_op(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 5)
        eng.set_input("b", 3)
        eng.set_input("op", 0b00)  # ADD
        assert eng.get_output("result") == 8
        assert eng.get_output("carry_out") == 0

    def test_sub_op(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 10)
        eng.set_input("b", 3)
        eng.set_input("op", 0b01)  # SUB
        assert eng.get_output("result") == 7

    def test_and_op(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 0b10101010)
        eng.set_input("b", 0b11001100)
        eng.set_input("op", 0b10)  # AND
        assert eng.get_output("result") == 0b10001000

    def test_or_op(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 0b10101010)
        eng.set_input("b", 0b01010101)
        eng.set_input("op", 0b11)  # OR
        assert eng.get_output("result") == 0b11111111

    def test_add_carry(self):
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 200)
        eng.set_input("b", 100)
        eng.set_input("op", 0b00)  # ADD
        assert eng.get_output("carry_out") == 1
        assert eng.get_output("result") == 44

    def test_param_width4(self):
        """WIDTH=4 パラメータ上書き"""
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["adder.sv", "alu.sv"], "alu", params={"WIDTH": 4})
        assert design.signals["a"].width == 4
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 0xF)
        eng.set_input("b", 0x1)
        eng.set_input("op", 0b00)
        assert eng.get_output("carry_out") == 1
        assert eng.get_output("result") == 0  # 0xF + 0x1 = 0x10, masked to 4-bit = 0


class TestALUWithSpec:
    """alu_spec.yaml でテスト実行"""

    def test_alu_spec_passes(self):
        from deltasim.tb.scenario import load_scenario
        from deltasim.tb.runner import TestRunner
        design = _parse_elab(["adder.sv", "alu.sv"], "alu")
        spec = load_scenario(os.path.join(EXAMPLES, "alu_spec.yaml"))
        runner = TestRunner(design, spec)
        result = runner.run()
        fails = [str(c) for c in result.checks if not c.passed]
        assert result.passed, f"FAIL:\n" + "\n".join(fails)


class TestCounterTBHierarchy:
    """counter_tb.sv を counter.sv と一緒に実行（2レベル階層 + initial ブロック）"""

    def test_parse_counter_tb(self):
        from deltasim.parser.parser import parse_file
        mod = parse_file(os.path.join(EXAMPLES, "counter_tb.sv"))
        assert mod.name == "counter_tb"

    def test_elaborate_counter_tb(self):
        design = _parse_elab(["counter_tb.sv", "counter.sv"], "counter_tb")
        # counter の seq_procs が counter_tb に flatten されること
        assert len(design.seq_procs) >= 1
        # initial ブロックが存在すること (clock/reset gen)
        assert len(design.initial_blocks) >= 1

    def test_counter_tb_run(self):
        """counter_tb.sv を直接実行して count が増加すること"""
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["counter_tb.sv", "counter.sv"], "counter_tb")
        eng = SimEngine(design, reset_cycles=0)  # TB が reset を自己駆動する
        result = eng.run(cycles=25)
        # TB の $finish は posedge 20 で呼ばれ、その時点で count=10 になっている
        assert result.finished, "TB が $finish で終了すること"
        # TB の $display で "PASS: count = 10" が出力されること
        assert any("PASS" in l for l in result.log), \
            f"TB の PASS メッセージが見つかりません: {result.log}"
        # $finish 直前の signals に count=10 が記録されていること
        assert eng.signals.get("count") == 10, \
            f"count={eng.signals.get('count')}, expected 10"

    def test_counter_tb_cli(self, tmp_path):
        """CLI で counter_tb を実行して VCD が生成されること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        vcd_path = str(tmp_path / "out.vcd")
        runner = CliRunner()
        result = runner.invoke(main, [
            "run",
            os.path.join(EXAMPLES, "counter_tb.sv"),
            os.path.join(EXAMPLES, "counter.sv"),
            "--top", "counter_tb",
            "--cycles", "25",
            "--vcd", vcd_path,
            "--reset-cycles", "0",
        ])
        assert result.exit_code == 0, result.output
        assert os.path.isfile(vcd_path)

    def test_gen_for_hierarchical(self, tmp_path):
        """deltasim gen でヘルパーモジュールを含む TB を生成できること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "gen",
            os.path.join(EXAMPLES, "adder.sv"),
            os.path.join(EXAMPLES, "alu.sv"),
            "--top", "alu",
            "--out-dir", str(tmp_path),
        ])
        assert result.exit_code == 0, result.output
        assert os.path.isfile(str(tmp_path / "alu_tb.sv"))
        assert os.path.isfile(str(tmp_path / "alu_spec.yaml"))

    def test_test_cmd_alu(self):
        """deltasim test で ALU（階層モジュール）がテスト通過すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "adder.sv"),
            os.path.join(EXAMPLES, "alu.sv"),
            "--spec", os.path.join(EXAMPLES, "alu_spec.yaml"),
        ])
        assert result.exit_code == 0, result.output


class TestGenerateBlock:
    """generate for/if のエラボレーションテスト"""

    def test_generate_for_inline(self, tmp_path):
        """generate for が正しく展開されること"""
        sv = tmp_path / "gen_test.sv"
        sv.write_text("""\
module gen_test #(parameter N=4) (
    input  logic [N-1:0] in,
    output logic [N-1:0] out
);
    generate
        genvar i;
        for (i = 0; i < N; i = i + 1) begin : gen_i
            assign out[i] = in[i];
        end
    endgenerate
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        mod = parse_file(str(sv))
        design = elaborate(mod, param_overrides={"N": 4})
        # 4 assign statements should be generated
        assert len(design.assign_procs) == 4

    def test_generate_if_inline(self, tmp_path):
        """generate if が条件に応じて展開されること"""
        sv = tmp_path / "genif_test.sv"
        sv.write_text("""\
module genif_test #(parameter USE_EXTRA=1) (
    input  logic a,
    output logic b
);
    generate
        if (USE_EXTRA) begin
            assign b = ~a;
        end else begin
            assign b = a;
        end
    endgenerate
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine

        mod = parse_file(str(sv))
        design_inv = elaborate(mod, param_overrides={"USE_EXTRA": 1})
        eng = SimEngine(design_inv, reset_cycles=0)
        eng.set_input("a", 1)
        assert eng.get_output("b") == 0   # ~1 = 0

        design_pass = elaborate(mod, param_overrides={"USE_EXTRA": 0})
        eng2 = SimEngine(design_pass, reset_cycles=0)
        eng2.set_input("a", 1)
        assert eng2.get_output("b") == 1   # pass through


class TestForWhileInAlways:
    """always 内 for/while ループのシミュレーション"""

    def test_for_in_always_comb(self, tmp_path):
        """always_comb 内の for ループが動作すること（popcount）"""
        sv = tmp_path / "popcount.sv"
        sv.write_text("""\
module popcount (
    input  logic [7:0] data,
    output logic [3:0] count
);
    integer i;
    always_comb begin
        count = 0;
        for (i = 0; i < 8; i = i + 1) begin
            if (data[i])
                count = count + 1;
        end
    end
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("data", 0b10110101)  # popcount = 5
        assert eng.get_output("count") == 5


class TestXZLogic:
    """X/Z 4値論理の基本動作テスト"""

    def test_fill_literal_x(self, tmp_path):
        """'x リテラルが X として伝搬すること"""
        sv = tmp_path / "xtest.sv"
        sv.write_text("""\
module xtest (
    input  logic a,
    output logic b
);
    assign b = a | 'x;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 0)
        # 0 | X = X (any bit is unknown) — §18.7: FourState または None
        b = eng.get_output("b")
        from deltasim.sim.engine import _has_xz
        assert _has_xz(b), f"expected x but got {b!r}"

    def test_x_in_vcd(self, tmp_path):
        """X 値が VCD に x として出力されること"""
        sv = tmp_path / "xvcd.sv"
        sv.write_text("""\
module xvcd (
    input  logic a,
    output logic b
);
    assign b = a & 'x;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.vcd.writer import VCDWriter
        mod = parse_file(str(sv))
        design = elaborate(mod)
        vcd_path = str(tmp_path / "x.vcd")
        eng = SimEngine(design, reset_cycles=0)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=1, reset_cycles=0)
        vcd.close()
        content = open(vcd_path).read()
        assert "x" in content or "X" in content  # x 値が VCD に含まれること
