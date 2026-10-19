"""tests/test_fsm.py — M3 FSM (typedef enum) + 組み合わせ回路テスト"""

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


class TestTypedefEnum:
    """typedef enum の解析・シミュレーションテスト"""

    def test_parse_seq_detect(self):
        from deltasim.parser.parser import parse_file
        mod = parse_file(os.path.join(EXAMPLES, "seq_detect.sv"))
        assert mod.name == "seq_detect"
        # typedef enum があること
        from deltasim.parser.ast_nodes import TypedefDecl
        typedef_items = [i for i in mod.items if isinstance(i, TypedefDecl)]
        assert len(typedef_items) == 1

    def test_elaborate_enum_params(self):
        """enum メンバーが params に追加されること"""
        design = _parse_elab(["seq_detect.sv"], "seq_detect")
        assert design.params.get("S_IDLE") == 0
        assert design.params.get("S_1") == 1
        assert design.params.get("S_10") == 2
        assert design.params.get("S_101") == 3

    def test_elaborate_enum_signal_width(self):
        """enum 型変数が正しいビット幅を持つこと"""
        design = _parse_elab(["seq_detect.sv"], "seq_detect")
        assert design.signals["state"].width == 2
        assert design.signals["nxt"].width == 2

    def test_fsm_idle_state(self):
        """リセット後は S_IDLE=0 であること"""
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["seq_detect.sv"], "seq_detect")
        eng = SimEngine(design, reset_cycles=2)
        result = eng.run(cycles=2)
        assert result.get_value("state", 1) == 0  # S_IDLE
        assert result.get_value("state", 2) == 0

    def test_fsm_transition_1(self):
        """din=1 でリセット後に S_1(=1) へ遷移すること"""
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["seq_detect.sv"], "seq_detect")
        eng = SimEngine(design, reset_cycles=2)
        result = eng.run(3, pre_posedge_hook=lambda c, s: eng._write("din", 1) if c >= 3 else None)
        # cycle 3 after posedge: state = S_1 = 1
        assert result.get_value("state", 3) == 1, \
            f"state@3 = {result.get_value('state', 3)}, expected 1 (S_1)"

    def test_fsm_full_pattern(self):
        """1011 パターンを入力して detected=1 になること"""
        from deltasim.sim.engine import SimEngine
        design = _parse_elab(["seq_detect.sv"], "seq_detect")
        eng = SimEngine(design, reset_cycles=2)

        din_seq = {3: 1, 4: 0, 5: 1, 6: 1, 7: 0}  # sequence: 1, 0, 1, 1, 0

        def pre_hook(cycle, signals):
            if cycle in din_seq:
                eng._write("din", din_seq[cycle])

        result = eng.run(cycles=8, pre_posedge_hook=pre_hook)

        # cycle 5 posedge: state = S_101, din=1 → detected=1
        assert result.get_value("detected", 5) == 1, \
            f"detected@5={result.get_value('detected', 5)}, expected 1"
        # cycle 7: din=0, detected=0
        assert result.get_value("detected", 7) == 0


class TestSeqDetectWithSpec:
    """seq_detect_spec.yaml でテスト実行"""

    def test_seq_detect_spec_passes(self):
        from deltasim.tb.scenario import load_scenario
        from deltasim.tb.runner import TestRunner
        design = _parse_elab(["seq_detect.sv"], "seq_detect")
        spec = load_scenario(os.path.join(EXAMPLES, "seq_detect_spec.yaml"))
        runner = TestRunner(design, spec)
        result = runner.run()
        fails = [str(c) for c in result.checks if not c.passed]
        assert result.passed, f"FAIL:\n" + "\n".join(fails)

    def test_seq_detect_cli(self):
        """deltasim test コマンドが通ること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "seq_detect.sv"),
            "--spec", os.path.join(EXAMPLES, "seq_detect_spec.yaml"),
        ])
        assert result.exit_code == 0, result.output


class TestForWhileInAlwaysFSM:
    """always 内 for ループを使った集計回路"""

    def test_priority_encoder(self, tmp_path):
        """優先エンコーダ（for ループ使用）- 最上位ビット優先"""
        sv = tmp_path / "penc.sv"
        sv.write_text("""\
module penc #(parameter W=8) (
    input  logic [W-1:0] req,
    output logic [2:0]   grant,
    output logic         valid
);
    integer i;
    always_comb begin
        grant = 0;
        valid = 0;
        // アップカウントでスキャン: 最後にヒットしたビットが最高優先度
        for (i = 0; i < W; i = i + 1) begin
            if (req[i]) begin
                grant = i[2:0];
                valid = 1;
            end
        end
    end
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod, param_overrides={"W": 8})
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("req", 0b00101000)  # bit 3 and 5 set → highest = 5
        assert eng.get_output("grant") == 5
        assert eng.get_output("valid") == 1

        eng.set_input("req", 0)
        assert eng.get_output("valid") == 0
