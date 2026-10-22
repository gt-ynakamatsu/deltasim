"""deltasim/llm/safety.py — §18.3 コマンド安全性チェック（改訂版）

LLM が返したテキストを deltasim コマンドとして実行する前に検査する。
シェル記号・再帰・未許可コマンド・書き込み先が CWD 外・ブラケット置換を拒否する。
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List, Optional, Tuple

# 実行を許可するサブコマンド（ask と llm と sim は除外）
# sim は対話セッションを起動するため ask の実行対象から除外
ALLOWED_SUBCOMMANDS = frozenset({
    "test", "run", "gen", "parse",
})

# セッション内の ask で許可するコマンド
SESSION_ALLOWED_COMMANDS = frozenset({
    "run", "examine", "force", "noforce", "restart", "bp", "when",
    "step", "until", "get", "signals", "puts", "stop",
})

# when の本体で許可するコマンド（限定的）
SESSION_WHEN_BODY_ALLOWED = frozenset({
    "examine", "stop", "puts", "bp",
})

# ファイル書き込みオプション（CLIの --json/-o/--vcd/--junit/--out-dir/--tb/--spec）
_WRITE_OPTIONS = frozenset({
    "--json", "-o", "--output", "--vcd", "--junit", "--out-dir", "--tb", "--spec", "-s",
})

# シェル特殊文字（拒否対象）
_SHELL_CHARS_RE = re.compile(r'[;&|><`]|\$\(|&&|\|\|')


class UnsafeCommandError(Exception):
    """安全でないコマンドを拒否したときの例外"""
    code = "E_LLM_UNSAFE_CMD"


# ---------------------------------------------------------------------------
# クロスプラットフォーム shlex 分割
# ---------------------------------------------------------------------------

def _split_command(cmd: str) -> List[str]:
    """コマンド文字列を引数リストに分解する。

    両 OS で同じ結果になるよう、引用符の外のバックスラッシュを
    フォワードスラッシュに正規化してから POSIX モードで分割する。
    引用符の中は変えない（"my dir\\file.sv" → ["my dir\\file.sv"]）。
    """
    normalized = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "'" and not in_double:
            in_single = not in_single
            normalized.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            normalized.append(c)
        elif c == '\\' and not in_single and not in_double:
            # 引用符の外のバックスラッシュ → フォワードスラッシュに置換
            # C:\work\a.sv → C:/work/a.sv（Windows でも有効なパス形式）
            normalized.append('/')
        else:
            normalized.append(c)
        i += 1
    cmd_normalized = ''.join(normalized)
    try:
        return shlex.split(cmd_normalized, posix=True)
    except ValueError as exc:
        raise UnsafeCommandError(f"コマンドの解析に失敗しました: {exc}") from exc


# ---------------------------------------------------------------------------
# 書き込み先チェック
# ---------------------------------------------------------------------------

def _check_write_paths(parts: List[str]) -> List[str]:
    """書き込みオプションの値を収集する（表示用）。"""
    write_paths = []
    for i, p in enumerate(parts):
        if p in _WRITE_OPTIONS and i + 1 < len(parts):
            write_paths.append(parts[i + 1])
    return write_paths


def _check_write_paths_safe(parts: List[str]) -> None:
    """書き込み先が CWD の外なら UnsafeCommandError を送出する。"""
    cwd = Path.cwd().resolve()
    for i, p in enumerate(parts):
        if p in _WRITE_OPTIONS and i + 1 < len(parts):
            dest = parts[i + 1]
            try:
                dest_resolved = Path(dest).resolve()
            except Exception:
                continue
            try:
                dest_resolved.relative_to(cwd)
            except ValueError:
                raise UnsafeCommandError(
                    f"書き込み先 {dest!r} がカレントディレクトリの外です。"
                    f"セキュリティのため拒否しました。"
                )


# ---------------------------------------------------------------------------
# Tcl コマンド再帰検査（セッション内 ask 用）
# ---------------------------------------------------------------------------

def _extract_tcl_commands(text: str) -> List[str]:
    """Tcl テキストから実行されるコマンド名を再帰的に抽出する（シンプル実装）。

    [] 置換の中のコマンド、when/for/while/if 等の本体のコマンドを検査する。
    ブレース {} の内容もコマンドとして扱う（when の本体など）。
    """
    commands = []
    depth = 0
    i = 0
    lines = text.replace('\r\n', '\n').replace('\r', '\n').splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if parts:
            commands.append(parts[0])
        # ブラケット置換 [cmd ...] の中のコマンドを抽出
        for m in re.finditer(r'\[(\w+)', line):
            commands.append(m.group(1))
    return commands


def check_session_command_deep(
    llm_output: str,
    allowed: frozenset = SESSION_ALLOWED_COMMANDS,
    when_body_allowed: frozenset = SESSION_WHEN_BODY_ALLOWED,
) -> Tuple[str, str]:
    """対話セッション内のコマンドを深く検証し (cmd, rest) を返す。

    - [] 置換を含む提案は拒否
    - when/proc 等の本体で許可リスト外が使われていれば拒否
    - ask/proc/set/source 系は拒否
    """
    # [...] を含む提案は一律拒否（コマンド置換は危険）
    if '[' in llm_output or ']' in llm_output:
        raise UnsafeCommandError(
            f"コマンド置換 [...] を含む提案は安全でないため拒否しました"
        )

    line = llm_output.strip()
    non_empty = [l.strip() for l in line.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
    if len(non_empty) != 1:
        raise UnsafeCommandError("対話セッションでは1行のコマンドのみ許可されます")

    cmd_line = non_empty[0]

    # シェル記号チェック
    if _SHELL_CHARS_RE.search(cmd_line):
        raise UnsafeCommandError(f"シェル記号が含まれているため拒否しました: {cmd_line!r}")

    parts = cmd_line.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    # 禁止コマンド（ask/proc/source/exec/eval 系）
    BLOCKED = frozenset({"ask", "proc", "source", "exec", "eval", "uplevel",
                         "namespace", "interp", "load", "package"})
    if cmd in BLOCKED:
        raise UnsafeCommandError(f"コマンド {cmd!r} は対話セッション内で使用できません")

    if cmd not in allowed:
        raise UnsafeCommandError(
            f"対話コマンド {cmd!r} は実行できません。許可: {sorted(allowed)}"
        )

    # when の本体の検査（when {cond} {body}）
    if cmd == "when" and rest:
        # ブレース引数を取り出す（ブレース内のコマンドを検査）
        body_match = re.search(r'\{([^}]*)\}\s*$', rest)
        if body_match:
            body = body_match.group(1).strip()
            body_cmds = _extract_tcl_commands(body)
            for bc in body_cmds:
                if bc not in when_body_allowed:
                    raise UnsafeCommandError(
                        f"when 本体のコマンド {bc!r} は許可されていません。"
                        f"許可: {sorted(when_body_allowed)}"
                    )

    return cmd, rest


# ---------------------------------------------------------------------------
# CLI ask 用チェック
# ---------------------------------------------------------------------------

def check_command(llm_output: str, check_write: bool = True) -> Tuple[str, List[str], List[str]]:
    """LLM の出力からコマンドを検証し (raw_cmd, subcmd_and_args, write_paths) を返す。

    Args:
        llm_output: LLM が返したテキスト
        check_write: True なら書き込み先が CWD 外なら拒否

    Returns:
        (コマンド文字列, [subcmd, *args], [書き込みパスリスト])

    Raises:
        UnsafeCommandError: 安全でないコマンドの場合
    """
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

    # [...] 置換チェック
    if '[' in cmd_line:
        raise UnsafeCommandError(
            f"コマンド置換 [...] を含む提案は安全でないため拒否しました"
        )

    parts = _split_command(cmd_line)

    if not parts:
        raise UnsafeCommandError("空のコマンドは実行できません")

    # 先頭が "deltasim" かチェック
    if parts[0].lower() not in ("deltasim", "deltasim.exe"):
        raise UnsafeCommandError(
            f"deltasim 以外のコマンドは実行できません: {parts[0]!r}"
        )

    if len(parts) < 2:
        raise UnsafeCommandError("サブコマンドがありません")

    subcmd = parts[1].lower()
    if subcmd not in ALLOWED_SUBCOMMANDS:
        raise UnsafeCommandError(
            f"サブコマンド {subcmd!r} は実行できません。"
            f"許可されているサブコマンド: {sorted(ALLOWED_SUBCOMMANDS)}"
        )

    rest_parts = parts[2:]

    # 書き込み先チェック
    write_paths = _check_write_paths(rest_parts)
    if check_write:
        _check_write_paths_safe(rest_parts)

    return cmd_line, [subcmd] + rest_parts, write_paths


def _extract_cmd_line(text: str) -> Optional[str]:
    """テキストから deltasim で始まる最初の行を抽出する。"""
    lines = text.splitlines()
    non_empty = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    # 複数の有効な deltasim 行がある場合は拒否
    cmd_lines = [l for l in non_empty if l.lower().startswith("deltasim")]
    if len(cmd_lines) > 1:
        raise UnsafeCommandError(
            f"複数のコマンドが含まれているため拒否しました: {len(cmd_lines)} 行"
        )

    for line in non_empty:
        stripped = line.strip()
        # バッククォートやコードブロックを除去
        stripped = re.sub(r'^```\w*\s*', '', stripped)
        stripped = re.sub(r'```$', '', stripped).strip()
        if stripped.lower().startswith("deltasim"):
            return stripped
    return None
