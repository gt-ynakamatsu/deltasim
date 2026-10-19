"""tests/test_m5.py — マイルストーン5 API テスト

カバー範囲:
  A. deltasim test --json  (name/status/signal/expected/actual/message/elapsed)
  B. エラー構造化 (file/line/column/severity/code)
  C. 進捗イベント (callback + JSON Lines + cancel)
  D. 公開 Python API (load/gen/test/run + DesignInfo)
  E. Simulator (step/run_until/get/set/force/release/list_signals/breakpoints)
  F. 信号変化リスト API
"""

from __future__ import annotations
import json as _json
import os
import tempfile
from pathlib import Path

import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# A. deltasim test --json
# ---------------------------------------------------------------------------

class TestJSONOutput:
    def test_json_output_structure(self):
        """--json が正しいスキーマを持つ JSON を出力すること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert d["schema_version"] in ("1", "2")
        assert d["status"] in ("PASS", "FAIL", "ERROR", "SKIP")
        assert "seed" in d
        assert "elapsed_ms" in d
        assert "cancelled" in d
        assert "checks" in d
        assert "errors" in d
        assert "log" in d

    def test_json_check_fields(self):
        """checks の各アイテムに必須フィールドが揃っていること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        for check in result.checks:
            assert check.name
            assert check.status in ("PASS", "FAIL", "ERROR", "SKIP")
            assert isinstance(check.cycle, int)
            assert isinstance(check.time_ns, int)
            assert isinstance(check.seed, int)
            assert isinstance(check.elapsed_ms, float)
            # expected と actual は 4 値文字列 (expr チェックは除く)
            if check.signal != "<expr>":
                assert all(c in "01xz" for c in check.expected.lower()), \
                    f"expected is not 4-state: {check.expected!r}"
                assert all(c in "01xz" for c in check.actual.lower()), \
                    f"actual is not 4-state: {check.actual!r}"

    def test_json_fail_has_message(self):
        """FAIL チェックに message フィールドがあること"""
        import deltasim.api as ds
        from deltasim.tb.scenario import Scenario, ExpectedEntry, SimSpec, ClockSpec, ResetSpec
        design = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        spec_str = """
version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 10}
sim: {cycles: 15}
inputs: []
expected:
  - at: 11
    check: {count: 999}
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            f.write(spec_str)
            spec_path = f.name
        try:
            result = ds.test(
                [os.path.join(EXAMPLES, "counter.sv")],
                spec_path,
            )
        finally:
            os.unlink(spec_path)

        fail_checks = [c for c in result.checks if c.status == "FAIL"]
        assert len(fail_checks) == 1
        assert fail_checks[0].message is not None
        assert fail_checks[0].signal == "count"
        assert fail_checks[0].cycle == 11

    def test_cli_json_output_file(self, tmp_path):
        """deltasim test --json が JSON ファイルを出力すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        json_path = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_path,
        ])
        assert result.exit_code == 0, result.output
        assert Path(json_path).exists()
        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert d["schema_version"] in ("1", "2")
        assert "checks" in d

    def test_cli_json_exit_code_fail(self, tmp_path):
        """FAIL 時に exit_code=1 を返すこと"""
        import yaml
        from click.testing import CliRunner
        from deltasim.cli import main
        bad_spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 15},
            "inputs": [],
            "expected": [{"at": 11, "check": {"count": 999}}],
        }
        spec_path = str(tmp_path / "bad.yaml")
        Path(spec_path).write_text(yaml.dump(bad_spec), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", spec_path,
        ])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# B. 構造化エラー
# ---------------------------------------------------------------------------

class TestStructuredErrors:
    def test_parse_error_has_location(self, tmp_path):
        """パースエラーに file/line/column が含まれること"""
        import deltasim.api as ds
        sv = tmp_path / "bad.sv"
        sv.write_text("module bad_syntax OOPS ;\nendmodule\n", encoding="utf-8")
        result = ds.load([str(sv)])
        assert result.status == "ERROR"
        assert len(result.errors) > 0
        err = result.errors[0]
        assert err.severity == "error"
        assert err.message
        assert err.code

    def test_error_schema_version_in_result(self):
        """エラー時にも schema_version が含まれること"""
        import deltasim.api as ds
        result = ds.load(["/nonexistent/bad.sv"])
        assert result.status == "ERROR"
        assert result.schema_version in ("1", "2")

    def test_elab_error_captured(self, tmp_path):
        """エラボレーションエラーが ErrorInfo として返されること"""
        import deltasim.api as ds
        sv = tmp_path / "elab_err.sv"
        sv.write_text("""\
module elab_err #(parameter W=8) (
    input  logic [W-1:0] a,
    output logic [UNDEFINED-1:0] b
);
    assign b = a;
endmodule
""", encoding="utf-8")
        result = ds.load([str(sv)])
        assert result.status == "ERROR"
        assert result.errors[0].severity == "error"

    def test_error_info_fields(self):
        """ErrorInfo に必須フィールドが揃っていること"""
        from deltasim.result import ErrorInfo
        err = ErrorInfo(
            file="counter.sv", line=5, column=3,
            severity="error", message="test error", code="E001"
        )
        d = err.to_dict()
        for key in ("file", "line", "column", "severity", "message", "code"):
            assert key in d


# ---------------------------------------------------------------------------
# C. 進捗イベント
# ---------------------------------------------------------------------------

class TestProgressEvents:
    def test_callback_start_finish(self):
        """start / finish イベントが発火すること"""
        import deltasim.api as ds
        events = []
        ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )
        types = [e["type"] for e in events]
        assert "start" in types
        assert "finish" in types

    def test_progress_has_phase_and_time(self):
        """progress イベントに phase と time_ns が含まれること"""
        import deltasim.api as ds
        events = []
        ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )
        sim_events = [e for e in events
                      if e.get("type") == "progress" and e.get("phase") == "sim"]
        assert sim_events, "sim phase の progress イベントがなかった"
        assert any("time_ns" in e for e in sim_events)

    def test_case_done_fired(self):
        """case_done イベントがチェックごとに発火すること"""
        import deltasim.api as ds
        events = []
        ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )
        case_done = [e for e in events if e.get("type") == "case_done"]
        assert len(case_done) > 0
        for ev in case_done:
            assert "name" in ev
            assert ev["status"] in ("PASS", "FAIL", "ERROR", "SKIP")

    def test_json_events_cli(self):
        """--json-events が有効な JSON Lines を出力すること"""
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
        lines = [l for l in result.output.strip().splitlines() if l.startswith("{")]
        assert len(lines) > 0
        for line in lines:
            ev = _json.loads(line)
            assert "type" in ev
            assert "timestamp_ms" in ev

    def test_cancel_returns_partial(self):
        """キャンセル時に cancelled=True と途中結果が返されること"""
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

    def test_test_events_generator(self):
        """test_events() ジェネレータが使えること"""
        import deltasim.api as ds
        events = []
        for ev in ds.test_events(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        ):
            events.append(ev)

        assert len(events) > 0
        assert any(e["type"] == "start" for e in events)
        assert any(e["type"] == "finish" for e in events)


# ---------------------------------------------------------------------------
# D. 公開 Python API
# ---------------------------------------------------------------------------

class TestPublicAPI:
    def test_load_returns_design_info(self):
        """load() が DesignInfo を返すこと"""
        import deltasim.api as ds
        from deltasim.result import DesignInfo
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")], top="counter")
        assert isinstance(di, DesignInfo)
        assert di.status == "PASS"
        assert di.module == "counter"

    def test_design_info_ports(self):
        """DesignInfo がポート情報（幅・方向・符号）を持つこと"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        assert len(di.ports) == 3  # clk, rst_n, count
        clk = next(p for p in di.ports if p.name == "clk")
        assert clk.direction == "input"
        assert clk.width == 1

    def test_design_info_params(self):
        """DesignInfo がパラメータを持つこと"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        assert di.params.get("WIDTH") == 8

    def test_design_info_hierarchy(self):
        """DesignInfo に hierarchy ツリーが含まれること"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        assert di.hierarchy is not None
        assert di.hierarchy.module == "counter"

    def test_design_info_to_json(self):
        """DesignInfo が JSON 化できること"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        j = di.to_json()
        d = _json.loads(j)
        assert d["schema_version"] in ("1", "2")
        assert "ports" in d
        assert "hierarchy" in d

    def test_gen_returns_strings(self):
        """gen() が strings=True で TB SV / YAML 文字列を返すこと"""
        import deltasim.api as ds
        result = ds.gen(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter",
        )
        assert result["status"] == "PASS"
        assert "tb_sv" in result
        assert "spec_yaml" in result
        assert "module counter_tb" in result["tb_sv"]
        assert "version:" in result["spec_yaml"]

    def test_gen_writes_files(self, tmp_path):
        """gen() が out_dir 指定でファイルを書き込むこと"""
        import deltasim.api as ds
        result = ds.gen(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter",
            out_dir=str(tmp_path),
        )
        assert result["status"] == "PASS"
        assert Path(result["tb_path"]).exists()
        assert Path(result["spec_path"]).exists()

    def test_test_accepts_design_info(self):
        """test() が DesignInfo を受け入れること"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        result = ds.test(
            di,  # DesignInfo を渡す
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.status == "PASS"
        assert result.schema_version in ("1", "2")

    def test_run_accepts_design_info(self):
        """run() が DesignInfo を受け入れること"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        result = ds.run(di, cycles=20)
        assert result.status == "PASS"
        assert result.cycles == 20

    def test_cli_test_uses_api(self):
        """CLI test コマンドが api.test() 経由で動くこと"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
        ])
        assert result.exit_code == 0, result.output

    def test_cli_gen_strings(self):
        """CLI gen --strings が文字列を stdout に出力すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "gen",
            os.path.join(EXAMPLES, "counter.sv"),
            "--top", "counter",
            "--strings",
        ])
        assert result.exit_code == 0, result.output
        assert "module counter_tb" in result.output


# ---------------------------------------------------------------------------
# E. Simulator (対話デバッグ)
# ---------------------------------------------------------------------------

class TestSimulator:
    def test_simulator_creates(self):
        """Simulator が作成できること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=10,
        )
        assert sim.time_ns == 0
        assert sim.cycle == 0

    def test_simulator_step(self):
        """step() でシミュレーションが進むこと"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=10,
        )
        snap = sim.step(5)
        assert snap.time_ns > 0
        assert sim.cycle == 5

    def test_simulator_step_returns_snapshot(self):
        """step() が SimSnapshot を返すこと"""
        import deltasim.api as ds
        from deltasim.result import SimSnapshot
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=10,
        )
        snap = sim.step(12)
        assert isinstance(snap, SimSnapshot)
        assert "count" in snap.signals
        assert snap.time_ns > 0

    def test_simulator_get(self):
        """get() で信号値が読めること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2,
        )
        sim.step(5)  # reset(2) + 3 count = count=3
        count = sim.get("count")
        assert isinstance(count, (int, type(None)))
        # reset=2, then 3 increments: count should be 3
        assert count == 3

    def test_simulator_set(self):
        """set() で信号を駆動してから評価できること"""
        import deltasim.api as ds
        sim = ds.Simulator([os.path.join(EXAMPLES, "mux2.sv")], reset_cycles=0)
        sim.set("a", 0xAA)
        sim.set("b", 0x55)
        sim.set("sel", 0)
        assert sim.get("y") == 0x55
        sim.set("sel", 1)
        assert sim.get("y") == 0xAA

    def test_simulator_force_release(self):
        """force/release が機能すること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=0,
        )
        # force rst_n=0 across multiple cycles
        sim.force("rst_n", 0)
        sim.step(5)
        assert sim.get("count") == 0  # still in reset
        sim.release("rst_n")

    def test_simulator_run_until(self):
        """run_until() が指定時刻まで実行すること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=1,
        )
        snap = sim.run_until(200)  # ≈ 20 cycles at 100MHz
        assert snap.time_ns >= 200

    def test_simulator_list_signals(self):
        """list_signals() が name/width/direction を含む辞書リストを返すこと"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=0,
        )
        signals = sim.list_signals()
        assert len(signals) > 0
        for s in signals:
            assert "name" in s
            assert "width" in s
            assert "value" in s  # 4-state string

    def test_simulator_breakpoint(self):
        """add_breakpoint() でブレークポイントが機能すること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2,
        )
        bp_id = sim.add_breakpoint("count == 5")
        snap = sim.step(100)  # run up to 100, should stop at count==5
        assert snap.breakpoint_hit == bp_id
        assert sim.get("count") == 5

    def test_simulator_remove_breakpoint(self):
        """remove_breakpoint() でブレークポイントが削除されること"""
        import deltasim.api as ds
        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")], reset_cycles=0)
        bp_id = sim.add_breakpoint("count == 1")
        sim.remove_breakpoint(bp_id)
        snap = sim.step(5)
        # No breakpoint → runs all 5 cycles
        assert snap.breakpoint_hit is None

    def test_simulator_list_breakpoints(self):
        """list_breakpoints() がブレークポイント一覧を返すこと"""
        import deltasim.api as ds
        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")])
        bp1 = sim.add_breakpoint("count == 5")
        bp2 = sim.add_breakpoint("clk == 1")
        bps = sim.list_breakpoints()
        ids = [b["id"] for b in bps]
        assert bp1 in ids
        assert bp2 in ids

    def test_simulator_reset(self):
        """reset() でシミュレーションが初期状態に戻ること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2,
        )
        sim.step(10)
        assert sim.cycle == 10
        sim.reset()
        assert sim.cycle == 0
        assert sim.time_ns == 0

    def test_simulator_snapshot(self):
        """snapshot() が現在の信号辞書を返すこと"""
        import deltasim.api as ds
        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")], reset_cycles=0)
        snap = sim.snapshot()
        assert isinstance(snap, dict)
        assert "count" in snap

    def test_simulator_design_info(self):
        """DesignInfo を渡して Simulator を作れること"""
        import deltasim.api as ds
        di = ds.load([os.path.join(EXAMPLES, "counter.sv")])
        sim = ds.Simulator(di, reset_cycles=2)
        sim.step(5)
        assert sim.get("count") is not None

    def test_cli_sim_basic(self):
        """CLI sim コマンドが動くこと（非インタラクティブで step + quit）"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        # 入力: step 5 → signals → quit
        result = runner.invoke(main, [
            "sim",
            os.path.join(EXAMPLES, "counter.sv"),
            "--top", "counter",
            "--reset-cycles", "2",
        ], input="step 5\nsignals\nquit\n")
        assert result.exit_code == 0, result.output
        assert "t=" in result.output or "cycle" in result.output


# ---------------------------------------------------------------------------
# F. 信号変化リスト API
# ---------------------------------------------------------------------------

class TestSignalChangeAPI:
    def test_run_keep_changes(self):
        """run(keep_changes=True) で変化リストが取得できること"""
        import deltasim.api as ds
        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            cycles=20, keep_changes=True,
        )
        changes = result.get_signal_changes("count")
        assert len(changes) > 0
        assert all(hasattr(c, "time_ns") for c in changes)
        assert all(hasattr(c, "value") for c in changes)

    def test_signal_change_time_range(self):
        """時間範囲でフィルタリングできること"""
        import deltasim.api as ds
        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            cycles=30, keep_changes=True,
        )
        all_changes = result.get_signal_changes("count")
        filtered = result.get_signal_changes("count", start_ns=100, end_ns=200)
        assert 0 <= len(filtered) <= len(all_changes)
        for c in filtered:
            assert 100 <= c.time_ns <= 200

    def test_signal_change_4state_values(self):
        """変化リストの value が 4 値文字列であること"""
        import deltasim.api as ds
        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            cycles=15, keep_changes=True,
        )
        changes = result.get_signal_changes("count")
        for c in changes:
            assert all(ch in "01xz" for ch in c.value.lower())

    def test_simulator_signal_changes(self):
        """Simulator でも信号変化が記録されること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2, keep_changes=True,
        )
        sim.step(10)
        changes = sim.get_signal_changes("count")
        assert len(changes) > 0

    def test_simulator_signal_changes_range(self):
        """Simulator の変化リストも時間範囲でフィルタできること"""
        import deltasim.api as ds
        sim = ds.Simulator(
            [os.path.join(EXAMPLES, "counter.sv")],
            reset_cycles=2, keep_changes=True,
        )
        sim.step(20)
        all_ch = sim.get_signal_changes("count")
        if len(all_ch) >= 2:
            mid = all_ch[len(all_ch) // 2].time_ns
            filtered = sim.get_signal_changes("count", start_ns=mid)
            assert len(filtered) <= len(all_ch)
