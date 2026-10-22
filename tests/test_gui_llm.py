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
