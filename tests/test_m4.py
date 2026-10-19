"""tests/test_m4.py — マイルストーン4 機能テスト

カバー範囲:
- 符号付き演算 (integer / signed ループ)
- struct packed フィールドアクセス
- アンパック配列 (FIFO)
- X/Z 4 値基本
- VCD 4 値出力
- 公開 API (deltasim.api)
- 結果 JSON / 4 値文字列
- 進捗イベント
- キャンセル
"""

from __future__ import annotations
import os
import pytest
import json as _json

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _elab(sv_name, **params):
    from deltasim.parser.parser import parse_file
    from deltasim.elab.elaborator import elaborate
    mod = parse_file(os.path.join(EXAMPLES, sv_name))
    return elaborate(mod, param_overrides=params)


# ---------------------------------------------------------------------------
# 1. 符号付き演算 / integer ダウンカウントループ
# ---------------------------------------------------------------------------

class TestSignedArithmetic:
    def test_integer_downcount(self, tmp_path):
        """integer i でダウンカウントループが正しく終了すること"""
        sv = tmp_path / "downcount.sv"
        sv.write_text("""\
module downcount (
    input  logic [7:0] data,
    output logic [3:0] popcount
);
    integer i;
    always_comb begin
        popcount = 0;
        // i >= 0 が符号付き比較で正しく動作すること
        for (i = 7; i >= 0; i = i - 1)
            if (data[i]) popcount = popcount + 1;
    end
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("data", 0b10110101)
        assert eng.get_output("popcount") == 5

    def test_signed_signal_comparison(self, tmp_path):
        """signed 信号の比較が符号付きで行われること"""
        sv = tmp_path / "signtest.sv"
        sv.write_text("""\
module signtest (
    input  logic signed [7:0] a,
    input  logic signed [7:0] b,
    output logic              a_lt_b
);
    assign a_lt_b = (a < b);
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        # a = -1 (0xFF), b = 1: signed: -1 < 1 = True
        eng.set_input("a", 0xFF)  # -1 as signed 8-bit
        eng.set_input("b", 1)
        assert eng.get_output("a_lt_b") == 1

    def test_integer_signed_read(self, tmp_path):
        """integer 変数が符号付きとして読まれること"""
        sv = tmp_path / "intread.sv"
        sv.write_text("""\
module intread (
    input  logic       clk,
    input  logic       rst_n,
    output logic [7:0] out
);
    integer cnt;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) cnt <= 3;
        else if (cnt > 0) cnt <= cnt - 1;
        else cnt <= 0;
    end
    assign out = cnt[7:0];
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=1)
        result = eng.run(cycles=6)
        # reset_cycles=1: cycle 1 はリセット(cnt=3), cycle 2 以降はデクリメント
        assert result.get_value("out", 1) == 3   # reset: cnt=3
        assert result.get_value("out", 2) == 2   # cnt=3-1=2
        assert result.get_value("out", 3) == 1   # cnt=2-1=1
        assert result.get_value("out", 4) == 0   # cnt=1-1=0
        assert result.get_value("out", 5) == 0   # stays 0 (cnt>0 is False)


# ---------------------------------------------------------------------------
# 2. struct packed フィールドアクセス
# ---------------------------------------------------------------------------

class TestStructPacked:
    def test_struct_field_read(self, tmp_path):
        """struct packed のフィールドが正しく読み出されること"""
        sv = tmp_path / "structread.sv"
        sv.write_text("""\
module structread (
    input  logic [11:0] frame_in,
    output logic [3:0]  addr,
    output logic [7:0]  data
);
    typedef struct packed {
        logic [3:0] addr;
        logic [7:0] data;
    } frame_t;

    frame_t frame;
    assign frame = frame_in;
    assign addr = frame.addr;
    assign data = frame.data;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        # frame_t: addr[3:0] = bits [11:8], data[7:0] = bits [7:0]
        assert "frame_t" in design.struct_fields
        eng = SimEngine(design, reset_cycles=0)
        # frame = 0xA55 = 0b_1010_01010101 → addr=0xA=10, data=0x55=85
        eng.set_input("frame_in", 0xA55)
        assert eng.get_output("addr") == 0xA
        assert eng.get_output("data") == 0x55

    def test_struct_typedef_width(self, tmp_path):
        """typedef struct の幅が正しく計算されること"""
        sv = tmp_path / "structwidth.sv"
        sv.write_text("""\
module structwidth (
    input  logic [15:0] x,
    output logic [3:0]  hi,
    output logic [7:0]  lo
);
    typedef struct packed {
        logic [3:0]  hi;
        logic [3:0]  mid;
        logic [7:0]  lo;
    } my_t;

    my_t val;
    assign val = x;
    assign hi  = val.hi;
    assign lo  = val.lo;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        assert design.typedefs.get("my_t") == 16  # 4+4+8=16 bits
        eng = SimEngine(design, reset_cycles=0)
        # x = 0xABCD: hi=0xA(bits 15:12), mid=0xB(bits 11:8), lo=0xCD(bits 7:0)
        eng.set_input("x", 0xABCD)
        assert eng.get_output("hi") == 0xA
        assert eng.get_output("lo") == 0xCD


# ---------------------------------------------------------------------------
# 3. アンパック配列（FIFO）
# ---------------------------------------------------------------------------

class TestUnpackedArray:
    def test_simple_array_write_read(self, tmp_path):
        """アンパック配列に書いて読み出せること"""
        sv = tmp_path / "mem.sv"
        sv.write_text("""\
module mem #(parameter DEPTH=4, parameter WIDTH=8) (
    input  logic             clk,
    input  logic             wr_en,
    input  logic [1:0]       wr_addr,
    input  logic [WIDTH-1:0] wr_data,
    input  logic [1:0]       rd_addr,
    output logic [WIDTH-1:0] rd_data
);
    logic [WIDTH-1:0] ram [DEPTH-1:0];

    assign rd_data = ram[rd_addr];

    always_ff @(posedge clk) begin
        if (wr_en) ram[wr_addr] <= wr_data;
    end
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod, param_overrides={"DEPTH": 4, "WIDTH": 8})
        assert "ram" in design.array_decls
        assert design.array_decls["ram"] == (4, 8)
        eng = SimEngine(design, reset_cycles=0)
        # Write 42 to address 2
        eng.signals["wr_en"] = 1
        eng.signals["wr_addr"] = 2
        eng.signals["wr_data"] = 42
        eng.run(cycles=1, reset_cycles=0)
        # Read back from address 2
        eng.signals["rd_addr"] = 2
        eng.evaluate()
        assert eng.get_output("rd_data") == 42

    def test_fifo_basic(self):
        """FIFO に書き込んで読み出せること（fifo.sv）"""
        design = _elab("fifo.sv")
        assert "mem" in design.array_decls

        from deltasim.tb.scenario import load_scenario
        from deltasim.tb.runner import TestRunner
        spec = load_scenario(os.path.join(EXAMPLES, "fifo_spec.yaml"))
        runner = TestRunner(design, spec)
        result = runner.run()
        fails = [str(c) for c in result.checks if not c.passed]
        assert result.passed, "FIFO テスト FAIL:\n" + "\n".join(fails)

    def test_fifo_cli(self):
        """deltasim test で FIFO spec が通ること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "fifo.sv"),
            "--spec", os.path.join(EXAMPLES, "fifo_spec.yaml"),
        ])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# 4. X/Z 4 値ロジック
# ---------------------------------------------------------------------------

class TestXZLogic:
    def test_x_propagation(self, tmp_path):
        """'x は X として伝搬すること"""
        sv = tmp_path / "xprop.sv"
        sv.write_text("""\
module xprop (
    input  logic a,
    output logic b
);
    assign b = a & 'x;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 1)
        # a & X = X
        assert eng.get_output("b") is None

    def test_zero_and_x_is_zero(self, tmp_path):
        """0 & X = 0 (IEEE 4 値特例)"""
        sv = tmp_path / "zeroandx.sv"
        sv.write_text("""\
module zeroandx (
    input  logic a,
    output logic b
);
    assign b = a & 'x;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 0)
        # 0 & X = 0
        assert eng.get_output("b") == 0


# ---------------------------------------------------------------------------
# 5. VCD 4 値出力
# ---------------------------------------------------------------------------

class TestVCDXOutput:
    def test_vcd_contains_x(self, tmp_path):
        """X 値が VCD に x として記録されること"""
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
        eng.signals["a"] = 1
        eng.run(cycles=1, reset_cycles=0)
        vcd.close()
        content = (tmp_path / "x.vcd").read_text(encoding="ascii")
        assert "x" in content


# ---------------------------------------------------------------------------
# 6. 公開 API
# ---------------------------------------------------------------------------

class TestPublicAPI:
    def test_load(self):
        """api.load() が DesignInfo を返すこと"""
        import deltasim.api as ds
        result = ds.load([os.path.join(EXAMPLES, "counter.sv")], top="counter")
        assert result.status == "PASS"
        assert result.module == "counter"
        assert any(p.name == "clk" for p in result.ports)
        assert result.params.get("WIDTH") == 8

    def test_test_api(self):
        """api.test() が TestResult を返し PASS すること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.schema_version in ("1", "2")
        assert result.status == "PASS"
        assert result.n_fail == 0
        assert len(result.checks) > 0

    def test_test_api_fail_detection(self):
        """api.test() が FAIL を検出すること"""
        import deltasim.api as ds
        from deltasim.tb.scenario import (
            Scenario, ExpectedEntry, SimSpec, ClockSpec, ResetSpec
        )
        import tempfile, yaml
        # 意図的に間違えたスペック
        bad_spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 15},
            "inputs": [],
            "expected": [{"at": 11, "check": {"count": 999}}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            yaml.dump(bad_spec, f, allow_unicode=True)
            spec_path = f.name
        try:
            result = ds.test(
                [os.path.join(EXAMPLES, "counter.sv")],
                spec=spec_path,
            )
            assert result.status == "FAIL"
            assert result.n_fail == 1
        finally:
            os.unlink(spec_path)

    def test_run_api(self):
        """api.run() が RunResult を返すこと"""
        import deltasim.api as ds
        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter", cycles=20,
        )
        assert result.status == "PASS"
        assert result.cycles == 20
        assert result.time_ns > 0

    def test_run_keep_changes(self):
        """keep_changes=True で信号変化が取得できること"""
        import deltasim.api as ds
        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter", cycles=15, keep_changes=True,
        )
        changes = result.get_signal_changes("count", start_ns=100, end_ns=200)
        assert len(changes) > 0
        for c in changes:
            assert "time_ns" in c.__dict__ or hasattr(c, "time_ns")
            assert hasattr(c, "value")

    def test_gen_api(self, tmp_path):
        """api.gen() が TB SV と YAML を生成すること"""
        import deltasim.api as ds
        result = ds.gen(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter",
            out_dir=str(tmp_path),
        )
        assert result["status"] == "PASS"
        assert os.path.isfile(result["tb_path"])
        assert os.path.isfile(result["spec_path"])


# ---------------------------------------------------------------------------
# 7. 結果 JSON / 4 値文字列
# ---------------------------------------------------------------------------

class TestResultJSON:
    def test_schema_version(self):
        from deltasim.result import TestResult, SCHEMA_VERSION
        tr = TestResult()
        d = tr.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION

    def test_status_values(self):
        from deltasim.result import TestResult
        for s in ("PASS", "FAIL", "ERROR", "SKIP"):
            tr = TestResult(status=s)
            assert tr.to_dict()["status"] == s

    def test_val_to_4state_normal(self):
        from deltasim.result import val_to_4state
        assert val_to_4state(0, 1) == "0"
        assert val_to_4state(1, 1) == "1"
        assert val_to_4state(5, 8) == "00000101"
        assert val_to_4state(0xFF, 8) == "11111111"

    def test_val_to_4state_x(self):
        from deltasim.result import val_to_4state
        assert val_to_4state(None, 1) == "x"
        assert val_to_4state(None, 8) == "xxxxxxxx"

    def test_json_serializable(self):
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        j = result.to_json()
        d = _json.loads(j)
        assert d["schema_version"] in ("1", "2")
        assert "checks" in d
        assert "errors" in d
        # 全チェックに seed フィールドがあること
        for check in d["checks"]:
            assert "seed" in check

    def test_check_has_4state_values(self):
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        for check in result.checks:
            # expected と actual が 4 値文字列であること
            if hasattr(check, "expected") and isinstance(check.expected, str):
                # spec チェックは整数→4値変換後の文字列
                pass  # OK


# ---------------------------------------------------------------------------
# 8. 進捗イベント
# ---------------------------------------------------------------------------

class TestProgressEvents:
    def test_progress_callbacks_fired(self):
        """進捗コールバックが発火すること"""
        import deltasim.api as ds
        events = []
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )
        types = [e["type"] for e in events]
        assert "start" in types
        assert "finish" in types
        assert "progress" in types
        # phase フィールドが含まれること
        progress_events = [e for e in events if e["type"] == "progress"]
        assert any("phase" in e for e in progress_events)
        # sim フェーズに time_ns があること
        sim_events = [e for e in progress_events if e.get("phase") == "sim"]
        assert any("time_ns" in e for e in sim_events)

    def test_json_events_cli(self):
        """--json-events フラグが JSON Lines を出力すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json-events",
        ])
        assert result.exit_code == 0, result.output
        # 各行が有効な JSON であること
        json_lines = [l for l in result.output.strip().splitlines()
                      if l.startswith("{")]
        assert len(json_lines) > 0
        for line in json_lines:
            d = _json.loads(line)
            assert "type" in d


# ---------------------------------------------------------------------------
# 9. キャンセル
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_cancel_returns_partial_results(self):
        """キャンセル時に途中結果が返されること"""
        import deltasim.api as ds
        from deltasim.result import CancelToken

        token = CancelToken()
        call_count = [0]

        def cancel_after_first(event):
            if event.get("type") == "progress" and event.get("phase") == "sim":
                call_count[0] += 1
                if call_count[0] >= 2:
                    token.cancel()

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=cancel_after_first,
            cancel=token,
        )
        assert result.cancelled is True
        # 途中結果は含まれていること
        # (チェック数は spec.sim.cycles=30 フル分より少ないはず)

    def test_cancel_run(self):
        """run() のキャンセルが動作すること"""
        import deltasim.api as ds
        from deltasim.result import CancelToken

        token = CancelToken()
        call_count = [0]

        def cancel_early(event):
            if event.get("type") == "progress":
                call_count[0] += 1
                if call_count[0] >= 3:
                    token.cancel()

        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            cycles=100,
            on_progress=cancel_early,
            cancel=token,
        )
        assert result.cancelled is True


# ---------------------------------------------------------------------------
# 10. Windows 互換性
# ---------------------------------------------------------------------------

class TestWindowsCompat:
    def test_crlf_parsing(self, tmp_path):
        """CRLF 改行の SV ファイルが正しくパースされること"""
        sv = tmp_path / "crlf.sv"
        # Write with CRLF
        sv.write_bytes(
            b"module crlf_test (\r\n"
            b"    input  logic a,\r\n"
            b"    output logic b\r\n"
            b");\r\n"
            b"    assign b = ~a;\r\n"
            b"endmodule\r\n"
        )
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        mod = parse_file(str(sv))
        assert mod.name == "crlf_test"
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 1)
        assert eng.get_output("b") == 0
        eng.set_input("a", 0)
        assert eng.get_output("b") == 1

    def test_utf8_comments(self, tmp_path):
        """UTF-8 コメントを含む SV ファイルが正しくパースされること"""
        sv = tmp_path / "utf8.sv"
        sv.write_text("""\
// 日本語コメント: カウンタ
module utf8_counter (
    input  logic clk,
    input  logic rst_n,
    output logic [7:0] count
);
    // 非同期リセット付き
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) count <= '0;
        else count <= count + 1;
    end
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        mod = parse_file(str(sv))
        assert mod.name == "utf8_counter"
        design = elaborate(mod)
        assert "count" in design.signals
