"""deltasim/llm/safety.py — §18.3 コマンド安全性チェック

LLM が返したテキストを deltasim コマンドとして実行する前に検査する。
シェル記号・再帰・未許可コマンドを拒否する。
"""
from __future__ import annotations

import os
import re
import shlex
from typing import List, Optional, Tuple

# 実行を許可するサブコマンド（ask と llm は再帰になるので除外）
ALLOWED_SUBCOMMANDS = frozenset({
    "test", "run", "gen", "parse", "sim",
})

# シェル特殊文字（拒否対象）
_SHELL_CHARS_RE = re.compile(r'[;&|><`]|\$\(|&&|\|\|')


class UnsafeCommandError(Exception):
    """安全でないコマンドを拒否したときの例外"""
    code = "E_LLM_UNSAFE_CMD"


def check_command(llm_output: str) -> Tuple[str, List[str]]:
    """LLM の出力からコマンドを検証し (raw_cmd, args) を返す。

    Args:
        llm_output: LLM が返したテキスト（複数行可）

    Returns:
        (コマンド文字列, shlex 分割された引数リスト)

    Raises:
        UnsafeCommandError: 安全でないコマンドの場合
    """
    # テキストから最初の deltasim コマンド行を抽出
    cmd_line = _extract_cmd_line(llm_output)
    if not cmd_line:
        raise UnsafeCommandError(
            f"LLM の出力に deltasim コマンドが含まれていません: {llm_output[:80]!r}"
        )

    # シェル記号チェック
    if _SHELL_CHARS_RE.search(cmd_line):
        raise UnsafeCommandError(
            f"シェル記号が含まれているため拒否しました: {cmd_line!r}"
        )

    # shlex 分解（Windows のバックスラッシュパスを壊さないよう posix=False）
    try:
        parts = shlex.split(cmd_line, posix=(os.name != "nt"))
    except ValueError as exc:
        raise UnsafeCommandError(f"コマンドの解析に失敗しました: {exc}") from exc

    if not parts:
        raise UnsafeCommandError("空のコマンドは実行できません")

    # 先頭が "deltasim" かチェック
    if parts[0] not in ("deltasim", "deltasim.exe"):
        raise UnsafeCommandError(
            f"deltasim 以外のコマンドは実行できません: {parts[0]!r}"
        )

    if len(parts) < 2:
        raise UnsafeCommandError("サブコマンドがありません")

    # サブコマンドが許可リストにあるか
    subcmd = parts[1].lower()
    if subcmd not in ALLOWED_SUBCOMMANDS:
        raise UnsafeCommandError(
            f"サブコマンド {subcmd!r} は実行できません。"
            f"許可されているサブコマンド: {sorted(ALLOWED_SUBCOMMANDS)}"
        )

    return cmd_line, parts[2:]  # parts[0]="deltasim", parts[1]=subcmd, rest=args


def check_session_command(llm_output: str, session_commands: frozenset) -> Tuple[str, str]:
    """対話セッション内のコマンドを検証し (cmd, rest) を返す。

    Args:
        llm_output: LLM が返したテキスト
        session_commands: 許可する対話コマンド集合

    Returns:
        (コマンド名, 残りの引数文字列)

    Raises:
        UnsafeCommandError: 安全でないコマンドの場合
    """
    line = llm_output.strip().splitlines()
    # 1行だけ許可
    non_empty = [l.strip() for l in line if l.strip() and not l.strip().startswith("#")]
    if len(non_empty) != 1:
        raise UnsafeCommandError(
            f"対話セッションでは1行のコマンドのみ許可されます。"
        )
    cmd_line = non_empty[0]

    # シェル記号チェック
    if _SHELL_CHARS_RE.search(cmd_line):
        raise UnsafeCommandError(
            f"シェル記号が含まれているため拒否しました: {cmd_line!r}"
        )

    parts = cmd_line.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if cmd not in session_commands:
        raise UnsafeCommandError(
            f"対話コマンド {cmd!r} は実行できません。"
            f"許可: {sorted(session_commands)}"
        )

    return cmd, rest


def _extract_cmd_line(text: str) -> Optional[str]:
    """テキストから deltasim で始まる最初の行を抽出する。"""
    lines = text.splitlines()
    non_empty = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    # 複数の有効行がある場合は拒否（複数コマンド）
    cmd_lines = [l for l in non_empty if l.startswith("deltasim")]
    if len(cmd_lines) > 1:
        raise UnsafeCommandError(
            f"複数のコマンドが含まれているため拒否しました: {len(cmd_lines)} 行"
        )

    for line in non_empty:
        stripped = line.strip()
        # バッククォートやコードブロックを除去
        stripped = re.sub(r'^```\w*\s*', '', stripped)
        stripped = re.sub(r'```$', '', stripped).strip()
        if stripped.startswith("deltasim"):
            return stripped
    return None
