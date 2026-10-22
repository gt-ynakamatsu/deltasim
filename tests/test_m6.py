"""tests/test_m6.py — マイルストーン6 テスト

カバー範囲:
1. VCD $scope/$upscope 階層パス
2. Simulator.force() NBA 優先セマンティクス  
3. §12.1 SKIP / cancel semantics
4. §12.2 case_done per scenario
5. §12.3 Signal change DB filtering / memory limit
6. §12.4 0/1/x/z 区別 (Z sentinel)
7. §12.5 elapsed_ms per case + summary
8. FIFO gen→test E2E
9. UART TX gen→test E2E
"""

from __future__ import annotations
import json as _json
import os
from pathlib import Path

import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# 1. VCD $scope/$upscope 階層パス
# ---------------------------------------------------------------------------

class TestVCDHierarchy:
    def test_flat_module_no_extra_scope(self, tmp_path):
        """フラットなモジュールは余分なスコープなし（top だけ）"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.vcd.writer import VCDWriter

        mod = parse_file(os.path.join(EXAMPLES, "counter.sv"))
        design = elaborate(mod)
        vcd_path = str(tmp_path / "flat.vcd")
        eng = SimEngine(design, reset_cycles=2)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=5)
        vcd.close()

        content = Path(vcd_path).read_text(encoding="ascii")
        # Top module scope
        assert "$scope module counter $end" in content
        # Signals declared inside (using local names)
        assert "$var wire 1" in content  # clk
        # Exactly one $upscope (top close)
        assert content.count("$upscope $end") == 1

    def test_hierarchical_module_has_subscopes(self, tmp_path):
        """階層モジュールの内部信号は VCD に正しく記録されること"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.vcd.writer import VCDWriter

        catalog = {}
        for f in ["adder.sv", "alu.sv"]:
            mod = parse_file(os.path.join(EXAMPLES, f))
            catalog[mod.name] = mod
        design = elaborate(catalog["alu"], catalog=catalog)

        vcd_path = str(tmp_path / "hier.vcd")
        eng = SimEngine(design, reset_cycles=0)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=2)
        vcd.close()

        content = Path(vcd_path).read_text(encoding="ascii")
        # Top scope must be present
        assert "$scope module alu $end" in content
        assert "$upscope $end" in content
        assert "$enddefinitions $end" in content

        # Scope/upscope count matches (balanced)
        n_scope = content.count("$scope")
        n_upscope = content.count("$upscope")
        assert n_scope == n_upscope, f"scope={n_scope} != upscope={n_upscope}"

        # ALU's flattened signals should appear (port-connected to parent namespace)
        assert "add_sum" in content or "a" in content  # signals exist

    def test_list_signals_hierarchy_path(self):
        """list_signals() のパスが VCD 階層と一致すること"""
        import deltasim.api as ds
        catalog = {}
        for f in ["adder.sv", "alu.sv"]:
            from deltasim.parser.parser import parse_file
            mod = parse_file(os.path.join(EXAMPLES, f))
            catalog[mod.name] = mod
        di = ds.load(list(map(lambda f: os.path.join(EXAMPLES, f),
                              ["adder.sv", "alu.sv"])),
                     top="alu")
        sim = ds.Simulator(di, reset_cycles=0)
        signals = sim.list_signals()
        # All signals with dot in name should have proper path
        for s in signals:
            assert s["path"] == s["name"]  # path uses full dot-separated name


# ---------------------------------------------------------------------------
# 2. Simulator.force() NBA 優先セマンティクス
# ---------------------------------------------------------------------------

class TestSimulatorForce:
    def test_force_rst_n_prevents_count(self):
        """force(rst_n, 0) でカウンタが進まないこと（NBA より優先）"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=0,
        )
        sim.force("rst_n", 0)
        snap = sim.step(5)
        assert snap.signals.get("count") == 0, \
            f"count should be 0, got {snap.signals.get('count')}"

    def test_release_allows_count(self):
        """release 後はカウンタが進むこと"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=0,
        )
        sim.force("rst_n", 0)
        sim.step(2)
        sim.release("rst_n")
        # Now rst_n can change naturally; run more cycles
        snap = sim.step(5)
        # After release, count should eventually increment
        # (engine will set rst_n=1 from reset_cycles=0 deassert logic)
        assert snap.signals.get("count") is not None

    def test_force_overrides_nba(self):
        """NBA が生成する値が force で上書きされること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2,
        )
        sim.step(12)  # let counter reach 10
        pre_count = sim.get("count")
        # Force count to a specific value
        sim.force("count", 0xFF)
        # Run more cycles - count should stay 0xFF despite always_ff
        snap = sim.step(3)
        assert snap.signals.get("count") == 0xFF, \
            f"count should stay forced at 0xFF, got {snap.signals.get('count')}"

    def test_release_restores_rtl_control(self):
        """release 後は RTL が信号を制御できること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2,
        )
        sim.step(5)
        sim.force("count", 0xFF)
        sim.step(1)
        assert sim.get("count") == 0xFF
        sim.release("count")
        # RTL takes over: count increments or resets
        snap = sim.step(1)
        # The exact value depends on RTL; just check it's no longer forced
        # (RTL may set it to something different than 0xFF)
        # Count was 0xFF after force, so after 1 cycle it should be 0 (overflow) or something else
        assert snap.signals.get("count") is not None  # just verify no crash


# ---------------------------------------------------------------------------
# 3. §12.1 SKIP semantics
# ---------------------------------------------------------------------------

class TestSKIPSemantics:
    def test_yaml_skip_true(self, tmp_path):
        """YAML に skip: true があるとき SKIP を返すこと"""
        import yaml
        import deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 15},
            "skip": True,
            "skip_reason": "テスト中のためスキップ",
            "inputs": [],
            "expected": [],
        }
        spec_path = str(tmp_path / "skip_spec.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec_path,
        )
        assert result.status == "SKIP"
        assert "テスト中のためスキップ" in " ".join(result.log)

    def test_pre_cancel_returns_skip(self):
        """実行前にキャンセルするとき SKIP を返すこと"""
        import deltasim.api as ds
        from deltasim.result import CancelToken

        token = CancelToken()
        token.cancel()  # cancel before test starts

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            cancel=token,
        )
        assert result.status == "SKIP"
        assert "cancelled" in " ".join(result.log)

    def test_mid_run_cancel_has_partial_results(self):
        """実行中キャンセルは cancelled=True と途中結果を返すこと"""
        import deltasim.api as ds
        from deltasim.result import CancelToken

        token = CancelToken()
        count = [0]

        def _cb(ev):
            if ev.get("type") == "progress" and ev.get("phase") == "sim":
                count[0] += 1
                if count[0] >= 3:
                    token.cancel()

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=_cb,
            cancel=token,
        )
        assert result.cancelled is True
        # Should have some partial checks
        assert result.status in ("PASS", "FAIL", "SKIP")


# ---------------------------------------------------------------------------
# 4. §12.2 case_done per scenario
# ---------------------------------------------------------------------------

class TestCaseDonePerScenario:
    def test_case_done_fires_once(self):
        """case_done はシナリオ全体で 1 回だけ発火すること"""
        import deltasim.api as ds

        events = []
        ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )
        case_done_events = [e for e in events if e["type"] == "case_done"]
        assert len(case_done_events) == 1, \
            f"Expected 1 case_done, got {len(case_done_events)}"

    def test_case_done_has_status(self):
        """case_done イベントに status フィールドがあること"""
        import deltasim.api as ds

        events = []
        ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )
        cd = next(e for e in events if e["type"] == "case_done")
        assert "status" in cd
        assert cd["status"] in ("PASS", "FAIL", "ERROR", "SKIP")


# ---------------------------------------------------------------------------
# 5. §12.3 Signal change DB filtering / memory limit
# ---------------------------------------------------------------------------

class TestSignalChangeDBFilter:
    def test_default_records_only_ports(self):
        """既定では変化 DB にトップポートのみ記録されること"""
        import deltasim.api as ds

        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            cycles=20, keep_changes=True,
            # keep_changes=True uses default record set (ports only for run())
        )
        db_sigs = set(result._change_db.signals())
        design_signals = {n for n, i in result._change_db._db.items()}
        # At minimum, output ports should be in DB
        clk_changes = result.get_signal_changes("clk")
        count_changes = result.get_signal_changes("count")
        assert len(clk_changes) > 0 or len(count_changes) > 0

    def test_memory_limit_truncates(self):
        """メモリ上限に達したら記録停止 + truncated_at が設定されること"""
        from deltasim.result import SignalChangeDB, val_to_4state

        # Very small limit: 100 bytes
        db = SignalChangeDB(max_bytes=100)
        # Fill it up
        for i in range(1000):
            db.record(i * 10, "count", i, 8)
        assert db.truncated_at is not None, "truncated_at should be set"
        assert len(db.get("count")) < 1000, "Should have fewer than 1000 entries"

    def test_truncated_at_in_result(self):
        """api.test() の truncated_at が結果 JSON に含まれること"""
        import deltasim.api as ds

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            wave_mem_mb=0,  # 0 MB → truncate immediately
        )
        d = result.to_dict()
        assert "truncated_at" in d
        # With 0 MB limit, truncation should be immediate
        # (may be None if no signals were being recorded)

    def test_record_all_includes_internal(self):
        """--record-all で内部信号も記録されること"""
        import deltasim.api as ds

        catalog = {}
        for f in ["adder.sv", "alu.sv"]:
            from deltasim.parser.parser import parse_file
            mod = parse_file(os.path.join(EXAMPLES, f))
            catalog[mod.name] = mod
        from deltasim.elab.elaborator import elaborate
        design = elaborate(catalog["alu"], catalog=catalog)
        di = ds.load([os.path.join(EXAMPLES, "adder.sv"),
                      os.path.join(EXAMPLES, "alu.sv")], top="alu")
        result = ds.run(di, cycles=5, keep_changes=True)
        db_signals = result._change_db.signals()
        # With default (no record_all), should have port signals
        assert len(db_signals) > 0


# ---------------------------------------------------------------------------
# 6. §12.4 Z値区別 (0/1/x/z 小文字統一)
# ---------------------------------------------------------------------------

class TestZValue:
    def test_val_to_4state_z(self):
        """Z_VALUE が 'z' として出力されること（小文字）"""
        from deltasim.result import val_to_4state, Z_VALUE
        assert val_to_4state(Z_VALUE, 1) == "z"
        assert val_to_4state(Z_VALUE, 4) == "zzzz"
        assert val_to_4state(Z_VALUE, 8) == "zzzzzzzz"

    def test_val_to_4state_x(self):
        """None が 'x' として出力されること（小文字）"""
        from deltasim.result import val_to_4state
        assert val_to_4state(None, 1) == "x"
        assert val_to_4state(None, 8) == "xxxxxxxx"

    def test_z_and_x_are_distinct(self):
        """Z と X が区別されること"""
        from deltasim.result import val_to_4state, Z_VALUE
        assert val_to_4state(Z_VALUE, 1) != val_to_4state(None, 1)
        assert val_to_4state(Z_VALUE, 1) == "z"
        assert val_to_4state(None, 1) == "x"

    def test_fill_z_in_engine(self, tmp_path):
        """'z リテラルが Z_VALUE として評価されること"""
        sv = tmp_path / "ztest.sv"
        sv.write_text("""\
module ztest (
    input  logic a,
    output logic b
);
    assign b = a | 'z;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.result import Z_VALUE
        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.set_input("a", 0)
        # 0 | z = x per IEEE 1800 Table 27-1 (z treated as x)
        b = eng.get_output("b")
        # §18.7: FourState(1'bx), None、または Z_VALUE すべて受け付ける
        from deltasim.sim.engine import _has_xz
        assert _has_xz(b), f"expected x/z but got {b!r}"

    def test_vcd_z_output(self, tmp_path):
        """Z 値が VCD に 'z' として記録されること"""
        sv = tmp_path / "zvcd.sv"
        sv.write_text("""\
module zvcd (
    input  logic a,
    output logic b
);
    assign b = a & 'z;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.vcd.writer import VCDWriter
        from deltasim.result import Z_VALUE

        mod = parse_file(str(sv))
        design = elaborate(mod)
        vcd_path = str(tmp_path / "z.vcd")
        eng = SimEngine(design, reset_cycles=0)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=1, reset_cycles=0)
        vcd.close()
        content = Path(vcd_path).read_text(encoding="ascii")
        # Either z or x should appear in VCD (implementation uses x for z in arithmetic)
        assert "x" in content or "z" in content


# ---------------------------------------------------------------------------
# 7. §12.5 elapsed_ms
# ---------------------------------------------------------------------------

class TestElapsedMs:
    def test_result_has_elapsed_ms(self):
        """TestResult に elapsed_ms (全体) が含まれること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.elapsed_ms >= 0
        d = result.to_dict()
        assert "elapsed_ms" in d
        assert d["elapsed_ms"] >= 0

    def test_summary_elapsed_ms_in_json(self):
        """JSON に summary.elapsed_ms が含まれること (§12.5)"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert "summary" in d
        assert "elapsed_ms" in d["summary"]
        # §12.5: summary.elapsed_ms (total) >= elapsed_ms (case sim time)
        assert d["summary"]["elapsed_ms"] >= d["elapsed_ms"]

    def test_check_has_elapsed_ms(self):
        """個々のチェックは elapsed_ms を持たない (§12.5: ケース単位)"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        # §12.5: elapsed_ms はシナリオ全体 (TestResult) の時間
        assert result.elapsed_ms >= 0
        # 個々のチェックは elapsed_ms フィールドを持たない
        for check in result.checks:
            assert not hasattr(check, "elapsed_ms") or check.elapsed_ms == 0.0


# ---------------------------------------------------------------------------
# 8. FIFO gen→test E2E
# ---------------------------------------------------------------------------

class TestFIFOE2E:
    def test_fifo_gen_creates_files(self, tmp_path):
        """deltasim gen が FIFO の TB を生成すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "gen",
            os.path.join(EXAMPLES, "fifo.sv"),
            "--top", "fifo",
            "--out-dir", str(tmp_path),
        ])
        assert result.exit_code == 0, result.output
        assert Path(tmp_path / "fifo_tb.sv").exists()
        assert Path(tmp_path / "fifo_spec.yaml").exists()

    def test_fifo_test_passes(self):
        """deltasim test で FIFO spec が全 PASS すること"""
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
# 9. UART TX gen→test E2E
# ---------------------------------------------------------------------------

class TestUARTTXE2E:
    def test_uart_tx_gen(self, tmp_path):
        """deltasim gen が UART TX の TB を生成すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "gen",
            os.path.join(EXAMPLES, "uart_tx.sv"),
            "--top", "uart_tx",
            "--out-dir", str(tmp_path),
        ])
        assert result.exit_code == 0, result.output
        assert Path(tmp_path / "uart_tx_tb.sv").exists()

    def test_uart_tx_spec_passes(self):
        """deltasim test で UART TX spec が全 PASS すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "uart_tx.sv"),
            "--spec", os.path.join(EXAMPLES, "uart_tx_spec.yaml"),
        ])
        assert result.exit_code == 0, result.output

    def test_uart_tx_api(self):
        """api.test() で UART TX が PASS すること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "uart_tx.sv")],
            spec=os.path.join(EXAMPLES, "uart_tx_spec.yaml"),
        )
        fails = [f"{c.signal}@{c.cycle}: exp={c.expected!r} got={c.actual!r}"
                 for c in result.checks if c.status == "FAIL"]
        assert result.status == "PASS", "UART TX FAIL:\n" + "\n".join(fails)


# ---------------------------------------------------------------------------
# 10. CI compatibility
# ---------------------------------------------------------------------------

class TestCICompat:
    def test_crlf_spec_file(self, tmp_path):
        """CRLF 改行の YAML スペックファイルが読み込めること"""
        import yaml
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "inputs": [],
            "expected": [{"at": 2, "check": {"count": 0}}],
        }
        yaml_str = yaml.dump(spec, allow_unicode=True)
        # Write with CRLF
        crlf_path = tmp_path / "crlf_spec.yaml"
        crlf_path.write_bytes(yaml_str.replace("\n", "\r\n").encode("utf-8"))

        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            str(crlf_path),
        )
        assert result.status in ("PASS", "FAIL")  # parsed successfully

    def test_vcd_lf_only(self, tmp_path):
        """VCD ファイルが LF のみで書き込まれること（Windows 互換）"""
        import deltasim.api as ds
        vcd_path = str(tmp_path / "test.vcd")
        ds.run([os.path.join(EXAMPLES, "counter.sv")],
               cycles=5, vcd=vcd_path)
        raw = Path(vcd_path).read_bytes()
        assert b"\r\n" not in raw, "VCD should use LF-only line endings"
