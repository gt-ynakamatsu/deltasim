"""tests/test_spec14.py — §14 確定事項の公開 API 経由テスト

1. --case 全除外 → status="ERROR", code="E_NO_CASE", 非0終了
2. fnmatch 記法（*, ?, [...]）が使えること
3. cases[].message フィールド
"""

from __future__ import annotations
import json as _json
import os
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ===========================================================================
# §14.1: 全ケース除外時 = ERROR
# ===========================================================================

class TestNoMatchError:
    """§14.1: --case フィルタで全ケースが除外された場合 status=ERROR"""

    def test_all_excluded_status_error(self):
        """全除外で status=ERROR を返すこと"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["__no_such_case__"],
        )
        assert result.status == "ERROR", \
            f"全除外で ERROR を期待したが {result.status} が返った"

    def test_all_excluded_code_e_no_case(self):
        """errors に code='E_NO_CASE' が含まれること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["xyz"],
        )
        codes = [e.code for e in result.errors]
        assert "E_NO_CASE" in codes, f"E_NO_CASE がない: {codes}"

    def test_all_excluded_message_contains_pattern(self):
        """errors[].message に指定パターンが含まれること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["my_pattern"],
        )
        e_no_case = next(e for e in result.errors if e.code == "E_NO_CASE")
        assert "my_pattern" in e_no_case.message, \
            f"パターン 'my_pattern' が message に含まれない: {e_no_case.message!r}"

    def test_all_excluded_cases_empty(self):
        """cases=[] であること（ケースは結果に含まれない）"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["xyz"],
        )
        assert result.cases == [], f"cases が空でない: {result.cases}"

    def test_all_excluded_returns_test_result(self):
        """戻り値は常に TestResult（None にならない）"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["xyz"],
        )
        from deltasim.result import TestResult
        assert isinstance(result, TestResult)

    def test_all_excluded_json_valid(self, tmp_path):
        """全除外時も --json が有効な JSON を出力すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        json_path = str(tmp_path / "result.json")
        runner = CliRunner()
        cli_result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--case", "xyz",
            "--json", json_path,
        ])
        # §14.1: 非 0 終了
        assert cli_result.exit_code != 0
        # JSON は正しく出力されていること
        assert Path(json_path).exists(), "JSON ファイルが生成されていない"
        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        assert any(e["code"] == "E_NO_CASE" for e in d["errors"])

    def test_all_excluded_cli_exit_nonzero(self):
        """CLI が非 0 で終了すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--case", "xyz",
        ])
        assert result.exit_code != 0, "全除外時は非 0 を期待"

    def test_partial_match_runs(self):
        """一部のケースが一致すれば ERROR にならないこと"""
        import deltasim.api as ds, yaml, tempfile
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [
                {"name": "run_me", "expected": [{"at": 5, "check": {"count": 3}}]},
                {"name": "skip_me", "expected": []},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            import yaml as _yaml
            _yaml.dump(spec, f, allow_unicode=True)
            path = f.name
        try:
            result = ds.test(
                [os.path.join(EXAMPLES, "counter.sv")],
                path, filter_cases=["run_me"]
            )
            assert result.status == "PASS", \
                f"1件一致で PASS を期待したが {result.status}"
            assert len(result.cases) == 1
        finally:
            os.unlink(path)


# ===========================================================================
# §14.2: fnmatch 記法（*, ?, [...]）
# ===========================================================================

class TestFnmatchSyntax:
    """§14.2: * だけでなく ? と [...] も使えること"""

    def _multi_case_spec(self, tmp_path):
        import yaml as _yaml
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [
                {"name": "test_a1", "expected": []},
                {"name": "test_b2", "expected": []},
                {"name": "other_c", "expected": []},
            ],
        }
        p = str(tmp_path / "fnmatch.yaml")
        Path(p).write_text(_yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        return p

    def test_star_wildcard(self, tmp_path):
        """* ワイルドカードで複数ケースを選択できること"""
        import deltasim.api as ds
        path = self._multi_case_spec(tmp_path)
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], path,
                         filter_cases=["test_*"])
        assert len(result.cases) == 2
        names = {cr.name for cr in result.cases}
        assert names == {"test_a1", "test_b2"}

    def test_question_mark_wildcard(self, tmp_path):
        """? ワイルドカードで1文字にマッチすること"""
        import deltasim.api as ds
        path = self._multi_case_spec(tmp_path)
        # test_?1 → test_a1 のみ
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], path,
                         filter_cases=["test_?1"])
        assert len(result.cases) == 1
        assert result.cases[0].name == "test_a1"

    def test_bracket_wildcard(self, tmp_path):
        """[...] ワイルドカードで文字クラスにマッチすること"""
        import deltasim.api as ds
        path = self._multi_case_spec(tmp_path)
        # test_[ab]? → test_a1, test_b2
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], path,
                         filter_cases=["test_[ab]?"])
        assert len(result.cases) == 2

    def test_star_all(self, tmp_path):
        """* だけで全ケースを選択できること"""
        import deltasim.api as ds
        path = self._multi_case_spec(tmp_path)
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], path,
                         filter_cases=["*"])
        assert len(result.cases) == 3

    def test_all_excluded_question_mark(self, tmp_path):
        """? で全除外の場合も E_NO_CASE になること"""
        import deltasim.api as ds
        path = self._multi_case_spec(tmp_path)
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], path,
                         filter_cases=["no_?atch"])
        assert result.status == "ERROR"
        assert any(e.code == "E_NO_CASE" for e in result.errors)


# ===========================================================================
# §14.3: cases[].message フィールド
# ===========================================================================

class TestCaseResultMessage:
    """§14.3: cases[].message フィールドの検証"""

    def test_skip_message_skip_reason(self, tmp_path):
        """SKIP ケース: message = skip_reason"""
        import yaml as _yaml, deltasim.api as ds
        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10},
            "cases": [{"name": "s", "skip": True, "skip_reason": "CI対象外"}],
        }
        p = str(tmp_path / "s.yaml")
        Path(p).write_text(_yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], p)
        cr = result.cases[0]
        assert cr.status == "SKIP"
        assert cr.message == "CI対象外", f"message={cr.message!r}"

    def test_skip_message_default_skipped(self, tmp_path):
        """SKIP ケース: skip_reason なし → message = 'skipped'"""
        import yaml as _yaml, deltasim.api as ds
        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10},
            "cases": [{"name": "s", "skip": True}],
        }
        p = str(tmp_path / "s2.yaml")
        Path(p).write_text(_yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], p)
        cr = result.cases[0]
        assert cr.status == "SKIP"
        assert cr.message == "skipped", f"message={cr.message!r}"

    def test_cancel_message(self):
        """キャンセル時に未実行のケース: message = 'cancelled'"""
        import deltasim.api as ds
        from deltasim.result import CancelToken
        token = CancelToken()
        events = []

        def _on(ev):
            if ev.get("type") == "progress" and ev.get("phase") == "sim":
                token.cancel()
            events.append(ev)

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=_on,
            cancel=token,
        )
        # キャンセル時にケースが SKIP になっていれば message = "cancelled"
        skip_cases = [cr for cr in result.cases if cr.status == "SKIP"]
        if skip_cases:
            assert skip_cases[0].message == "cancelled", \
                f"cancel時 message={skip_cases[0].message!r}"

    def test_pass_message_empty(self, tmp_path):
        """PASS ケース: message = '' (§14 未定義 → 空文字列)"""
        import yaml as _yaml, deltasim.api as ds
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [{"name": "p", "expected": [{"at": 3, "check": {"count": 1}}]}],
        }
        p = str(tmp_path / "p.yaml")
        Path(p).write_text(_yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], p)
        cr = result.cases[0]
        assert cr.status == "PASS"
        assert cr.message == "", \
            f"PASS の message は '' のはず（§14 未定義）: {cr.message!r}"

    def test_fail_message_empty(self, tmp_path):
        """FAIL ケース: message = '' (§14 未定義 → 空文字列)"""
        import yaml as _yaml, deltasim.api as ds
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 12, "seed": 0},
            "cases": [{"name": "f", "expected": [{"at": 3, "check": {"count": 999}}]}],
        }
        p = str(tmp_path / "f.yaml")
        Path(p).write_text(_yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], p)
        cr = result.cases[0]
        assert cr.status == "FAIL"
        assert cr.message == "", \
            f"FAIL の message は '' のはず（§14 未定義）: {cr.message!r}"

    def test_message_in_json(self, tmp_path):
        """cases[].message が JSON に含まれること"""
        import yaml as _yaml, deltasim.api as ds
        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10},
            "cases": [{"name": "s", "skip": True, "skip_reason": "test"}],
        }
        p = str(tmp_path / "m.yaml")
        Path(p).write_text(_yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        result = ds.test([os.path.join(EXAMPLES, "counter.sv")], p)
        d = result.to_dict()
        assert "cases" in d
        cr_d = d["cases"][0]
        assert "message" in cr_d, f"cases[0] に message がない: {list(cr_d.keys())}"
        assert cr_d["message"] == "test"

    def test_case_done_event_has_message(self):
        """case_done イベントに message が含まれること (GUI §2.4)"""
        import deltasim.api as ds, yaml as _yaml, tempfile

        spec = {
            "version": 1, "top": "counter",
            "sim": {"cycles": 10},
            "cases": [{"name": "s", "skip": True, "skip_reason": "event_test"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml",
                                         delete=False, encoding="utf-8") as f:
            _yaml.dump(spec, f, allow_unicode=True)
            path = f.name
        try:
            events = []
            ds.test([os.path.join(EXAMPLES, "counter.sv")], path,
                    on_progress=events.append)
            cd_events = [e for e in events if e.get("type") == "case_done"]
            assert cd_events, "case_done イベントが発生しなかった"
            cd = cd_events[0]
            assert "message" in cd, f"case_done に message がない: {list(cd.keys())}"
            assert cd["message"] == "event_test"
        finally:
            os.unlink(path)
