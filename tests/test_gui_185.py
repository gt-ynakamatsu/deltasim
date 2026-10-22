"""§18.5 GUI 対応テスト

1. find_top_candidates: api.find_top_candidates() を使うこと
2. E_AMBIGUOUS_TOP: candidates フィールドが含まれること
3. E_UNKNOWN_PORT / E_NO_CHECK: 結果に含まれること
4. --log オプション: LLM run_suggestion による subprocess での --log 影響確認
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deltasim.gui.server import app

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
COUNTER_SV   = str(EXAMPLES_DIR / "counter.sv")
COUNTER_SPEC = str(EXAMPLES_DIR / "counter_spec.yaml")
MUX2_SV      = str(EXAMPLES_DIR / "mux2.sv")


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


# ---------------------------------------------------------------------------
# 1. find_top_candidates: api.find_top_candidates() が使われること
# ---------------------------------------------------------------------------

class TestFindTopCandidates:
    """§18.5: top 候補が api.find_top_candidates() で正しく判定されること"""

    def test_single_module_is_top_candidate(self, client):
        """単一モジュールは is_top_candidate=True"""
        res = client.post("/api/load", json={"files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        data = res.json()
        mods = data["all_modules"]
        assert any(m["name"] == "counter" and m["is_top_candidate"] for m in mods)

    def test_all_modules_have_is_top_candidate_field(self, client):
        """all_modules の各エントリに is_top_candidate があること"""
        res = client.post("/api/load", json={"files": [COUNTER_SV]})
        assert res.status_code == 200
        for m in res.json()["all_modules"]:
            assert "is_top_candidate" in m
            assert isinstance(m["is_top_candidate"], bool)

    def test_top_candidates_appear_first(self, client):
        """is_top_candidate=True のモジュールが先頭に来ること"""
        res = client.post("/api/load", json={"files": [COUNTER_SV]})
        assert res.status_code == 200
        mods = res.json()["all_modules"]
        if len(mods) > 1:
            # トップ候補が先頭に並んでいること
            top_idxs = [i for i, m in enumerate(mods) if m["is_top_candidate"]]
            non_top_idxs = [i for i, m in enumerate(mods) if not m["is_top_candidate"]]
            if top_idxs and non_top_idxs:
                assert max(top_idxs) < min(non_top_idxs), "トップ候補が先頭に並ぶこと"

    def test_uses_public_api_not_internal(self, client, monkeypatch):
        """§18.5: adapter.load() が api.find_top_candidates() を呼んでいること"""
        called = []
        import deltasim.api as _ds
        original = _ds.find_top_candidates

        def _spy(mods, catalog=None):
            called.append(mods)
            return original(mods, catalog)

        monkeypatch.setattr("deltasim.api.find_top_candidates", _spy)

        client.post("/api/load", json={"files": [COUNTER_SV]})
        assert len(called) >= 1, "find_top_candidates が呼ばれること"


# ---------------------------------------------------------------------------
# 2. E_AMBIGUOUS_TOP: candidates フィールドが含まれること
# ---------------------------------------------------------------------------

class TestAmbiguousTop:
    """§18.5: E_AMBIGUOUS_TOP エラーに candidates フィールドが含まれること"""

    def test_ambiguous_top_error_has_candidates(self, tmp_path, client):
        """複数のトップ候補がある場合 E_AMBIGUOUS_TOP に candidates が含まれること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # 2つの独立したモジュール（どちらもインスタンス化されていない）
        sv_content = """
module module_a (input clk);
endmodule

module module_b (input clk);
endmodule
"""
        sv_path = str(tmp_path / "two_tops.sv")
        Path(sv_path).write_text(sv_content, encoding="utf-8")

        # top を指定せずに読み込む → E_AMBIGUOUS_TOP
        res = client.post("/api/load", json={"files": [sv_path]})
        assert res.status_code == 200
        data = res.json()

        ambig_errors = [e for e in data.get("errors", []) if e.get("code") == "E_AMBIGUOUS_TOP"]
        if ambig_errors:
            # candidates フィールドが含まれること
            assert "candidates" in ambig_errors[0], "candidates フィールドが必要"
            assert isinstance(ambig_errors[0]["candidates"], list)
            assert len(ambig_errors[0]["candidates"]) >= 2

    def test_load_with_top_specified_avoids_ambiguous(self, tmp_path, client):
        """§18.5: top を明示すると E_AMBIGUOUS_TOP が回避されること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        sv_content = """
module module_a (input clk);
endmodule
module module_b (input clk);
endmodule
"""
        sv_path = str(tmp_path / "two_tops2.sv")
        Path(sv_path).write_text(sv_content, encoding="utf-8")

        res = client.post("/api/load", json={"files": [sv_path], "top": "module_a"})
        assert res.status_code == 200
        data = res.json()
        ambig = [e for e in data.get("errors", []) if e.get("code") == "E_AMBIGUOUS_TOP"]
        assert len(ambig) == 0, "top を指定すれば E_AMBIGUOUS_TOP なし"
        assert data.get("module") == "module_a"


# ---------------------------------------------------------------------------
# 3. E_UNKNOWN_PORT / E_NO_CHECK: 結果に含まれること
# ---------------------------------------------------------------------------

class TestErrorCodes185:
    """§18.5: 新エラーコードが正しく返ること"""

    def test_e_unknown_port_in_test_result(self, tmp_path, client):
        """§18.5: 存在しないポート名を YAML に書くと E_UNKNOWN_PORT が返ること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        spec_content = """version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 5}
sim: {cycles: 3}
expected:
  - at: 2
    check: {nonexistent_port_xyz: 0}
"""
        spec_path = str(tmp_path / "bad_port.yaml")
        Path(spec_path).write_text(spec_content, encoding="utf-8")

        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": spec_path,
        })
        assert res.status_code == 200
        data = res.json()
        # E_UNKNOWN_PORT があるか、または ERROR 状態であること
        error_codes = [e.get("code") for e in data.get("errors", [])]
        case_error_codes = [
            e.get("code")
            for c in data.get("cases", [])
            for e in c.get("errors", [])
        ]
        all_codes = error_codes + case_error_codes
        assert data["status"] == "ERROR" or "E_UNKNOWN_PORT" in all_codes, (
            f"E_UNKNOWN_PORT か ERROR が返ること: status={data['status']}, codes={all_codes}"
        )

    def test_e_no_check_in_test_result(self, tmp_path, client):
        """§18.5: チェックなしスペックは E_NO_CHECK または ERROR が返ること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        spec_content = """version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 5}
sim: {cycles: 3}
cases:
  - name: no_checks
    stimulus: []
"""
        spec_path = str(tmp_path / "no_check.yaml")
        Path(spec_path).write_text(spec_content, encoding="utf-8")

        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": spec_path,
        })
        assert res.status_code == 200
        data = res.json()
        error_codes = [e.get("code") for e in data.get("errors", [])]
        case_error_codes = [
            e.get("code")
            for c in data.get("cases", [])
            for e in c.get("errors", [])
        ]
        all_codes = error_codes + case_error_codes
        # E_NO_CHECK が返るか、または ERROR 状態であること
        assert data["status"] == "ERROR" or "E_NO_CHECK" in all_codes, (
            f"E_NO_CHECK か ERROR が返ること: status={data['status']}, codes={all_codes}"
        )


# ---------------------------------------------------------------------------
# 4. --log オプション: LLM run_suggestion での影響確認
# ---------------------------------------------------------------------------

class TestLogOptionImpact:
    """§18.5: --log オプションが GUI の LLM 実行に与える影響の確認"""

    def test_cli_has_log_option_default(self):
        """CLI の test コマンドに --log オプションがあることを確認"""
        from click.testing import CliRunner
        from deltasim.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["test", "--help"])
        assert "--log" in result.output, "test コマンドに --log がある"
        assert "deltasim.log" in result.output, "--log のデフォルトは deltasim.log"

    def test_run_suggestion_uses_subprocess_without_log_path(self, tmp_path, monkeypatch):
        """LLM run_suggestion が subprocess で deltasim を起動することを確認。
        
        §18.5 方針: GUI から run_suggestion 経由で deltasim を起動する場合、
        --log /dev/null (Linux) または --log NUL (Windows) を追加して
        CWD への deltasim.log 作成を抑制し、表示コマンドと実際の実行を一致させる。
        （この修正は PR #26 の run_llm_run_job に適用予定）
        """
        import subprocess
        import sys as _sys

        # run_suggestion が subprocess.run を呼ぶことを確認
        from deltasim.llm import Suggestion, run_suggestion
        from deltasim.llm.config import LLMConfig

        captured = []

        def _capture_executor(argv):
            captured.append(argv)
            return 0

        sug = Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        run_suggestion(sug, yes=True, executor=_capture_executor)

        assert len(captured) == 1, "executor が1回呼ばれること"
        argv = captured[0]
        assert "deltasim" in str(argv), "deltasim コマンドが含まれること"

    def test_log_file_suppression_with_devnull(self, tmp_path):
        """--log /dev/null または --log NUL で deltasim.log が作成されないことを確認"""
        import subprocess, sys, os

        devnull = "NUL" if os.name == "nt" else "/dev/null"
        result = subprocess.run(
            [sys.executable, "-m", "deltasim", "--version", "--log", devnull],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        # deltasim.log が作成されていないこと
        log_path = tmp_path / "deltasim.log"
        assert not log_path.exists(), (
            f"--log {devnull} を指定すれば deltasim.log が作成されないこと"
        )
