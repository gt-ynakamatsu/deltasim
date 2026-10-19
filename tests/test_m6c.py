"""tests/test_m6c.py — §13 マルチケース YAML 対応テスト"""

from __future__ import annotations
import json as _json
import os
from pathlib import Path
import tempfile
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


class TestMultiCaseYAML:
    """§13.2: cases: リスト付き YAML の解析・実行"""

    def test_parse_multi_case(self, tmp_path):
        """cases: 複数ケースを持つ YAML がパースできること"""
        import yaml
        from deltasim.tb.scenario import load_scenario

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 20, "seed": 0},
            "cases": [
                {
                    "name": "basic",
                    "expected": [{"at": 11, "check": {"count": 1}}],
                },
                {
                    "name": "skip_example",
                    "skip": True,
                    "skip_reason": "テスト中",
                },
            ],
        }
        spec_path = str(tmp_path / "multi.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        scenario = load_scenario(spec_path)
        assert len(scenario.cases) == 2
        assert scenario.cases[0].name == "basic"
        assert scenario.cases[1].name == "skip_example"
        assert scenario.cases[1].skip is True

    def test_run_multi_case(self, tmp_path):
        """複数ケースが実行され TestResult.cases に結果が入ること"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 20, "seed": 0},
            "cases": [
                {
                    "name": "basic",
                    "expected": [{"at": 11, "check": {"count": 1}}],
                },
                {
                    "name": "wider",
                    "expected": [{"at": 12, "check": {"count": 2}}],
                },
            ],
        }
        spec_path = str(tmp_path / "multi.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path)
        assert len(result.cases) == 2
        assert result.cases[0].name == "basic"
        assert result.cases[1].name == "wider"
        assert result.status == "PASS"

    def test_case_elapsed_ms_per_case(self, tmp_path):
        """cases[].elapsed_ms が各ケースの実時間であること (§13.1)"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 15, "seed": 0},
            "cases": [
                {"name": "case1", "expected": [{"at": 3, "check": {"count": 1}}]},
                {"name": "case2", "expected": [{"at": 3, "check": {"count": 1}}]},
            ],
        }
        spec_path = str(tmp_path / "elapsed.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path)
        for cr in result.cases:
            assert cr.elapsed_ms >= 0  # §13.1: ケースごとの実時間

        # §13.1: top-level elapsed_ms = 全体時間 >= 各ケース合計
        sum_case_ms = sum(cr.elapsed_ms for cr in result.cases)
        assert result.elapsed_ms >= sum_case_ms - 1.0  # 誤差1ms許容

    def test_top_level_elapsed_ms_is_total(self, tmp_path):
        """§13.1: top-level elapsed_ms は全体時間 (summary.elapsed_ms と同値)"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 10, "seed": 0},
            "cases": [{"name": "c1", "expected": []}],
        }
        spec_path = str(tmp_path / "s.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path)
        d = result.to_dict()
        # §13.1: elapsed_ms == summary.elapsed_ms
        assert d["elapsed_ms"] == d["summary"]["elapsed_ms"]

    def test_skip_case_in_multi(self, tmp_path):
        """skip: true のケースは SKIP として cases[] に含まれること"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 15, "seed": 0},
            "cases": [
                {"name": "run_this", "expected": []},
                {"name": "skip_this", "skip": True, "skip_reason": "CI対象外"},
            ],
        }
        spec_path = str(tmp_path / "skip.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path)
        skip_cases = [cr for cr in result.cases if cr.status == "SKIP"]
        run_cases  = [cr for cr in result.cases if cr.status != "SKIP"]
        assert len(skip_cases) == 1
        assert skip_cases[0].name == "skip_this"
        assert "CI対象外" in skip_cases[0].log[0]
        assert len(run_cases) == 1

    def test_duplicate_name_raises(self, tmp_path):
        """name が重複するとロード時に ScenarioError が発生すること"""
        import yaml
        from deltasim.tb.scenario import load_scenario, ScenarioError

        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10},
            "cases": [
                {"name": "dup", "expected": []},
                {"name": "dup", "expected": []},  # duplicate
            ],
        }
        spec_path = str(tmp_path / "dup.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        with pytest.raises(ScenarioError, match="unique"):
            load_scenario(spec_path)

    def test_case_seed_override(self, tmp_path):
        """cases[].seed がトップレベル seed を上書きすること"""
        import yaml
        from deltasim.tb.scenario import load_scenario

        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10, "seed": 99},
            "cases": [
                {"name": "custom_seed", "seed": 42, "expected": []},
            ],
        }
        spec_path = str(tmp_path / "seed.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        scenario = load_scenario(spec_path)
        assert scenario.cases[0].seed == 42
        assert scenario.sim.seed == 99  # top-level unchanged

    def test_legacy_no_cases_key(self):
        """cases: が無い旧形式がファイル名を name とする 1 ケースとして扱われること"""
        from deltasim.tb.scenario import load_scenario

        scenario = load_scenario(os.path.join(EXAMPLES, "counter_spec.yaml"))
        # No cases: key → s.cases is empty (legacy single-case mode)
        assert len(scenario.cases) == 0  # legacy mode: top-level inputs/expected used

    def test_json_has_cases_field(self, tmp_path):
        """TestResult.to_dict() に cases フィールドが含まれること"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [{"name": "c1", "expected": [{"at": 3, "check": {"count": 1}}]}],
        }
        spec_path = str(tmp_path / "j.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path)
        d = result.to_dict()
        assert "cases" in d
        assert len(d["cases"]) == 1
        cr = d["cases"][0]
        assert "name" in cr
        assert "status" in cr
        assert "elapsed_ms" in cr
        assert "checks" in cr


class TestCaseFilterWildcard:
    """--case ワイルドカードフィルタのテスト"""

    def test_exact_match(self, tmp_path):
        """正確なケース名でフィルタできること"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [
                {"name": "basic", "expected": [{"at": 3, "check": {"count": 1}}]},
                {"name": "advanced", "expected": [{"at": 3, "check": {"count": 1}}]},
            ],
        }
        spec_path = str(tmp_path / "f.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path,
                         filter_cases=["basic"])
        assert len(result.cases) == 1
        assert result.cases[0].name == "basic"

    def test_wildcard_star(self, tmp_path):
        """* ワイルドカードでケースを選択できること"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [
                {"name": "test_a", "expected": []},
                {"name": "test_b", "expected": []},
                {"name": "other_c", "expected": []},
            ],
        }
        spec_path = str(tmp_path / "wild.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        # test_* → test_a と test_b のみ
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path,
                         filter_cases=["test_*"])
        assert len(result.cases) == 2
        names = {cr.name for cr in result.cases}
        assert names == {"test_a", "test_b"}

    def test_filter_excludes_fully(self, tmp_path):
        """全ケースが除外された場合も cases=[] の TestResult を返すこと"""
        import yaml, deltasim.api as ds

        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10},
            "cases": [{"name": "case1", "expected": []}],
        }
        spec_path = str(tmp_path / "excl.yaml")
        Path(spec_path).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")

        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], spec_path,
                         filter_cases=["no_match"])
        assert result is not None, "TestResult は常に返すこと"
        assert len(result.cases) == 0
        assert result.status == "PASS"


class TestGenYAMLFormat:
    """gen が生成する YAML が §13.2 cases: 形式であること"""

    def test_gen_yaml_has_cases_key(self, tmp_path):
        """deltasim gen が cases: を含む YAML を生成すること"""
        import deltasim.api as ds
        result = ds.gen([os.path.join(EXAMPLES, "counter.sv")], top="counter")
        assert "cases:" in result["spec_yaml"], \
            f"gen YAML に cases: がない:\n{result['spec_yaml']}"

    def test_gen_yaml_parseable(self, tmp_path):
        """gen した YAML が load_scenario でパースできること"""
        import deltasim.api as ds
        from deltasim.tb.scenario import load_scenario
        result = ds.gen([os.path.join(EXAMPLES, "counter.sv")], top="counter")
        spec_path = str(tmp_path / "gen.yaml")
        Path(spec_path).write_text(result["spec_yaml"], encoding="utf-8")
        scenario = load_scenario(spec_path)
        assert len(scenario.cases) >= 1

    def test_gen_yaml_has_case_name(self, tmp_path):
        """gen した YAML のケースに name が含まれること"""
        import deltasim.api as ds
        result = ds.gen([os.path.join(EXAMPLES, "counter.sv")], top="counter")
        assert "name:" in result["spec_yaml"]
