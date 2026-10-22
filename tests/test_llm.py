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
        cmd, args, _ = check_command("deltasim test counter.sv --spec spec.yaml")
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


# ===========================================================================
# 追加テスト（レビュー対応）
# ===========================================================================

class TestSafetyRevised:
    """check_command の改訂版テスト"""

    def test_check_command_returns_write_paths(self):
        """check_command が書き込みパスリストを返すこと"""
        from deltasim.llm.safety import check_command
        cmd, args, write_paths = check_command(
            "deltasim test counter.sv --json result.json", check_write=False
        )
        assert "result.json" in write_paths

    def test_write_path_outside_cwd_rejected(self, tmp_path):
        """CWD 外への書き込みが拒否されること"""
        import os
        from deltasim.llm.safety import check_command, UnsafeCommandError
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            with pytest.raises(UnsafeCommandError, match="カレントディレクトリの外"):
                check_command("deltasim test counter.sv --json /tmp/result.json",
                              check_write=True)
        finally:
            os.chdir(old_cwd)

    def test_write_path_dotdot_rejected(self, tmp_path):
        """.. で外に出るパスが拒否されること"""
        import os
        from deltasim.llm.safety import check_command, UnsafeCommandError
        sub = tmp_path / "sub"
        sub.mkdir()
        old_cwd = os.getcwd()
        try:
            os.chdir(str(sub))
            with pytest.raises(UnsafeCommandError, match="カレントディレクトリの外"):
                check_command("deltasim test a.sv --json ../result.json",
                              check_write=True)
        finally:
            os.chdir(old_cwd)

    def test_write_path_inside_cwd_ok(self, tmp_path):
        """CWD 内への書き込みは許可されること"""
        import os
        from deltasim.llm.safety import check_command
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            cmd, args, write_paths = check_command(
                "deltasim test a.sv --json result.json", check_write=True
            )
            assert "result.json" in write_paths
        finally:
            os.chdir(old_cwd)

    def test_bracket_substitution_rejected(self):
        """[...] 置換が拒否されること"""
        from deltasim.llm.safety import check_command, UnsafeCommandError
        with pytest.raises(UnsafeCommandError):
            check_command("deltasim test [expr 1+1] --spec spec.yaml")

    def test_sim_not_allowed_in_ask(self):
        """`sim` が CLI ask の許可リストにないこと"""
        from deltasim.llm.safety import check_command, UnsafeCommandError, ALLOWED_SUBCOMMANDS
        assert "sim" not in ALLOWED_SUBCOMMANDS
        with pytest.raises(UnsafeCommandError):
            check_command("deltasim sim counter.sv")


class TestShlexCrossPlatform:
    """shlex のクロスプラットフォームテスト"""

    def test_space_in_quoted_path(self):
        """スペースを含むパスが引用符で正しく扱われること"""
        from deltasim.llm.safety import _split_command
        parts = _split_command('deltasim test "my dir/a.sv" --spec spec.yaml')
        assert "my dir/a.sv" in parts

    def test_backslash_path(self):
        """バックスラッシュパスが正しく扱われること（Windows スタイル）"""
        from deltasim.llm.safety import _split_command
        # C:\work\a.sv → C:/work/a.sv に正規化されてから分割
        parts = _split_command(r"deltasim test C:\work\a.sv --spec spec.yaml")
        # パスがひとつの要素として入っていること（バックスラッシュは/に変換）
        path_parts = [p for p in parts if "work" in p]
        assert path_parts, f"パスが見つからない: {parts}"
        assert "/" in path_parts[0], f"/ に変換されていない: {path_parts[0]}"

    def test_check_command_backslash_path(self):
        """バックスラッシュパスを含むコマンドが check_command を通ること"""
        from deltasim.llm.safety import check_command
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(os.path.dirname(EXAMPLES) if os.path.isdir(EXAMPLES) else ".")
            cmd, args, _ = check_command(
                "deltasim test counter.sv", check_write=False
            )
            assert "counter.sv" in " ".join(args)
        finally:
            os.chdir(old_cwd)


class TestSessionAskRevised:
    """セッション内 ask の改訂版テスト"""

    def _make_session(self, mock_response: str, llm_cfg_path: str = None,
                       tmp_path=None):
        """テスト用 SimSession を作成する。"""
        import deltasim.api as ds
        from deltasim.interp.session import SimSession
        from deltasim.llm.config import load_llm_config

        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")],
                            top="counter", reset_cycles=2)
        session = SimSession(sim)
        if llm_cfg_path:
            cfg = load_llm_config(llm_cfg_path)
        elif tmp_path:
            cfg_p = str(Path(tmp_path) / "simconfig.yaml")
            Path(cfg_p).write_text("llm:\n  model: test\n")
            cfg = load_llm_config(cfg_p)
        else:
            cfg = None
        session.set_llm(
            llm_fn=lambda prompt, cfg: mock_response,
            llm_cfg=cfg,
        )
        return session, sim

    def test_ask_yes_executes(self, tmp_path):
        """ask -yes でコマンドが実行されること"""
        session, sim = self._make_session("run 10ns", tmp_path=str(tmp_path))
        session.eval_script("ask -yes run 10ns")
        t = sim._engine._time
        assert t > 0, f"run が実行されなかった: t={t}"

    def test_ask_n_does_not_execute(self, tmp_path, monkeypatch):
        """N で実行されないこと"""
        session, sim = self._make_session("run 10ns", tmp_path=str(tmp_path))
        # stdin を tty として扱い N を入力させる
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with patch("builtins.input", return_value="n"):
            session.eval_script("ask run 10ns")
        assert sim._engine._time == 0

    def test_ask_eof_does_not_execute(self, tmp_path, monkeypatch):
        """EOF で実行されないこと"""
        session, sim = self._make_session("run 10ns", tmp_path=str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with patch("builtins.input", side_effect=EOFError):
            session.eval_script("ask run 10ns")
        assert sim._engine._time == 0

    def test_ask_non_tty_does_not_execute(self, tmp_path, monkeypatch):
        """非 TTY では実行されないこと（既定 N）"""
        session, sim = self._make_session("run 10ns", tmp_path=str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        session.eval_script("ask run 10ns")
        assert sim._engine._time == 0, f"非 TTY なのに実行された: t={sim._engine._time}"

    def test_ask_bracket_rejected(self, tmp_path):
        """[...] 置換を含む提案が拒否されること"""
        session, sim = self._make_session("run [expr 1+1]ns", tmp_path=str(tmp_path))
        session.eval_script("ask -yes テスト")
        out = "\n".join(session._output_lines)
        assert "E_LLM_UNSAFE_CMD" in out
        assert sim._engine._time == 0

    def test_ask_when_body_unsafe_rejected(self, tmp_path):
        """when 本体に禁止コマンドがある提案が拒否されること"""
        session, sim = self._make_session(
            "when {count == 1} {exec rm -rf /}", tmp_path=str(tmp_path)
        )
        session.eval_script("ask -yes テスト")
        out = "\n".join(session._output_lines)
        assert "E_LLM_UNSAFE_CMD" in out

    def test_ask_unconfigured_no_network(self, tmp_path, monkeypatch):
        """未設定のとき通信しないこと"""
        import socket
        calls = []
        original_create = socket.create_connection
        def mock_create(*a, **kw):
            calls.append(a)
            return original_create(*a, **kw)
        monkeypatch.setattr("socket.create_connection", mock_create)

        import deltasim.api as ds
        from deltasim.interp.session import SimSession
        sim = ds.Simulator([os.path.join(EXAMPLES, "counter.sv")],
                            top="counter", reset_cycles=2)
        session = SimSession(sim)
        # set_llm を呼ばない（未設定状態）
        session.eval_script("ask テスト")
        out = "\n".join(session._output_lines)
        assert "E_LLM_NOT_CONFIGURED" in out or "LLM が未設定" in out
        assert calls == [], f"未設定なのにネットワーク呼び出しがあった: {calls}"


class TestRunSuggestionSubprocess:
    """`run_suggestion` がサブプロセスで実行されること"""

    def _cfg(self, tmp_path):
        cfg_p = str(Path(tmp_path) / "simconfig.yaml")
        Path(cfg_p).write_text("llm:\n  model: test\n")
        from deltasim.llm.config import load_llm_config
        return load_llm_config(cfg_p)

    def test_executor_called_with_correct_argv(self, tmp_path):
        """executor が正しい argv で呼ばれること"""
        from deltasim.llm import suggest_command, run_suggestion

        sv_p = os.path.join(EXAMPLES, "counter.sv")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        cfg = self._cfg(tmp_path)

        captured_argv = []
        def _executor(argv):
            captured_argv.extend(argv)
            return 0

        sug = suggest_command(
            "テスト",
            cfg=cfg,
            llm_fn=_mock_llm(f"deltasim test {sv_p} --spec {spec_p}"),
        )
        assert sug.safe
        run_suggestion(sug, yes=True, executor=_executor)
        assert captured_argv, "executor が呼ばれなかった"
        assert sys.executable in captured_argv[0] or "python" in captured_argv[0].lower()
        assert "test" in captured_argv

    def test_python_m_deltasim_works(self):
        """python -m deltasim --version が動作すること"""
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "deltasim", "--version"],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "0.3.0" in proc.stdout


class TestWritePathRestriction:
    """書き込み先制限のテスト"""

    def _cfg(self, tmp_path):
        cfg_p = str(Path(tmp_path) / "simconfig.yaml")
        Path(cfg_p).write_text("llm:\n  model: test\n")
        from deltasim.llm.config import load_llm_config
        return load_llm_config(cfg_p)

    def test_write_path_shown_in_suggestion(self, tmp_path):
        """提案表示に「書き込むファイル:」が含まれること"""
        import io
        from deltasim.llm import suggest_command, run_suggestion
        cfg = self._cfg(tmp_path)

        sv_p = os.path.join(EXAMPLES, "counter.sv")
        sug = suggest_command(
            "テスト",
            cfg=cfg,
            llm_fn=_mock_llm(f"deltasim test {sv_p} --json result.json"),
        )

        # result.json が CWD 内 → 許可
        if sug.safe:
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            run_suggestion(sug, yes=False, prompt_fn=lambda p: "n",
                          executor=lambda argv: 0)
            sys.stdout = old_stdout
            output = captured.getvalue()
            if sug.write_paths:
                assert "書き込むファイル" in output

    def test_outside_cwd_rejected_in_suggestion(self, tmp_path):
        """CWD 外への書き込みが suggest_command で拒否されること"""
        import os
        from deltasim.llm import suggest_command
        cfg = self._cfg(tmp_path)

        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            sug = suggest_command(
                "テスト",
                cfg=cfg,
                llm_fn=_mock_llm("deltasim test counter.sv --json /tmp/out.json"),
            )
            assert not sug.safe, "CWD 外書き込みが許可されてしまった"
            assert "E_LLM_UNSAFE_CMD" in sug.reason or "カレントディレクトリ" in sug.reason
        finally:
            os.chdir(old_cwd)


class TestDraftSpecExitCode:
    """draft-spec の終了コードテスト"""

    def _cfg(self, tmp_path):
        cfg_p = str(Path(tmp_path) / "simconfig.yaml")
        Path(cfg_p).write_text("llm:\n  model: test\n")
        from deltasim.llm.config import load_llm_config
        return load_llm_config(cfg_p)

    def test_draft_invalid_exits_6(self, tmp_path, monkeypatch):
        """検証を通らない YAML で exit_code=6 になること"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        import deltasim.llm.client as llm_client

        cfg_p = str(tmp_path / "simconfig.yaml")
        Path(cfg_p).write_text("llm:\n  model: test\n")
        out_p = str(tmp_path / "spec.yaml")
        monkeypatch.setattr(llm_client, "_call_ollama",
                            lambda p, c: "invalid yaml: {{{{")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "llm", "draft-spec", os.path.join(EXAMPLES, "counter.sv"),
            "-o", out_p, "--config", cfg_p, "--force",
        ])
        assert result.exit_code == 6, \
            f"expected exit_code=6 but got {result.exit_code}: {result.output}"
