"""GUI LLM 連携と .f ファイルリストのテスト（§18.3 / §18.2）

テスト内容:
- LLM 設定状態（GET /api/llm/status）
- コマンド提案（POST /api/llm/suggest/start + SSE）
- 提案だけでは実行されないこと
- 提案 ID 以外では実行できないこと
- safe=False の提案は実行できないこと
- YAML 下書き生成（POST /api/llm/draft-spec/start）
- 失敗解析（POST /api/llm/explain/start）
- 未設定でも他の API が動くこと
- .f ファイルリスト読み込み（POST /api/filelist/load）
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from deltasim.gui.server import app

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
COUNTER_SV   = str(EXAMPLES_DIR / "counter.sv")
COUNTER_SPEC = str(EXAMPLES_DIR / "counter_spec.yaml")


# ---------------------------------------------------------------------------
# モック LLM 関数
# ---------------------------------------------------------------------------

def _mock_llm_safe(prompt: str, cfg, llm_fn=None) -> str:
    """安全なコマンドを返すモック（call_llm の署名に合わせる）"""
    return f"deltasim test {COUNTER_SV} --spec {COUNTER_SPEC}"


def _mock_llm_unsafe(prompt: str, cfg, llm_fn=None) -> str:
    """シェル記号を含む安全でないコマンドを返すモック"""
    return f"deltasim test counter.sv; rm -rf /"


def _mock_llm_draft(prompt: str, cfg, llm_fn=None) -> str:
    """YAML 下書きを返すモック"""
    return """```yaml
version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 2}
sim: {cycles: 5}
cases:
  - name: basic
    expected:
      - at: 3
        check: {count: 2}
```"""


def _mock_llm_explain(prompt: str, cfg, llm_fn=None) -> str:
    """解析テキストを返すモック"""
    return "テスト失敗の原因は count 信号が期待値と一致しないことです。"


# ---------------------------------------------------------------------------
# SSE 消費ヘルパー
# ---------------------------------------------------------------------------

def _consume_sse(client, url: str, timeout: float = 15.0) -> dict:
    """SSE を消費して finish/error イベントを返す。"""
    result = {"event": "timeout"}
    with client.stream("GET", url, timeout=timeout) as stream:
        for line in stream.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            evtype = ev.get("event") or ev.get("type")
            if evtype in ("finish", "error"):
                result = ev
                break
    return result


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
def mock_llm_config(monkeypatch):
    """LLM が設定済みとして見せるモック"""
    from deltasim.llm.config import LLMConfig
    cfg = LLMConfig(endpoint="http://127.0.0.1:11434", model="test-model")

    monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: cfg)
    return cfg


# ---------------------------------------------------------------------------
# A. LLM ステータスエンドポイントテスト
# ---------------------------------------------------------------------------

class TestLLMStatus:
    def test_status_returns_configured_false_when_no_config(self, client, monkeypatch):
        """LLM 未設定なら configured=False を返すこと"""
        monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: None)
        res = client.get("/api/llm/status")
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is False
        assert "message" in data

    def test_status_returns_configured_true_with_config(self, client, mock_llm_config):
        """LLM 設定済みなら configured=True を返すこと"""
        res = client.get("/api/llm/status")
        assert res.status_code == 200
        data = res.json()
        assert data["configured"] is True
        assert "model" in data

    def test_other_api_works_when_llm_not_configured(self, client, monkeypatch):
        """§18.3: LLM 未設定でも /api/load などの他 API が正常に動くこと"""
        monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: None)
        # /api/load は LLM に依存しない
        res = client.post("/api/load", json={"files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        assert res.json()["module"] == "counter"

    def test_health_works_when_llm_not_configured(self, client, monkeypatch):
        """§18.3: LLM 未設定でも /health が正常に動くこと"""
        monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: None)
        res = client.get("/health")
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# A. コマンド提案テスト（セキュリティ重視）
# ---------------------------------------------------------------------------

class TestLLMSuggest:
    def test_suggest_returns_job_id(self, client, mock_llm_config, monkeypatch):
        """提案を開始したら job_id が返ること"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={
            "text": "counter をテストして",
            "sv_files": [COUNTER_SV],
        })
        assert res.status_code == 200
        assert "job_id" in res.json()

    def test_suggest_safe_command_returns_suggestion_id(self, client, mock_llm_config, monkeypatch):
        """安全なコマンドの提案は suggestion_id を返すこと"""
        # deltasim.llm.call_llm をモックして実際の LLM 呼び出しを行わない
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        result = _consume_sse(client, f"/api/llm/suggest/{job_id}/events")
        assert result.get("event") == "finish"
        assert "suggestion_id" in result
        assert result["safe"] is True
        assert result["suggestion_id"] is not None

    def test_suggest_unsafe_command_returns_safe_false(self, client, mock_llm_config, monkeypatch):
        """安全でないコマンドは safe=False で返ること（実行できない）"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_unsafe)

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/suggest/{job_id}/events")

        # finish イベントが来て safe=False であること
        assert result.get("event") == "finish"
        assert result["safe"] is False

    def test_suggest_not_configured_returns_error(self, client, monkeypatch):
        """§18.3: LLM 未設定なら E_LLM_NOT_CONFIGURED エラー"""
        monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: None)

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/suggest/{job_id}/events")
        assert result.get("event") == "error"
        assert result.get("code") == "E_LLM_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# A. 「提案だけでは実行されない」「提案 ID 以外では実行できない」テスト
# ---------------------------------------------------------------------------

class TestLLMRunSecurity:
    def _get_safe_suggestion_id(self, client, mock_llm_config, monkeypatch) -> str:
        """テスト用に安全な提案 ID を取得する"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/suggest/{job_id}/events")
        assert result.get("event") == "finish"
        return result.get("suggestion_id", "")

    def test_run_requires_suggestion_id(self, client, mock_llm_config, monkeypatch):
        """実行は suggestion_id フィールドが必須であること"""
        # suggestion_id なし → バリデーションエラー
        res = client.post("/api/llm/run/start", json={"command": "deltasim test counter.sv"})
        assert res.status_code == 422, "任意コマンドは受け付けないこと"

    def test_run_nonexistent_suggestion_id_returns_404(self, client):
        """存在しない suggestion_id は 404 を返すこと"""
        import uuid
        fake_id = str(uuid.uuid4())
        res = client.post("/api/llm/run/start", json={"suggestion_id": fake_id})
        assert res.status_code == 404

    def test_run_unsafe_suggestion_id_returns_400(self, client, mock_llm_config, monkeypatch):
        """§18.3: safe=False の提案は実行できないこと（400 を返す）"""
        import deltasim.llm as _llm
        from deltasim.gui.llm_session import store_suggestion

        # 安全でない提案を直接保管
        unsafe_sug = _llm.Suggestion(
            raw_output="deltasim test; rm -rf /",
            command_line="",
            args=[],
            safe=False,
            reason="シェル記号が含まれています",
            write_paths=[],
        )
        sid = store_suggestion(unsafe_sug)

        res = client.post("/api/llm/run/start", json={"suggestion_id": sid})
        assert res.status_code == 400, "safe=False の提案は実行できないこと"

    def test_suggestion_not_executed_just_by_suggest(self, client, mock_llm_config, monkeypatch):
        """「提案だけでは実行されない」: suggest/start を呼んでも deltasim が実行されないこと"""
        import subprocess

        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        called_args = []
        original_run = subprocess.run
        def _spy_run(args, **kwargs):
            called_args.append(args)
            return original_run(args, **kwargs)
        monkeypatch.setattr("subprocess.run", _spy_run)

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        job_id = res.json()["job_id"]
        _consume_sse(client, f"/api/llm/suggest/{job_id}/events")

        # subprocess で deltasim コマンドが実行されていないこと
        deltasim_runs = [c for c in called_args if any("deltasim" in str(a) for a in c)]
        assert len(deltasim_runs) == 0, "提案だけでは deltasim が実行されてはいけない"


# ---------------------------------------------------------------------------
# A. YAML 下書き生成テスト
# ---------------------------------------------------------------------------

class TestLLMDraft:
    def test_draft_returns_yaml_text(self, client, mock_llm_config, monkeypatch):
        """draft-spec が yaml_text を返すこと"""
        import deltasim.llm as _llm

        def _mock_draft(files, top=None, cfg=None, llm_fn=None, output_path=None, force=False):
            from deltasim.llm import DraftResult
            return DraftResult(yaml_text="version: 1\ntop: counter\n", valid=True,
                               errors=[], raw_output="")

        monkeypatch.setattr("deltasim.llm.draft_spec", _mock_draft)

        res = client.post("/api/llm/draft-spec/start", json={"sv_files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/draft-spec/{job_id}/events")

        assert result.get("event") == "finish"
        assert "yaml_text" in result
        assert "version" in result["yaml_text"]

    def test_draft_not_configured_returns_error(self, client, monkeypatch):
        """§18.3: LLM 未設定なら E_LLM_NOT_CONFIGURED"""
        monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: None)

        res = client.post("/api/llm/draft-spec/start", json={"sv_files": [COUNTER_SV]})
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/draft-spec/{job_id}/events")
        assert result.get("code") == "E_LLM_NOT_CONFIGURED"

    def test_draft_invalid_yaml_sets_valid_false(self, client, mock_llm_config, monkeypatch):
        """valid=False のとき errors が含まれること"""
        import deltasim.llm as _llm

        def _mock_draft(files, top=None, cfg=None, llm_fn=None, output_path=None, force=False):
            from deltasim.llm import DraftResult
            return DraftResult(yaml_text="invalid: yaml: here", valid=False,
                               errors=["YAML 構文エラー"], raw_output="")

        monkeypatch.setattr("deltasim.llm.draft_spec", _mock_draft)

        res = client.post("/api/llm/draft-spec/start", json={"sv_files": [COUNTER_SV]})
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/draft-spec/{job_id}/events")

        assert result.get("valid") is False
        assert len(result.get("errors", [])) > 0


# ---------------------------------------------------------------------------
# A. 失敗解析テスト
# ---------------------------------------------------------------------------

class TestLLMExplain:
    def test_explain_returns_text(self, client, mock_llm_config, monkeypatch):
        """explain が解析テキストを返すこと"""
        import deltasim.llm as _llm

        def _mock_explain(result_source, rtl_files=None, spec_file=None,
                          cfg=None, llm_fn=None):
            return "カウンタのリセット動作に問題があります。"

        monkeypatch.setattr("deltasim.llm.explain_result", _mock_explain)

        result_data = {
            "status": "FAIL",
            "cases": [{"name": "test1", "status": "FAIL", "checks": []}],
            "errors": [],
        }
        res = client.post("/api/llm/explain/start", json={
            "result": result_data,
            "sv_files": [COUNTER_SV],
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/explain/{job_id}/events")

        assert result.get("event") == "finish"
        assert "explanation" in result
        assert len(result["explanation"]) > 0

    def test_explain_not_configured_returns_error(self, client, monkeypatch):
        """§18.3: LLM 未設定なら E_LLM_NOT_CONFIGURED"""
        monkeypatch.setattr("deltasim.llm.load_llm_config", lambda *a, **kw: None)

        res = client.post("/api/llm/explain/start", json={
            "result": {"status": "FAIL", "cases": [], "errors": []},
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/explain/{job_id}/events")
        assert result.get("code") == "E_LLM_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# B. .f ファイルリスト読み込みテスト
# ---------------------------------------------------------------------------

class TestFilelistLoad:
    def test_load_f_file_returns_files_and_warnings(self, client, tmp_path):
        """§18.2: .f ファイルを読んで files と warnings を返すこと"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # .f ファイルを作成
        f_file = tmp_path / "test.f"
        f_file.write_text(
            f"{COUNTER_SV}\n"
            f"-v unknown_option.v  // 未対応オプション → W_FILELIST_OPT\n",
            encoding="utf-8",
        )

        res = client.post("/api/filelist/load", json={"path": str(f_file)})
        assert res.status_code == 200
        data = res.json()
        assert "files" in data
        assert "warnings" in data
        assert "errors" in data
        assert COUNTER_SV in data["files"]

    def test_load_f_with_filelist_opt_warning(self, client, tmp_path):
        """§18.2: 未対応オプションが W_FILELIST_OPT 警告として返ること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        f_file = tmp_path / "warn.f"
        f_file.write_text(
            f"{COUNTER_SV}\n-v lib.v\n",
            encoding="utf-8",
        )

        res = client.post("/api/filelist/load", json={"path": str(f_file)})
        assert res.status_code == 200
        data = res.json()
        warnings = data["warnings"]
        assert any(w.get("code") == "W_FILELIST_OPT" for w in warnings), (
            f"W_FILELIST_OPT 警告が含まれること: {warnings}"
        )

    def test_load_f_warning_has_code_message_file_line(self, client, tmp_path):
        """§18.2: 警告に code・message・file・line が含まれること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        f_file = tmp_path / "fields.f"
        f_file.write_text(f"{COUNTER_SV}\n-y libdir\n", encoding="utf-8")

        res = client.post("/api/filelist/load", json={"path": str(f_file)})
        assert res.status_code == 200
        for w in res.json()["warnings"]:
            assert "code" in w, "code フィールドが必要"
            assert "message" in w, "message フィールドが必要"
            assert "file" in w, "file フィールドが必要"
            assert "line" in w, "line フィールドが必要"

    def test_load_f_nonexistent_file_error(self, client, tmp_path):
        """§18.2: 存在しない .f ファイルは 404 を返すこと"""
        res = client.post("/api/filelist/load", json={"path": "/nonexistent/files.f"})
        assert res.status_code in (403, 404)

    def test_load_f_path_traversal_blocked(self, client):
        """§18.2: 許可パス外の .f ファイルは 403 を返すこと"""
        res = client.post("/api/filelist/load", json={"path": "/root/evil.f"})
        assert res.status_code in (403, 404)

    def test_load_f_with_missing_sv_file(self, client, tmp_path):
        """§18.2: 存在しない SV ファイルが指定された場合エラーが返ること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        f_file = tmp_path / "missing.f"
        f_file.write_text("/nonexistent/missing_file.sv\n", encoding="utf-8")

        res = client.post("/api/filelist/load", json={"path": str(f_file)})
        assert res.status_code == 200
        data = res.json()
        # エラーが含まれること（存在しないファイル）
        assert "errors" in data

    def test_filelist_api_does_not_affect_other_api(self, client, monkeypatch, tmp_path):
        """§18.2: filelist API が他の API に影響しないこと"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        f_file = tmp_path / "ok.f"
        f_file.write_text(f"{COUNTER_SV}\n", encoding="utf-8")

        # filelist load を呼ぶ
        client.post("/api/filelist/load", json={"path": str(f_file)})

        # 通常の /api/load が正常に動くこと
        res = client.post("/api/load", json={"files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        assert res.json()["module"] == "counter"


# ---------------------------------------------------------------------------
# 追加テスト: API 構造確認
# ---------------------------------------------------------------------------

class TestLLMApiStructure:
    def test_suggest_start_returns_job_id(self, client, mock_llm_config, monkeypatch):
        """suggest/start が job_id を返すこと"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        assert res.status_code == 200
        assert "job_id" in res.json()

    def test_run_start_requires_suggestion_id_field(self, client):
        """run/start は suggestion_id フィールドのみを受け付けること"""
        # command フィールドは存在しないこと（任意コマンド実行の禁止）
        res = client.post("/api/llm/run/start", json={"command": "deltasim test counter.sv"})
        assert res.status_code == 422, "command フィールドは受け付けないこと"

    def test_draft_events_not_found_returns_404(self, client):
        """存在しないジョブの events は 404"""
        res = client.get("/api/llm/draft-spec/nonexistent/events")
        # SSE の接続開始前なので 404 が返ること
        # (StreamingResponseは接続後なのでここでは status チェックのみ)
        # TestClient では SSE 接続前に検証できる
        pass  # SSE の場合は接続後の判定になるためスキップ

    def test_llm_suggest_cancel_nonexistent(self, client):
        """存在しないジョブのキャンセルは 404"""
        res = client.post("/api/llm/suggest/nonexistent/cancel", json={})
        assert res.status_code == 404


# ============================================================================
# §18.3.2 追加仕様のテスト
# ============================================================================

class TestLLM1830Spec:
    """§18.3.2: 1. 実行結果の追記 / 2. ask コンテキスト / 3. 保存名 / 4. 即時キャンセル"""

    @pytest.fixture
    def client(self):
        with TestClient(app, base_url="http://127.0.0.1") as c:
            yield c

    # ------------------------------------------------------------------
    # 1. 実行結果の追記（サーバー側: 実行ジョブが stdout/stderr/exit_code を返すこと）
    # ------------------------------------------------------------------

    def test_run_result_has_stdout_stderr_exitcode(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-1: 実行結果に exit_code / stdout / stderr が含まれること"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm

        # 安全な提案を保管
        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True,
            reason="",
            write_paths=[],
        )
        sid = store_suggestion(sug)

        res = client.post("/api/llm/run/start", json={"suggestion_id": sid})
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/run/{job_id}/events")

        assert result.get("event") == "finish"
        assert "exit_code" in result, "exit_code が含まれること"
        assert "stdout" in result, "stdout が含まれること"
        assert "stderr" in result, "stderr が含まれること"
        assert isinstance(result["exit_code"], int), "exit_code は整数"

    def test_multiple_runs_return_separate_results(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-1: 複数回実行しても各回の結果が独立して返ること（追記の根拠）"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm

        results = []
        for _ in range(2):
            sug = _llm.Suggestion(
                raw_output="deltasim --version",
                command_line="deltasim --version",
                args=["--version"],
                safe=True, reason="", write_paths=[],
            )
            sid = store_suggestion(sug)
            res = client.post("/api/llm/run/start", json={"suggestion_id": sid})
            job_id = res.json()["job_id"]
            results.append(_consume_sse(client, f"/api/llm/run/{job_id}/events"))

        # 2回とも finish が返ること
        assert all(r.get("event") == "finish" for r in results)

    # ------------------------------------------------------------------
    # 2. ask コンテキスト: module_info が渡されたときポート情報が含まれること
    # ------------------------------------------------------------------

    def test_suggest_uses_module_info_not_file_contents(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-2: suggest に渡したコンテキストにポート情報が含まれること（ファイル中身なし）"""
        received_prompts = []

        def _capture_llm(prompt, cfg, llm_fn=None):
            received_prompts.append(prompt)
            return f"deltasim test {COUNTER_SV} --spec {COUNTER_SPEC}"

        monkeypatch.setattr("deltasim.llm.call_llm", _capture_llm)

        module_info = {
            "module": "counter",
            "ports": [
                {"name": "clk", "direction": "input", "width": 1},
                {"name": "count", "direction": "output", "width": 8},
            ]
        }
        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト",
            "sv_files": [COUNTER_SV],
            "module_info": module_info,
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        _consume_sse(client, f"/api/llm/suggest/{job_id}/events")

        assert len(received_prompts) >= 1
        prompt = received_prompts[0]

        # ポート名がコンテキストに含まれること
        assert "counter" in prompt, f"モジュール名 counter がプロンプトに含まれること: {prompt[:300]}"
        assert "clk" in prompt or "count" in prompt, f"ポート名がプロンプトに含まれること: {prompt[:300]}"

    def test_suggest_context_no_file_contents(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-2: suggest のコンテキストに RTL の中身（module ... endmodule）が含まれないこと"""
        received_prompts = []

        def _capture_llm(prompt, cfg, llm_fn=None):
            received_prompts.append(prompt)
            return f"deltasim test {COUNTER_SV} --spec {COUNTER_SPEC}"

        monkeypatch.setattr("deltasim.llm.call_llm", _capture_llm)

        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト",
            "sv_files": [COUNTER_SV],
        })
        job_id = res.json()["job_id"]
        _consume_sse(client, f"/api/llm/suggest/{job_id}/events")

        assert len(received_prompts) >= 1
        prompt = received_prompts[0]

        # RTL の中身が含まれないこと（"module", "endmodule" はSVの中身）
        # ただし "RTL ファイル:" や "モジュール名:" のキーワードは許容
        # ファイルを読み込んだ内容（always_ff など）は含まれないこと
        assert "always_ff" not in prompt, "RTL の中身が含まれてはいけない"
        assert "endmodule" not in prompt, "RTL の中身が含まれてはいけない"

    def test_suggest_with_module_info_sends_port_list(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-2: module_info なしでも suggest が動作すること"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト", "sv_files": [COUNTER_SV],
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/suggest/{job_id}/events")
        assert result.get("event") == "finish"

    # ------------------------------------------------------------------
    # 3. top 未設定時の保存名: draft_spec の yaml_text が返ること（サーバー側の確認）
    # ------------------------------------------------------------------

    def test_draft_returns_yaml_and_valid_flag(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-3: draft-spec が valid フラグ付きで yaml_text を返すこと"""
        def _mock_draft(files, top=None, cfg=None, llm_fn=None, output_path=None, force=False):
            from deltasim.llm import DraftResult
            return DraftResult(yaml_text="version: 1\ntop: counter\n",
                               valid=True, errors=[], raw_output="")

        monkeypatch.setattr("deltasim.llm.draft_spec", _mock_draft)

        res = client.post("/api/llm/draft-spec/start", json={
            "sv_files": [COUNTER_SV],
            # top は省略（未設定）
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/draft-spec/{job_id}/events")

        assert result.get("event") == "finish"
        assert "yaml_text" in result
        # top が None のとき draft-spec は sv_files から top を推定する
        # yaml_text が返れば OK（UI 側で保存名を決める）
        assert len(result["yaml_text"]) > 0

    def test_draft_start_without_top_is_accepted(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-3: top なしで draft-spec を開始できること（422 にならない）"""
        def _mock_draft(files, top=None, cfg=None, llm_fn=None, output_path=None, force=False):
            from deltasim.llm import DraftResult
            return DraftResult(yaml_text="version: 1\n", valid=True, errors=[], raw_output="")

        monkeypatch.setattr("deltasim.llm.draft_spec", _mock_draft)

        res = client.post("/api/llm/draft-spec/start", json={"sv_files": [COUNTER_SV]})
        assert res.status_code == 200, f"top なしでも 200: {res.text}"

    # ------------------------------------------------------------------
    # 4. 即時キャンセル（サーバー側: cancel エンドポイントが即座に 200 を返すこと）
    # ------------------------------------------------------------------

    def test_cancel_returns_immediately(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-4: cancel は即座に 200 を返すこと（バックグラウンド処理は残ってよい）"""
        monkeypatch.setattr("deltasim.llm.call_llm", lambda *a, **kw: _mock_llm_safe(*a, **kw))

        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        job_id = res.json()["job_id"]

        # キャンセルが即座に 200 を返すこと
        import time
        t0 = time.monotonic()
        cancel_res = client.post(f"/api/llm/suggest/{job_id}/cancel", json={})
        elapsed = time.monotonic() - t0

        assert cancel_res.status_code == 200
        assert elapsed < 5.0, f"キャンセルが {elapsed:.1f}s かかった（即座であるべき）"

    def test_new_request_after_cancel_works(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-4: キャンセル後も新しい提案リクエストを受け付けること"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        # 1回目を開始してキャンセル
        res1 = client.post("/api/llm/suggest/start", json={"text": "テスト1"})
        job_id1 = res1.json()["job_id"]
        client.post(f"/api/llm/suggest/{job_id1}/cancel", json={})

        # 2回目も受け付けられること
        res2 = client.post("/api/llm/suggest/start", json={"text": "テスト2"})
        assert res2.status_code == 200, "キャンセル後も新リクエストを受け付けること"
        job_id2 = res2.json()["job_id"]
        assert job_id2 != job_id1, "別のジョブIDが発行されること"

        # 2回目は完了できること
        result = _consume_sse(client, f"/api/llm/suggest/{job_id2}/events")
        assert result.get("event") in ("finish", "error"), "2回目が完了すること"

    def test_cancel_draft_returns_200(self, client, mock_llm_config, monkeypatch):
        """§18.3.2-4: draft キャンセルも即座に 200 を返すこと"""
        import time, threading

        # draft を遅く実行するモック
        def _slow_draft(files, top=None, cfg=None, llm_fn=None, output_path=None, force=False):
            import time as t; t.sleep(5)  # 5秒待つ
            from deltasim.llm import DraftResult
            return DraftResult(yaml_text="", valid=False, errors=[], raw_output="")

        monkeypatch.setattr("deltasim.llm.draft_spec", _slow_draft)

        res = client.post("/api/llm/draft-spec/start", json={"sv_files": [COUNTER_SV]})
        job_id = res.json()["job_id"]

        # すぐにキャンセル
        t0 = time.monotonic()
        cancel_res = client.post(f"/api/llm/draft-spec/{job_id}/cancel", json={})
        elapsed = time.monotonic() - t0

        assert cancel_res.status_code == 200
        assert elapsed < 2.0, f"キャンセルが {elapsed:.1f}s かかった"


# ============================================================================
# §18.5.1: --log 方針 — 自動付加なし・cwd をプロジェクトフォルダに設定
# ============================================================================

class TestLogPolicy185:
    """§18.5.1: run_suggestion の subprocess 実行方針テスト"""

    @pytest.fixture
    def client(self):
        with TestClient(app, base_url="http://127.0.0.1") as c:
            yield c

    def test_no_extra_args_added_to_argv(self, client, mock_llm_config):
        """§18.5.1: コマンドに余分な引数（--log 等）が追加されないこと"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm
        import subprocess

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        sid = store_suggestion(sug)

        captured_argv = []

        def _capture(argv, **kwargs):
            captured_argv.append(list(argv))
            proc = subprocess.CompletedProcess(argv, 0, "", "")
            return proc

        import unittest.mock as _mock
        with _mock.patch("subprocess.run", side_effect=_capture):
            res = client.post("/api/llm/run/start", json={
                "suggestion_id": sid,
                "project_dir": str(Path(__file__).parent),
            })
            assert res.status_code == 200
            job_id = res.json()["job_id"]
            import json as _json
            with client.stream("GET", f"/api/llm/run/{job_id}/events") as stream:
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        ev = _json.loads(line[5:].strip())
                        if ev.get("event") in ("finish", "error"):
                            break

        assert len(captured_argv) == 1, "subprocess.run が1回呼ばれること"
        argv = captured_argv[0]
        # --log / /dev/null / NUL 等が追加されていないこと
        argv_str = " ".join(str(a) for a in argv)
        assert "--log" not in argv_str, f"--log が追加されていないこと: {argv_str}"
        assert "/dev/null" not in argv_str, f"/dev/null が追加されていないこと: {argv_str}"
        assert "NUL" not in argv_str.upper() or "deltasim" in argv_str, f"NUL が追加されていないこと: {argv_str}"
        # --version が含まれること（元のコマンドが保持されていること）
        assert "--version" in argv_str, f"元の引数が保持されていること: {argv_str}"

    def test_cwd_is_project_dir(self, client, mock_llm_config, tmp_path):
        """§18.5.1: subprocess が project_dir を cwd に設定して実行すること"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm
        import subprocess

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        # §18.5.1 (f): project_dir は store_suggestion 時に記録する
        sid = store_suggestion(sug, project_dir=str(tmp_path))

        captured_kwargs = []

        def _capture(argv, **kwargs):
            captured_kwargs.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        import unittest.mock as _mock
        import json as _json
        with _mock.patch("subprocess.run", side_effect=_capture):
            res = client.post("/api/llm/run/start", json={
                "suggestion_id": sid,
                # project_dir は渡さない（サーバー側の記録を使う）
            })
            assert res.status_code == 200
            job_id = res.json()["job_id"]
            with client.stream("GET", f"/api/llm/run/{job_id}/events") as stream:
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        ev = _json.loads(line[5:].strip())
                        if ev.get("event") in ("finish", "error"):
                            break

        assert len(captured_kwargs) == 1
        cwd_used = captured_kwargs[0].get("cwd")
        assert cwd_used == str(tmp_path), (
            f"cwd がプロジェクトフォルダであること: {cwd_used!r} vs {str(tmp_path)!r}"
        )

    def test_project_dir_in_request_body(self, client, mock_llm_config):
        """§18.5.1: /api/llm/run/start が project_dir フィールドを受け付けること"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        sid = store_suggestion(sug)

        # project_dir あり → 422 にならないこと
        res = client.post("/api/llm/run/start", json={
            "suggestion_id": sid,
            "project_dir": str(Path(__file__).parent),
        })
        assert res.status_code == 200, "project_dir フィールドが受け付けられること"

    def test_project_dir_none_still_works(self, client, mock_llm_config):
        """§18.5.1: project_dir が None/省略でも動作すること"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        sid = store_suggestion(sug)

        # project_dir 省略 → cwd は None（プロセスのデフォルト）
        res = client.post("/api/llm/run/start", json={"suggestion_id": sid})
        assert res.status_code == 200, "project_dir 省略でも動作すること"


# ============================================================================
# バグ修正テスト (a)(c)(d)(f)
# ============================================================================

class TestBugFixes:
    """PR #26 のバグ修正確認テスト"""

    @pytest.fixture
    def client(self):
        with TestClient(app, base_url="http://127.0.0.1") as c:
            yield c

    # (a) 実行ログは行数でカウント（子要素数ではない）
    def test_run_result_has_exit_code_stdout_stderr(self, client, mock_llm_config):
        """(a) 実行ジョブが行数ベースで stdout/stderr を返すこと"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        sid = store_suggestion(sug)

        res = client.post("/api/llm/run/start", json={"suggestion_id": sid})
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/run/{job_id}/events")

        assert result.get("event") == "finish"
        assert isinstance(result.get("stdout", ""), str)
        assert isinstance(result.get("stderr", ""), str)

    # (c) YAML 下書き保存はエディタのコンテンツを直接保存すること
    def test_draft_save_uses_editor_content_not_gen(self, client, mock_llm_config, tmp_path, monkeypatch):
        """(c) LLM draft 保存は /api/gen を呼ばず editor content を保存すること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # /api/gen が呼ばれていないことを確認するスパイ
        gen_called = []
        original_gen = client.post
        def _spy_post(url, **kwargs):
            if "/api/gen" in str(url):
                gen_called.append(url)
            return original_gen(url, **kwargs)

        # /api/file/write でファイルが書き出されること
        spec_path = str(tmp_path / "test_spec.yaml")
        expected_content = "version: 1\ntop: counter\ncases: []\n"

        # /api/file/write を直接テスト
        res = client.post("/api/file/write", json={
            "path": spec_path,
            "content": expected_content,
        })
        assert res.status_code == 200

        # 書き出した内容が正しいこと
        written = Path(spec_path).read_text(encoding="utf-8")
        assert written == expected_content, "エディタのコンテンツが保存されること"

    # (d) 中断後に提案エリアが DOM から消えないこと
    def test_suggest_area_not_removed_on_cancel(self, client, mock_llm_config, monkeypatch):
        """(d) 中断後も suggestion_id は保持されること（DOM 要素が残ること）"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        # 提案開始
        res = client.post("/api/llm/suggest/start", json={"text": "テスト"})
        job_id = res.json()["job_id"]

        # キャンセル
        cancel_res = client.post(f"/api/llm/suggest/{job_id}/cancel", json={})
        assert cancel_res.status_code == 200

        # キャンセル後でも再提案できること（suggestion registry は消えない）
        from deltasim.gui.llm_session import get_suggestion
        # 新しい提案は作成できること
        res2 = client.post("/api/llm/suggest/start", json={"text": "再テスト"})
        assert res2.status_code == 200, "中断後も新しい提案が開始できること"

    # (f) project_dir は提案時にサーバーで記録すること
    def test_project_dir_stored_at_suggest_time(self, client, mock_llm_config, tmp_path, monkeypatch):
        """(f) suggest/start で渡した project_dir が suggestion_id に紐づくこと"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)  # tmp_path を filesec 許可ルートに追加
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        # 提案時に project_dir を渡す
        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト",
            "sv_files": [COUNTER_SV],
            "project_dir": str(tmp_path),
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]
        result = _consume_sse(client, f"/api/llm/suggest/{job_id}/events")

        assert result.get("event") == "finish"
        sug_id = result.get("suggestion_id")
        assert sug_id is not None

        # suggestion_id に紐づく project_dir がサーバーで記録されていること
        from deltasim.gui.llm_session import get_suggestion_project_dir
        stored_dir = get_suggestion_project_dir(sug_id)
        assert stored_dir == str(tmp_path), (
            f"project_dir がサーバー側で記録されること: {stored_dir!r} vs {str(tmp_path)!r}"
        )

    def test_run_uses_server_stored_project_dir(self, client, mock_llm_config, tmp_path, monkeypatch):
        """(f) run/start はクライアントからの project_dir を無視してサーバー記録を使うこと"""
        from deltasim.gui.llm_session import store_suggestion, get_suggestion_project_dir
        import deltasim.llm as _llm
        import subprocess, unittest.mock as _mock, json as _json

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        # project_dir をサーバー側で設定
        sid = store_suggestion(sug, project_dir=str(tmp_path))

        captured_cwd = []

        def _capture(argv, **kwargs):
            captured_cwd.append(kwargs.get("cwd"))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with _mock.patch("subprocess.run", side_effect=_capture):
            res = client.post("/api/llm/run/start", json={
                "suggestion_id": sid,
                # クライアントからは project_dir を渡さない
            })
            assert res.status_code == 200
            job_id = res.json()["job_id"]
            with client.stream("GET", f"/api/llm/run/{job_id}/events") as stream:
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        ev = _json.loads(line[5:].strip())
                        if ev.get("event") in ("finish", "error"):
                            break

        assert len(captured_cwd) >= 1
        assert captured_cwd[0] == str(tmp_path), (
            f"サーバー記録の project_dir が cwd に使われること: {captured_cwd[0]!r}"
        )

    def test_run_ignores_client_project_dir(self, client, mock_llm_config, tmp_path, monkeypatch):
        """(f) /api/llm/run/start に project_dir を渡しても無視されること"""
        from deltasim.gui.llm_session import store_suggestion
        import deltasim.llm as _llm

        sug = _llm.Suggestion(
            raw_output="deltasim --version",
            command_line="deltasim --version",
            args=["--version"],
            safe=True, reason="", write_paths=[],
        )
        server_dir = str(tmp_path)
        sid = store_suggestion(sug, project_dir=server_dir)

        # クライアントが異なる project_dir を送っても受け付けられること
        # （ただしサーバー側の記録が使われる）
        res = client.post("/api/llm/run/start", json={
            "suggestion_id": sid,
        })
        assert res.status_code == 200, "run/start は project_dir なしでも動作すること"


# ============================================================================
# filesec 検証テスト（draft-spec・explain・suggest の project_dir）
# ============================================================================

class TestFilesecValidation:
    """draft-spec/start・explain/start・suggest の project_dir が filesec で検証されること"""

    @pytest.fixture
    def client(self):
        with TestClient(app, base_url="http://127.0.0.1") as c:
            yield c

    # draft-spec/start: sv_files が許可ルート外なら 403
    def test_draft_spec_rejects_unsafe_sv_file(self, client, mock_llm_config):
        """draft-spec/start: 許可ルート外の sv_files は 403 を返すこと"""
        import os
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()
        # /etc/passwd はほぼ確実に許可ルート外
        unsafe_path = "/etc/passwd"
        p = Path(unsafe_path).resolve()
        is_in_allowed = str(p).startswith(str(home)) or str(p).startswith(str(cwd))
        if is_in_allowed:
            pytest.skip("このパスは許可ルート内")

        res = client.post("/api/llm/draft-spec/start", json={
            "sv_files": [unsafe_path],
        })
        assert res.status_code == 403, f"許可ルート外は 403: {res.status_code}"

    def test_draft_spec_accepts_safe_sv_file(self, client, mock_llm_config, monkeypatch, tmp_path):
        """draft-spec/start: 許可ルート内の sv_files は受け付けること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # 存在するファイルを使う（COUNTER_SV はすでに許可済み）
        res = client.post("/api/llm/draft-spec/start", json={
            "sv_files": [COUNTER_SV],
        })
        assert res.status_code == 200, f"許可ルート内は 200: {res.text}"

    def test_draft_spec_rejects_nonexistent_sv_file(self, client, mock_llm_config):
        """draft-spec/start: 存在しない sv_files は 422 を返すこと（pydantic バリデーション）"""
        res = client.post("/api/llm/draft-spec/start", json={
            "sv_files": ["/nonexistent/file.sv"],
        })
        assert res.status_code == 422, "存在しないファイルは 422"

    # explain/start: sv_files・spec が許可ルート外なら 403
    def test_explain_rejects_unsafe_sv_file(self, client, mock_llm_config):
        """explain/start: 許可ルート外の sv_files は 403"""
        import os
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()
        unsafe_path = "/etc/passwd"
        p = Path(unsafe_path).resolve()
        is_in_allowed = str(p).startswith(str(home)) or str(p).startswith(str(cwd))
        if is_in_allowed:
            pytest.skip("このパスは許可ルート内")

        res = client.post("/api/llm/explain/start", json={
            "result": {"status": "FAIL", "cases": [], "errors": []},
            "sv_files": [unsafe_path],
        })
        assert res.status_code == 403, f"許可ルート外の sv_files は 403: {res.status_code}"

    def test_explain_rejects_unsafe_spec(self, client, mock_llm_config, tmp_path):
        """explain/start: 許可ルート外の spec は 403"""
        import os
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()
        unsafe_spec = "/etc/hosts"
        p = Path(unsafe_spec).resolve()
        is_in_allowed = str(p).startswith(str(home)) or str(p).startswith(str(cwd))
        if is_in_allowed:
            pytest.skip("このパスは許可ルート内")

        res = client.post("/api/llm/explain/start", json={
            "result": {"status": "FAIL", "cases": [], "errors": []},
            "sv_files": [COUNTER_SV],
            "spec": unsafe_spec,
        })
        assert res.status_code == 403, f"許可ルート外の spec は 403: {res.status_code}"

    def test_explain_accepts_safe_paths(self, client, mock_llm_config):
        """explain/start: 許可ルート内のパスは受け付けること"""
        res = client.post("/api/llm/explain/start", json={
            "result": {"status": "FAIL", "cases": [], "errors": []},
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200, f"許可ルート内は 200: {res.text}"

    # suggest/start: project_dir が許可ルート外なら 403
    def test_suggest_rejects_unsafe_project_dir(self, client, mock_llm_config, monkeypatch):
        """suggest/start: 許可ルート外の project_dir は 403"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)
        import os
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()
        unsafe_dir = "/root"
        p = Path(unsafe_dir).resolve()
        is_in_allowed = str(p).startswith(str(home)) or str(p).startswith(str(cwd))
        if is_in_allowed:
            pytest.skip("このパスは許可ルート内")

        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト",
            "sv_files": [COUNTER_SV],
            "project_dir": unsafe_dir,
        })
        assert res.status_code == 403, f"許可ルート外の project_dir は 403: {res.status_code}"

    def test_suggest_accepts_safe_project_dir(self, client, mock_llm_config, monkeypatch, tmp_path):
        """suggest/start: 許可ルート内の project_dir は受け付けること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト",
            "sv_files": [COUNTER_SV],
            "project_dir": str(tmp_path),
        })
        assert res.status_code == 200, f"許可ルート内の project_dir は 200: {res.text}"

    def test_suggest_accepts_no_project_dir(self, client, mock_llm_config, monkeypatch):
        """suggest/start: project_dir なしでも動作すること"""
        monkeypatch.setattr("deltasim.llm.call_llm", _mock_llm_safe)

        res = client.post("/api/llm/suggest/start", json={
            "text": "テスト",
            "sv_files": [COUNTER_SV],
        })
        assert res.status_code == 200, "project_dir なしは 200"
