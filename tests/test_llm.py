"""tests/test_llm.py — §18.3 LLM 機能テスト

LLM はすべてモックする。CI は実際のサーバーなしで通る。
"""

from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))


# ---------------------------------------------------------------------------
# モック LLM
# ---------------------------------------------------------------------------

def _mock_llm(response: str):
    """指定レスポンスを返す LLMCallable を作る。"""
    def _fn(prompt, cfg):
        return response
    return _fn


# ---------------------------------------------------------------------------
# 1. 設定ファイル
# ---------------------------------------------------------------------------

class TestLLMConfig:

    def test_no_config_returns_none(self, tmp_path):
        """設定ファイルがなければ None"""
        from deltasim.llm.config import load_llm_config
        assert load_llm_config(str(tmp_path / "nonexistent.yaml")) is None

    def test_valid_config(self, tmp_path):
        """有効な設定を読み込める"""
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("llm:\n  model: llama3\n  endpoint: http://localhost:11434\n")
        from deltasim.llm.config import load_llm_config
        cfg = load_llm_config(str(cfg_p))
        assert cfg is not None
        assert cfg.model == "llama3"

    def test_broken_config_returns_none(self, tmp_path):
        """壊れた YAML でも None を返す（他の機能に影響しない）"""
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("llm: [broken yaml: {{\n")
        from deltasim.llm.config import load_llm_config
        cfg = load_llm_config(str(cfg_p))
        assert cfg is None

    def test_missing_llm_section_returns_none(self, tmp_path):
        """llm 節がなければ None"""
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("other_section:\n  key: value\n")
        from deltasim.llm.config import load_llm_config
        assert load_llm_config(str(cfg_p)) is None

    def test_all_defaults(self, tmp_path):
        """llm 節があれば既定値が入る"""
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("llm:\n  model: qwen\n")
        from deltasim.llm.config import load_llm_config
        cfg = load_llm_config(str(cfg_p))
        assert cfg.endpoint == "http://127.0.0.1:11434"
        assert cfg.timeout_s == 60
        assert cfg.api == "ollama"

    def test_broken_config_does_not_affect_test(self, tmp_path):
        """壊れた simconfig.yaml があっても deltasim test の結果が変わらない"""
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("{{broken yaml\n")
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.status == "PASS"

    def test_unconfigured_no_network_call(self, tmp_path):
        """LLM 未設定のとき socket/urlopen が呼ばれないこと"""
        import socket, urllib.request
        orig_create = socket.create_connection
        orig_open = urllib.request.urlopen
        calls = []

        def mock_create(*a, **kw):
            calls.append(("socket", a))
            return orig_create(*a, **kw)

        def mock_open(*a, **kw):
            calls.append(("urlopen", a))
            return orig_open(*a, **kw)

        with patch("socket.create_connection", mock_create), \
             patch("urllib.request.urlopen", mock_open):
            from deltasim.llm.config import load_llm_config
            cfg = load_llm_config(str(tmp_path / "missing.yaml"))
            assert cfg is None
            assert calls == [], f"未設定なのにネットワーク呼び出しがあった: {calls}"


# ---------------------------------------------------------------------------
# 2. 安全性チェック
# ---------------------------------------------------------------------------

class TestSafety:

    def test_valid_command(self):
        from deltasim.llm.safety import check_command
        cmd, args = check_command("deltasim test counter.sv --spec spec.yaml")
        assert "counter.sv" in " ".join(args)

    def test_shell_chars_rejected(self):
        from deltasim.llm.safety import check_command, UnsafeCommandError
        for bad in [
            "deltasim test ; rm -rf /",
            "deltasim test | cat /etc/passwd",
            "deltasim test && echo pwned",
            "deltasim test > /tmp/x",
            "deltasim test `cat /etc/passwd`",
            "deltasim test $(whoami)",
        ]:
            with pytest.raises(UnsafeCommandError):
                check_command(bad)

    def test_non_deltasim_rejected(self):
        from deltasim.llm.safety import check_command, UnsafeCommandError
        with pytest.raises(UnsafeCommandError):
            check_command("python3 -c 'import os; os.system(\"rm -rf /\")'")

    def test_recursive_ask_rejected(self):
        from deltasim.llm.safety import check_command, UnsafeCommandError
        with pytest.raises(UnsafeCommandError):
            check_command("deltasim ask 'do something'")

    def test_recursive_llm_rejected(self):
        from deltasim.llm.safety import check_command, UnsafeCommandError
        with pytest.raises(UnsafeCommandError):
            check_command("deltasim llm explain result.json")

    def test_multiple_commands_rejected(self):
        from deltasim.llm.safety import check_command, UnsafeCommandError
        with pytest.raises(UnsafeCommandError):
            check_command("deltasim test a.sv\ndeltasim run b.sv")

    def test_backtick_in_output_rejected(self):
        from deltasim.llm.safety import check_command, UnsafeCommandError
        # バッククォート付きコードブロックの中のコマンドは取り出せるが、
        # シェル記号なしなら通る
        from deltasim.llm.safety import _extract_cmd_line
        line = _extract_cmd_line("```\ndeltasim test counter.sv\n```")
        assert line is not None and "deltasim" in line


# ---------------------------------------------------------------------------
# 3. suggest_command
# ---------------------------------------------------------------------------

class TestSuggestCommand:

    def _cfg(self, tmp_path):
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("llm:\n  model: test\n")
        from deltasim.llm.config import load_llm_config
        return load_llm_config(str(cfg_p))

    def test_valid_suggestion(self, tmp_path):
        from deltasim.llm import suggest_command
        cfg = self._cfg(tmp_path)
        sug = suggest_command(
            "counter.sv をテストして",
            cfg=cfg,
            llm_fn=_mock_llm("deltasim test counter.sv --spec counter_spec.yaml"),
        )
        assert sug.safe
        assert "test" in sug.args

    def test_unsafe_suggestion(self, tmp_path):
        from deltasim.llm import suggest_command
        cfg = self._cfg(tmp_path)
        sug = suggest_command(
            "何かして",
            cfg=cfg,
            llm_fn=_mock_llm("rm -rf /"),
        )
        assert not sug.safe
        assert sug.reason != ""

    def test_run_suggestion_yes(self, tmp_path):
        """--yes で確認なしに実行される"""
        from deltasim.llm import suggest_command, run_suggestion
        cfg = self._cfg(tmp_path)
        sug = suggest_command(
            "counter.sv をテストして",
            cfg=cfg,
            llm_fn=_mock_llm(
                f"deltasim test {os.path.join(EXAMPLES, 'counter.sv')} "
                f"--spec {os.path.join(EXAMPLES, 'counter_spec.yaml')}"
            ),
        )
        assert sug.safe
        rc = run_suggestion(sug, yes=True)
        assert rc == 0

    def test_run_suggestion_no(self, tmp_path):
        """N を選んだとき実行されない"""
        from deltasim.llm import suggest_command, run_suggestion
        cfg = self._cfg(tmp_path)
        sug = suggest_command(
            "テスト",
            cfg=cfg,
            llm_fn=_mock_llm("deltasim test counter.sv --spec spec.yaml"),
        )
        # N で答える
        rc = run_suggestion(sug, yes=False, prompt_fn=lambda p: "n")
        assert rc == 0

    def test_run_suggestion_eof(self, tmp_path):
        """EOF のとき実行されない"""
        from deltasim.llm import suggest_command, run_suggestion
        cfg = self._cfg(tmp_path)
        sug = suggest_command(
            "テスト",
            cfg=cfg,
            llm_fn=_mock_llm("deltasim test counter.sv --spec spec.yaml"),
        )
        def _eof(p):
            raise EOFError
        rc = run_suggestion(sug, yes=False, prompt_fn=_eof)
        assert rc == 0

    def test_run_suggestion_unsafe(self, tmp_path):
        """不正なコマンドは実行されない"""
        from deltasim.llm import Suggestion, run_suggestion
        sug = Suggestion(
            raw_output="rm -rf /",
            command_line="",
            args=[],
            safe=False,
            reason="シェル記号",
        )
        rc = run_suggestion(sug, yes=True)
        assert rc != 0


# ---------------------------------------------------------------------------
# 4. draft_spec
# ---------------------------------------------------------------------------

class TestDraftSpec:

    def _cfg(self, tmp_path):
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("llm:\n  model: test\n")
        from deltasim.llm.config import load_llm_config
        return load_llm_config(str(cfg_p))

    MOCK_YAML = """```yaml
version: 1
top: counter
clock:
  signal: clk
  period_ns: 10
reset:
  signal: rst_n
  active_low: true
  duration_cycles: 5
sim:
  cycles: 20
cases:
  - name: basic
    expected:
      - at: 10
        check:
          count: 5
```"""

    def test_extract_yaml_from_fence(self):
        """コードブロックから YAML を抽出できること"""
        from deltasim.llm import _extract_yaml
        yaml_text = _extract_yaml(self.MOCK_YAML)
        assert "version: 1" in yaml_text
        assert "```" not in yaml_text

    def test_draft_returns_yaml(self, tmp_path):
        cfg = self._cfg(tmp_path)
        from deltasim.llm import draft_spec
        result = draft_spec(
            [os.path.join(EXAMPLES, "counter.sv")],
            cfg=cfg,
            llm_fn=_mock_llm(self.MOCK_YAML),
        )
        assert "version" in result.yaml_text

    def test_draft_valid_yaml(self, tmp_path):
        cfg = self._cfg(tmp_path)
        from deltasim.llm import draft_spec
        result = draft_spec(
            [os.path.join(EXAMPLES, "counter.sv")],
            cfg=cfg,
            llm_fn=_mock_llm(self.MOCK_YAML),
        )
        assert result.valid, f"errors: {result.errors}"

    def test_draft_invalid_yaml(self, tmp_path):
        cfg = self._cfg(tmp_path)
        from deltasim.llm import draft_spec
        result = draft_spec(
            [os.path.join(EXAMPLES, "counter.sv")],
            cfg=cfg,
            llm_fn=_mock_llm("invalid yaml: {{{{{{"),
        )
        assert not result.valid
        assert result.errors

    def test_no_overwrite_without_force(self, tmp_path):
        cfg = self._cfg(tmp_path)
        out_p = str(tmp_path / "spec.yaml")
        Path(out_p).write_text("existing content\n")
        from deltasim.llm import draft_spec
        with pytest.raises(FileExistsError):
            draft_spec(
                [os.path.join(EXAMPLES, "counter.sv")],
                cfg=cfg,
                llm_fn=_mock_llm(self.MOCK_YAML),
                output_path=out_p,
                force=False,
            )

    def test_overwrite_with_force(self, tmp_path):
        cfg = self._cfg(tmp_path)
        out_p = str(tmp_path / "spec.yaml")
        Path(out_p).write_text("existing\n")
        from deltasim.llm import draft_spec
        result = draft_spec(
            [os.path.join(EXAMPLES, "counter.sv")],
            cfg=cfg,
            llm_fn=_mock_llm(self.MOCK_YAML),
            output_path=out_p,
            force=True,
        )
        assert "version" in Path(out_p).read_text()


# ---------------------------------------------------------------------------
# 5. explain_result
# ---------------------------------------------------------------------------

class TestExplainResult:

    def _cfg(self, tmp_path):
        cfg_p = tmp_path / "simconfig.yaml"
        cfg_p.write_text("llm:\n  model: test\n")
        from deltasim.llm.config import load_llm_config
        return load_llm_config(str(cfg_p))

    def test_no_failures_no_llm_call(self, tmp_path):
        """失敗がなければ LLM を呼ばない"""
        called = []
        def _spy(prompt, cfg):
            called.append(prompt)
            return "OK"

        from deltasim.llm import explain_result
        result = {"status": "PASS", "cases": [], "errors": []}
        text = explain_result(result, cfg=self._cfg(tmp_path), llm_fn=_spy)
        assert called == [], "失敗なしなのに LLM が呼ばれた"
        assert "失敗はありません" in text

    def test_failures_call_llm(self, tmp_path):
        """失敗があれば LLM を呼ぶ"""
        called = []
        def _spy(prompt, cfg):
            called.append(prompt)
            return "解析結果です"

        from deltasim.llm import explain_result
        result = {
            "status": "FAIL",
            "cases": [{"name": "test1", "status": "FAIL", "checks": []}],
            "errors": [],
        }
        text = explain_result(result, cfg=self._cfg(tmp_path), llm_fn=_spy)
        assert called, "LLM が呼ばれなかった"
        assert "解析結果です" in text

    def test_context_includes_failures(self, tmp_path):
        """プロンプトに失敗ケースが含まれること"""
        prompts = []
        def _spy(prompt, cfg):
            prompts.append(prompt)
            return "分析"

        from deltasim.llm import explain_result
        result = {
            "status": "FAIL",
            "cases": [{"name": "fail_case", "status": "FAIL", "checks": [
                {"signal": "count", "status": "FAIL", "expected": "5", "actual": "3"}
            ]}],
            "errors": [],
        }
        explain_result(result, cfg=self._cfg(tmp_path), llm_fn=_spy)
        assert prompts
        assert "fail_case" in prompts[0]

    def test_context_includes_rtl(self, tmp_path):
        """プロンプトに RTL の内容が含まれること"""
        prompts = []
        def _spy(prompt, cfg):
            prompts.append(prompt)
            return "分析"

        from deltasim.llm import explain_result
        result = {"status": "FAIL", "cases": [{"name": "t", "status": "FAIL"}], "errors": []}
        explain_result(
            result,
            rtl_files=[os.path.join(EXAMPLES, "counter.sv")],
            cfg=self._cfg(tmp_path),
            llm_fn=_spy,
        )
        assert "counter" in prompts[0]

    def test_context_includes_spec(self, tmp_path):
        """プロンプトにスペック YAML の内容が含まれること"""
        prompts = []
        def _spy(prompt, cfg):
            prompts.append(prompt)
            return "分析"

        from deltasim.llm import explain_result
        result = {"status": "FAIL", "cases": [{"name": "t", "status": "FAIL"}], "errors": []}
        explain_result(
            result,
            spec_file=os.path.join(EXAMPLES, "counter_spec.yaml"),
            cfg=self._cfg(tmp_path),
            llm_fn=_spy,
        )
        assert "counter" in prompts[0]


# ---------------------------------------------------------------------------
# 6. CLI テスト
# ---------------------------------------------------------------------------

class TestLLMCli:

    def _cli(self, args, input_text=None):
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        runner = CliRunner()
        return runner.invoke(cli_main, args, input=input_text)

    def _write_cfg(self, tmp_path, model="test"):
        cfg_p = str(tmp_path / "simconfig.yaml")
        Path(cfg_p).write_text(f"llm:\n  model: {model}\n")
        return cfg_p

    def test_ask_unconfigured(self, tmp_path):
        """設定なしで ask → エラーメッセージ + 非0"""
        result = self._cli(["ask", "テスト", "--config", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0
        assert "E_LLM_NOT_CONFIGURED" in result.output

    def test_ask_with_mock(self, tmp_path, monkeypatch):
        """ask がモック LLM からコマンドを提案し、--yes で実行"""
        cfg_p = self._write_cfg(tmp_path)
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        mock_response = f"deltasim test {sv_p} --spec {spec_p}"

        import deltasim.llm.client as llm_client
        monkeypatch.setattr(llm_client, "_call_ollama",
                            lambda prompt, cfg: mock_response)

        result = self._cli(["ask", "counter をテスト", "--yes", "--config", cfg_p])
        assert result.exit_code == 0, result.output

    def test_ask_no_confirms_no_run(self, tmp_path, monkeypatch):
        """N を選んだとき実行されない"""
        cfg_p = self._write_cfg(tmp_path)
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")

        import deltasim.llm.client as llm_client
        monkeypatch.setattr(llm_client, "_call_ollama",
                            lambda prompt, cfg: f"deltasim test {sv_p} --spec {spec_p}")

        result = self._cli(["ask", "テスト", "--config", cfg_p], input_text="n\n")
        assert "実行しませんでした" in result.output

    def test_draft_spec_unconfigured(self, tmp_path):
        """設定なしで draft-spec → E_LLM_NOT_CONFIGURED"""
        result = self._cli([
            "llm", "draft-spec", os.path.join(EXAMPLES, "counter.sv"),
            "--config", str(tmp_path / "nope.yaml"),
        ])
        assert result.exit_code != 0
        assert "E_LLM_NOT_CONFIGURED" in result.output

    def test_draft_spec_no_overwrite(self, tmp_path, monkeypatch):
        """--force なしで既存ファイルを上書きしない"""
        cfg_p = self._write_cfg(tmp_path)
        out_p = str(tmp_path / "spec.yaml")
        Path(out_p).write_text("existing\n")
        import deltasim.llm.client as llm_client
        monkeypatch.setattr(llm_client, "_call_ollama",
                            lambda p, c: "version: 1\ntop: x\nclock:\n  signal: clk\n"
                                         "reset:\n  signal: rst\nsim:\n  cycles: 10\n"
                                         "cases:\n  - name: t\n    expected: []\n")
        result = self._cli([
            "llm", "draft-spec", os.path.join(EXAMPLES, "counter.sv"),
            "-o", out_p, "--config", cfg_p,
        ])
        assert result.exit_code != 0
        assert Path(out_p).read_text() == "existing\n"

    def test_explain_no_failures(self, tmp_path, monkeypatch):
        """失敗なしなら LLM を呼ばずに「失敗はありません」と出力"""
        cfg_p = self._write_cfg(tmp_path)
        result_p = str(tmp_path / "result.json")
        result_data = {"status": "PASS", "cases": [], "errors": []}
        Path(result_p).write_text(json.dumps(result_data))

        called = []
        import deltasim.llm.client as llm_client
        def _spy(p, c): called.append(p); return "x"
        monkeypatch.setattr(llm_client, "_call_ollama", _spy)

        result = self._cli(["llm", "explain", result_p, "--config", cfg_p])
        assert "失敗はありません" in result.output
        assert called == []


# ---------------------------------------------------------------------------
# 7. 対話セッション内の ask
# ---------------------------------------------------------------------------

class TestSessionAsk:

    def test_session_ask_unconfigured(self, tmp_path):
        """LLM 未設定のとき ask が E_LLM_NOT_CONFIGURED を出す"""
        import deltasim.api as ds
        from deltasim.interp.session import SimSession
        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")],
                            top="counter", reset_cycles=2)
        session = SimSession(sim)
        session.eval_script(f"ask テストして")
        out = "\n".join(session._output_lines)
        assert "E_LLM_NOT_CONFIGURED" in out or "LLM が未設定" in out

    def test_session_ask_unsafe_rejected(self, tmp_path, monkeypatch):
        """unsafe なコマンドが拒否される"""
        cfg_p = str(tmp_path / "simconfig.yaml")
        Path(cfg_p).write_text("llm:\n  model: test\n")

        import deltasim.llm.client as llm_client
        monkeypatch.setattr(llm_client, "_call_ollama",
                            lambda p, c: "rm -rf /")

        import deltasim.llm.config as llm_config
        monkeypatch.setattr(llm_config, "_search_paths", lambda: [Path(cfg_p)])

        import deltasim.api as ds
        from deltasim.interp.session import SimSession
        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")],
                            top="counter", reset_cycles=2)
        session = SimSession(sim)
        session.eval_script("ask テストして")
        out = "\n".join(session._output_lines)
        assert "E_LLM_UNSAFE_CMD" in out
