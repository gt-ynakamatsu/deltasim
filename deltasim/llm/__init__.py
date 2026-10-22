"""deltasim/llm/__init__.py — §18.3 LLM 公開 API

GUI 係もここを使う。

公開関数:
    load_llm_config(config_path=None) -> LLMConfig | None
    suggest_command(text, cfg, llm_fn=None) -> Suggestion
    run_suggestion(suggestion, yes=False, ...) -> int
    draft_spec(files, top=None, cfg=None, llm_fn=None) -> DraftResult
    explain_result(result_source, cfg=None, llm_fn=None, ...) -> str
"""
from __future__ import annotations

import sys
import json as _json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from deltasim.llm.config import LLMConfig, load_llm_config
from deltasim.llm.client import (
    call_llm, LLMCallable, LLMConnectError, LLMResponseError,
)
from deltasim.llm.safety import (
    check_command, check_session_command_deep, UnsafeCommandError,
    ALLOWED_SUBCOMMANDS, _check_write_paths,
)


# ---------------------------------------------------------------------------
# 返り値型
# ---------------------------------------------------------------------------

@dataclass
class Suggestion:
    """suggest_command の戻り値"""
    raw_output:   str        # LLM の生出力
    command_line: str        # 提案コマンド行（"deltasim test ..." 形式）
    args:         List[str]  # ["test", "counter.sv", ...]
    safe:         bool       # 安全判定
    reason:       str        # 理由（不正の場合はエラー説明）
    write_paths:  List[str] = field(default_factory=list)  # 書き込むファイルリスト


# subprocess 実行関数の型
ExecutorCallable = Callable[[List[str]], int]
"""サブプロセス実行シグネチャ: (argv) -> exit_code"""


@dataclass
class DraftResult:
    """draft_spec の戻り値"""
    yaml_text:   str         # LLM が生成した YAML テキスト
    valid:       bool        # deltasim の検証を通ったか
    errors:      List[str]   # 検証エラー（valid=False のとき）
    raw_output:  str         # LLM の生出力


# ---------------------------------------------------------------------------
# コンテキスト構築ヘルパー
# ---------------------------------------------------------------------------

_MAX_CONTEXT_CHARS = 8000  # 送信する文脈の最大文字数


def _read_truncated(path: str, max_chars: int = 4000) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return text[:max_chars] + ("...(省略)" if len(text) > max_chars else "")
    except Exception:
        return f"(ファイルを読み込めませんでした: {path})"


def _extract_yaml(text: str) -> str:
    """LLM 出力から YAML 部分を抽出する（```yaml フェンス対応）。"""
    # ```yaml ... ``` で囲まれている場合
    m = re.search(r'```(?:yaml)?\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # フェンスなし: version: や top: で始まる最初のブロックを返す
    lines = text.splitlines()
    yaml_lines = []
    in_yaml = False
    for line in lines:
        if not in_yaml and re.match(r'^(version|top|clock|reset|sim|cases|inputs|expected)\s*:', line):
            in_yaml = True
        if in_yaml:
            yaml_lines.append(line)
    if yaml_lines:
        return "\n".join(yaml_lines)
    return text.strip()


def _validate_yaml_spec(yaml_text: str) -> tuple[bool, List[str]]:
    """YAML テキストを deltasim の仕様パーサで検証する。"""
    import tempfile, os
    errors: List[str] = []
    try:
        import yaml as _yaml
        _yaml.safe_load(yaml_text)  # YAML syntax check
    except Exception as exc:
        return False, [f"YAML 構文エラー: {exc}"]
    try:
        from deltasim.tb.scenario import load_scenario, ScenarioError
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(yaml_text); path = f.name
        try:
            load_scenario(path)
        except ScenarioError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(str(exc))
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
    except ImportError:
        pass
    return (len(errors) == 0), errors


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def suggest_command(
    text: str,
    cfg: Optional[LLMConfig] = None,
    llm_fn: Optional[LLMCallable] = None,
    context: str = "",
) -> Suggestion:
    """自然言語テキストから deltasim コマンドを提案する（実行しない）。

    Args:
        text: 「counter.sv をテストして」などの指示
        cfg: LLM 設定（None なら load_llm_config() を使う）
        llm_fn: モック用 LLM 呼び出し関数
        context: 追加の文脈（ファイルリストなど）

    Returns:
        Suggestion（safe=False の場合は実行しない）
    """
    if cfg is None:
        cfg = load_llm_config()
    if cfg is None:
        raise RuntimeError("LLM が未設定です")

    allowed = sorted(ALLOWED_SUBCOMMANDS)
    prompt = (
        f"あなたは deltasim というコマンドラインツールのアシスタントです。\n"
        f"次の指示に対して、deltasim コマンドを1行だけ提案してください。\n"
        f"使えるサブコマンド: {allowed}\n"
        f"シェル記号（;|>&`$()など）は使わないこと。\n"
        f"指示: {text}\n"
    )
    if context:
        prompt += f"\n文脈:\n{context}\n"

    raw = call_llm(prompt, cfg, llm_fn)

    try:
        cmd_line, args, write_paths = check_command(raw)
        return Suggestion(
            raw_output=raw,
            command_line=cmd_line,
            args=args,
            safe=True,
            reason="",
            write_paths=write_paths,
        )
    except UnsafeCommandError as exc:
        return Suggestion(
            raw_output=raw,
            command_line="",
            args=[],
            safe=False,
            reason=str(exc),
            write_paths=[],
        )


def run_suggestion(
    suggestion: Suggestion,
    yes: bool = False,
    prompt_fn=None,
    executor: Optional[ExecutorCallable] = None,
) -> int:
    """Suggestion を確認してサブプロセスで実行する。

    Args:
        suggestion: suggest_command の戻り値
        yes: True なら確認なしで実行
        prompt_fn: 確認プロンプト関数（テスト用）。None なら input()
        executor: (argv) -> int のサブプロセス実行関数（テスト用）。
                  None なら sys.executable + subprocess.run を使う。

    Returns:
        終了コード（0 = 成功、非0 = 失敗・拒否）
    """
    import subprocess

    if not suggestion.safe:
        print(f"ERROR (E_LLM_UNSAFE_CMD): {suggestion.reason}", file=sys.stderr)
        return 2

    print(f"\n提案コマンド: {suggestion.command_line}")
    if suggestion.write_paths:
        print(f"書き込むファイル: {', '.join(suggestion.write_paths)}")

    if not yes:
        fn = prompt_fn or (lambda p: input(p))
        try:
            ans = fn("実行しますか？ [y/N] ").strip().lower()
        except EOFError:
            print("（EOF）実行しません")
            return 0
        if ans not in ("y", "yes"):
            print("実行しませんでした")
            return 0

    # subprocess で実行（stdin/stdout/stderr を親から継承）
    argv = [sys.executable, "-m", "deltasim"] + suggestion.args
    if executor is not None:
        return executor(argv)
    proc = subprocess.run(argv, shell=False)
    return proc.returncode


def draft_spec(
    files: List[str],
    top: Optional[str] = None,
    cfg: Optional[LLMConfig] = None,
    llm_fn: Optional[LLMCallable] = None,
    output_path: Optional[str] = None,
    force: bool = False,
) -> DraftResult:
    """YAML スペックの下書きを生成する。

    Args:
        files: SV ファイルパスのリスト
        top: トップモジュール名
        cfg: LLM 設定
        llm_fn: モック用 LLM 関数
        output_path: 出力先ファイルパス
        force: True なら既存ファイルを上書き

    Returns:
        DraftResult(yaml_text, valid, errors, raw_output)
    """
    if cfg is None:
        cfg = load_llm_config()
    if cfg is None:
        raise RuntimeError("LLM が未設定です")

    # RTL の内容を文脈に含める
    rtl_parts = []
    for f in files:
        rtl_parts.append(f"=== {f} ===\n{_read_truncated(f, 3000)}")
    rtl_context = "\n".join(rtl_parts)[:_MAX_CONTEXT_CHARS]

    # gen コマンドのスケルトンを取得
    skeleton = ""
    try:
        import deltasim.api as ds
        result = ds.gen(files, top=top)
        skeleton = result.get("spec_yaml", "")[:2000]
    except Exception:
        pass

    top_hint = f"トップモジュール: {top}\n" if top else ""
    prompt = (
        f"あなたは SystemVerilog 検証の専門家です。\n"
        f"{top_hint}"
        f"以下の RTL に対する deltasim テストスペック（YAML 形式）を生成してください。\n"
        f"- version: 1 から始めること\n"
        f"- cases: リストを使うこと\n"
        f"- 基本的なクロック・リセット・入出力チェックを含めること\n"
        f"- コードブロック（```yaml）で囲んで出力すること\n\n"
        f"RTL:\n{rtl_context}\n"
    )
    if skeleton:
        prompt += f"\nスケルトン参考:\n{skeleton}\n"

    raw = call_llm(prompt, cfg, llm_fn)
    yaml_text = _extract_yaml(raw)
    valid, errors = _validate_yaml_spec(yaml_text)

    result = DraftResult(yaml_text=yaml_text, valid=valid, errors=errors, raw_output=raw)

    if output_path:
        p = Path(output_path)
        if p.exists() and not force:
            raise FileExistsError(
                f"{output_path} は既に存在します。上書きには --force を指定してください"
            )
        header = ""
        if not valid:
            header = "# WARNING: このスペックは deltasim の検証を通りませんでした。\n"
            for e in errors:
                header += f"# ERROR: {e}\n"
            header += "\n"
        p.write_text(header + yaml_text, encoding="utf-8")

    return result


def explain_result(
    result_source: Union[str, Path, dict],
    rtl_files: Optional[List[str]] = None,
    spec_file: Optional[str] = None,
    cfg: Optional[LLMConfig] = None,
    llm_fn: Optional[LLMCallable] = None,
) -> str:
    """テスト失敗を LLM に解析させて日本語で説明を返す。

    Args:
        result_source: result.json のパスまたは辞書
        rtl_files: SV ファイルパスのリスト（result.json から取れない場合）
        spec_file: スペック YAML のパス
        cfg: LLM 設定
        llm_fn: モック用 LLM 関数

    Returns:
        失敗の解析テキスト（失敗がなければ「失敗はありません」）
    """
    # result_source を辞書に変換
    if isinstance(result_source, dict):
        result_data = result_source
    else:
        result_data = _json.loads(
            Path(result_source).read_text(encoding="utf-8")
        )

    # 失敗チェック
    status = result_data.get("status", "PASS")
    if status == "PASS":
        return "失敗はありません。すべてのテストが PASS しました。"

    # LLM が必要
    if cfg is None:
        cfg = load_llm_config()
    if cfg is None:
        raise RuntimeError("LLM が未設定です")

    # 失敗ケースの抽出
    failed_cases = [
        c for c in result_data.get("cases", [])
        if c.get("status") in ("FAIL", "ERROR")
    ]
    errors = result_data.get("errors", [])

    cases_text = _json.dumps({"failed_cases": failed_cases[:5], "errors": errors[:5]},
                              ensure_ascii=False, indent=2)[:3000]

    # RTL の内容
    rtl_text = ""
    if rtl_files:
        for f in rtl_files:
            rtl_text += f"=== {f} ===\n{_read_truncated(f, 2000)}\n"
    rtl_text = rtl_text[:3000]

    # スペック YAML
    spec_text = ""
    if spec_file:
        spec_text = _read_truncated(spec_file, 2000)

    prompt = (
        f"あなたは SystemVerilog 検証の専門家です。\n"
        f"以下の deltasim テスト結果を日本語で詳しく解析してください。\n"
        f"失敗の原因と修正方法を説明してください。\n\n"
        f"テスト結果:\n{cases_text}\n"
    )
    if rtl_text:
        prompt += f"\nRTL:\n{rtl_text}\n"
    if spec_text:
        prompt += f"\nスペック:\n{spec_text}\n"

    # 文脈上限チェック
    if len(prompt) > _MAX_CONTEXT_CHARS:
        prompt = prompt[:_MAX_CONTEXT_CHARS] + "\n...(文脈を省略しました)"

    return call_llm(prompt, cfg, llm_fn)
