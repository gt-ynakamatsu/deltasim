"""tests/test_spec15_13.py — §15.13 W_UNKNOWN_KEY 全階層 + warnings 常時出力テスト

§15.13 確定事項:
1. W_UNKNOWN_KEY: YAML のどの階層の未知キーにも警告（パス付き）
   message 形式: "{cases[i].clock.key}: 不明なキーは無視されます"
   トップレベルは "{KEY}: 不明なキーは無視されます"
2. warnings: 空でも常に [] として出力
"""

from __future__ import annotations
import os, tempfile, yaml
from pathlib import Path
import pytest
import deltasim.api as ds

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# §15.13-1: W_UNKNOWN_KEY — 全階層でパス付き警告
# ---------------------------------------------------------------------------

class TestUnknownKeyAllLayers:
    """各階層で W_UNKNOWN_KEY が正しいパスで出ること"""

    def _run(self, spec_dict, sv=None):
        """指定 spec で test() を実行し TestResult を返す。sv=None は counter.sv を使用。"""
        sv = sv or os.path.join(EXAMPLES, "counter.sv")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            yaml.dump(spec_dict, f, allow_unicode=True)
            p = f.name
        try:
            return ds.test([sv], spec=p), p
        finally:
            os.unlink(p)

    def _base_spec(self):
        return {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "basic", "expected": []}],
        }

    def _all_warnings(self, result):
        ws = list(result.warnings)
        for cr in result.cases:
            ws.extend(cr.warnings)
        return ws

    def test_top_level_unknown_key(self):
        """トップレベルの未知キーは '{KEY}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["BOGUS_KEY"] = 99
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        # §15.13: トップレベルはパスなし → キー名がそのまま先頭
        assert any(m.startswith("BOGUS_KEY:") for m in msgs), \
            f"expected 'BOGUS_KEY: ...' but got: {msgs}"

    def test_clock_unknown_key(self):
        """clock 配下の未知キーは 'clock.{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["clock"]["bogus_clock"] = "x"
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("clock.bogus_clock" in m for m in msgs), f"msgs={msgs}"

    def test_reset_unknown_key(self):
        """reset 配下の未知キーは 'reset.{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["reset"]["bogus_reset"] = True
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("reset.bogus_reset" in m for m in msgs), f"msgs={msgs}"

    def test_sim_unknown_key(self):
        """sim 配下の未知キーは 'sim.{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["sim"]["bogus_sim"] = 0
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("sim.bogus_sim" in m for m in msgs), f"msgs={msgs}"

    def test_cases_item_unknown_key(self):
        """cases[0] 直下の未知キーは 'cases[0].{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["cases"][0]["skpi"] = True  # よくある typo: skip → skpi
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        # §15.13 仕様どおり "cases[0].skpi" がメッセージに含まれること
        assert any("cases[0].skpi" in m for m in msgs), \
            f"expected 'cases[0].skpi' in message but got: {msgs}"

    def test_cases_item_clock_unknown_key(self):
        """cases[0].clock 配下の未知キーは 'cases[0].clock.{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["cases"][0]["clock"] = {"signal": "clk", "period_ns": 10, "jitter": 1}
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[0].clock.jitter" in m for m in msgs), \
            f"expected 'cases[0].clock.jitter' in message but got: {msgs}"

    def test_cases_item_reset_unknown_key(self):
        """cases[0].reset 配下の未知キーは 'cases[0].reset.{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["cases"][0]["reset"] = {"signal": "rst_n", "active_low": True,
                                      "duration_cycles": 2, "bogus": 0}
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[0].reset.bogus" in m for m in msgs), f"msgs={msgs}"

    def test_stimulus_item_unknown_key(self):
        """cases[0].stimulus[0] の未知キーは 'cases[0].stimulus[0].{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["cases"][0]["stimulus"] = [{"at": 3, "set": {"rst_n": 1}, "alien": 99}]
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[0].stimulus[0].alien" in m for m in msgs), f"msgs={msgs}"

    def test_expect_item_unknown_key(self):
        """cases[0].expected[0] の未知キーは 'cases[0].expected[0].{key}: ...' 形式で出ること"""
        spec = self._base_spec()
        spec["cases"][0]["expected"] = [{"at": 3, "check": {"count": 0}, "weird": 1}]
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[0].expected[0].weird" in m for m in msgs), f"msgs={msgs}"

    def test_stimulus_alias_inputs_uses_key_name(self):
        """YAML に 'inputs' と書いたときは 'cases[0].inputs[i].{key}' で報告されること"""
        spec = self._base_spec()
        spec["cases"][0]["inputs"] = [{"at": 3, "set": {"rst_n": 1}, "typo_key": 1}]
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[0].inputs[0].typo_key" in m for m in msgs), f"msgs={msgs}"

    def test_stimulus_item_offset_typo(self):
        """'ofset_ns'（typo）は 'cases[0].stimulus[2].ofset_ns: ...' で報告されること（§15.13 仕様例）"""
        spec = self._base_spec()
        spec["cases"][0]["stimulus"] = [
            {"at": 1, "set": {"rst_n": 1}},
            {"at": 2, "set": {"rst_n": 1}},
            {"at": 3, "set": {"rst_n": 1}, "ofset_ns": 1},  # typo
        ]
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[0].stimulus[2].ofset_ns" in m for m in msgs), \
            f"expected 'cases[0].stimulus[2].ofset_ns' in message but got: {msgs}"

    def test_cases_second_item_path_index(self):
        """cases[1] の未知キーにインデックス 1 が出ること"""
        spec = self._base_spec()
        spec["cases"].append({"name": "second", "expected": [], "junk": 42})
        result, _ = self._run(spec)
        msgs = [w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert any("cases[1].junk" in m for m in msgs), \
            f"expected 'cases[1].junk' in message but got: {msgs}"

    def test_no_warning_for_known_keys(self):
        """全て既知キーのスペックで警告が出ないこと"""
        spec = self._base_spec()
        result, _ = self._run(spec)
        unk = [w for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY"]
        assert unk == [], f"警告が出た: {[(w.code, w.message) for w in unk]}"

    def test_multiple_unknown_keys_all_reported(self):
        """複数の未知キーが全部報告されること"""
        spec = self._base_spec()
        spec["a_bad"] = 1
        spec["b_bad"] = 2
        result, _ = self._run(spec)
        msgs = " ".join(w.message for w in self._all_warnings(result) if w.code == "W_UNKNOWN_KEY")
        assert "a_bad" in msgs
        assert "b_bad" in msgs

    def test_legacy_yaml_format_no_warning(self):
        """旧形式 YAML（cases: なし）でも既知キーに警告が出ないこと"""
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 10, "seed": 0},
            "inputs": [],
            "expected": [{"at": 2, "check": {"count": 0}}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            yaml.dump(spec, f)
            p = f.name
        try:
            result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec=p)
        finally:
            os.unlink(p)
        all_w = list(result.warnings) + [w for cr in result.cases for w in cr.warnings]
        unk = [w for w in all_w if w.code == "W_UNKNOWN_KEY"]
        assert unk == []

    def test_top_level_stimulus_alias_uses_key_name(self):
        """旧形式 YAML でトップレベルの stimulus のタイポは 'stimulus[i].{key}' で報告されること"""
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "stimulus": [{"at": 1, "set": {"rst_n": 1}, "bad_key": 0}],
            "expected": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            yaml.dump(spec, f)
            p = f.name
        try:
            result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec=p)
        finally:
            os.unlink(p)
        msgs = [w.message for w in result.warnings if w.code == "W_UNKNOWN_KEY"]
        # トップレベルに "stimulus" と書いたので "stimulus[0].bad_key" が出ること
        assert any("stimulus[0].bad_key" in m for m in msgs), f"msgs={msgs}"


# ---------------------------------------------------------------------------
# §15.13-2: warnings は常に [] として出力
# ---------------------------------------------------------------------------

class TestWarningsAlwaysPresent:
    """warnings キーは空でも常に出力されること"""

    def test_test_result_warnings_always_in_dict(self):
        """TestResult.to_dict() に warnings キーが常に含まれること"""
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert "warnings" in d, "TestResult.to_dict() に warnings がない"
        assert isinstance(d["warnings"], list)

    def test_test_result_warnings_empty_list(self):
        """警告なしのスペックで warnings: [] になること"""
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert d["warnings"] == [], f"expected [] but got {d['warnings']}"

    def test_case_result_warnings_always_in_dict(self):
        """cases[].to_dict() に warnings キーが常に含まれること"""
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        for cr_d in d.get("cases", []):
            assert "warnings" in cr_d, \
                f"cases[].to_dict() に warnings がない: {list(cr_d.keys())}"

    def test_warnings_in_json_output(self, tmp_path):
        """--json ファイルに warnings キーが含まれること"""
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
        import json as _json
        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "warnings" in d
        assert isinstance(d["warnings"], list)

    def test_warnings_empty_not_omitted(self):
        """空の warnings が省略されず [] として JSON に含まれること"""
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        import json as _json
        d = _json.loads(result.to_json())
        assert "warnings" in d
        assert d["warnings"] == []


# ---------------------------------------------------------------------------
# 既存 examples/ YAML で警告なしことを確認
# ---------------------------------------------------------------------------

class TestExistingYAMLsNoWarnings:
    """examples/ の既存 YAML で W_UNKNOWN_KEY が出ないこと"""

    @pytest.mark.parametrize("yaml_file,sv_files", [
        ("counter_spec.yaml", ["counter.sv"]),
        ("mux2_spec.yaml",    ["mux2.sv"]),
        ("shift_reg_spec.yaml", ["shift_reg.sv"]),
        ("fifo_spec.yaml",    ["fifo.sv"]),
        ("alu_spec.yaml",     ["adder.sv", "alu.sv"]),
        ("seq_detect_spec.yaml", ["seq_detect.sv"]),
        ("uart_tx_spec.yaml", ["uart_tx.sv"]),
        ("dff_base.yaml",     ["dff_sync.sv"]),
        ("dff_ex1.yaml",      ["dff_sync.sv"]),
        ("dff_ex2.yaml",      ["dff_sync.sv"]),
        ("reg_output_blocking_spec.yaml", ["reg_output_blocking.sv"]),
    ])
    def test_no_unknown_key_warning(self, yaml_file, sv_files):
        """既存 YAML スペックで W_UNKNOWN_KEY 警告が出ないこと"""
        from deltasim.tb.scenario import load_scenario
        spec_path = os.path.join(EXAMPLES, yaml_file)
        s = load_scenario(spec_path)
        unk = [w for w in s._warnings if w.code == "W_UNKNOWN_KEY"]
        assert unk == [], \
            f"{yaml_file}: 警告が出た: {[(w.code, w.message) for w in unk]}"

    def test_gen_output_no_warnings(self, tmp_path):
        """deltasim gen の出力 YAML で警告が出ないこと"""
        result = ds.gen(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter",
        )
        assert result["status"] == "PASS"
        spec_content = result["spec_yaml"]
        p = str(tmp_path / "gen_spec.yaml")
        Path(p).write_text(spec_content, encoding="utf-8")
        from deltasim.tb.scenario import load_scenario
        s = load_scenario(p)
        unk = [w for w in s._warnings if w.code == "W_UNKNOWN_KEY"]
        assert unk == [], \
            f"gen 出力で警告が出た: {[(w.code, w.message) for w in unk]}"
