"""deltasim/llm/safety.py — §18.3 コマンド安全性チェック（改訂版2）

LLM が返したテキストを deltasim コマンドとして実行する前に検査する。
シェル記号・再帰・未許可コマンド・書き込み先が CWD 外・ブラケット置換を拒否する。
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import List, Optional, Tuple

# 実行を許可するサブコマンド（ask/llm/sim は除外）
# sim は対話セッションを起動するため除外
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

# ---------------------------------------------------------------------------
# §18.3 書き込みオプション一覧（一か所で管理）
# ここに追加すれば書き込み先チェックが自動的に反映される。
# §18.5 で --log が追加される予定。
# ---------------------------------------------------------------------------
#
# 書式: long form → 使用するサブコマンド
# 短形式 (short_form) は _SHORT_WRITE_OPTS に別途定義
#
_WRITE_OPTS_LONG: Tuple[str, ...] = (
    "--json",       # test: JSON 出力ファイル
    "--vcd",        # test/run: VCD 出力ファイル
    "--junit",      # test: JUnit XML 出力ファイル
    "--out-dir",    # gen: 出力ディレクトリ
    "--tb",         # gen: TB SV 出力ファイル
    "--spec",       # gen: スペック YAML 出力ファイル（-s も）
    "--output",     # run: -o の別名
    "--log",        # §18.5: run/test/gen/parse: ログ出力ファイル
)

# 読み取りオプション（パスを取るが書き込みでないもの）
# テストで click のコマンド定義と照合する
_READ_OPTS_LONG: Tuple[str, ...] = (
    "--spec",       # test: スペック YAML 読み取り（-s も）
    "--config",     # llm ask/draft-spec/explain: simconfig.yaml
)
_READ_OPTS_SHORT: Tuple[str, ...] = (
    "-s",           # test: スペックファイル（--spec の短形式）
    "-f",           # すべて: ファイルリスト .f（読み取り）
)
_WRITE_OPTS_SHORT: Tuple[str, ...] = (
    "-o",   # run: VCD 出力 (--vcd 相当)
    "-s",   # gen/test: スペックファイル（test では読み取りだが safe 側に入れる）
)
# すべての書き込みオプションの集合（長形式 + 短形式）
_ALL_WRITE_OPTS: frozenset = frozenset(_WRITE_OPTS_LONG + _WRITE_OPTS_SHORT)

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

    引用符の外のバックスラッシュをフォワードスラッシュに正規化してから
    POSIX モードで分割することで Windows/Linux 両方で同じ結果を得る。
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
# 書き込み先チェック（Fix 2・Fix 3: --opt=value 形式も対応）
# ---------------------------------------------------------------------------

def _extract_all_write_paths(parts: List[str]) -> List[str]:
    """引数リストから書き込みパスをすべて抽出する。

    対応形式:
      --opt value        (separated)
      --opt=value        (equals)
      -ovalue            (short option attached)
      -o value           (short option separated)
      --opt-prefix=value (click の省略形も含む)
    """
    write_paths: List[str] = []
    i = 0
    while i < len(parts):
        arg = parts[i]

        # --opt=value 形式（等号で値が付いている）
        if "=" in arg and arg.startswith("-"):
            opt_part, _, val_part = arg.partition("=")
            # 既知の書き込みオプションか、あるいはそのプレフィックスか確認
            opt_lower = opt_part.lower()
            if any(opt_lower == w or w.startswith(opt_lower)
                   for w in _ALL_WRITE_OPTS if w.startswith("--")):
                write_paths.append(val_part)
            i += 1
            continue

        # 短形式の -oVALUE（値が直後に続く形）
        if arg.startswith("-") and len(arg) > 2 and arg[1] != "-":
            short = arg[:2]  # e.g. "-o"
            if short in _WRITE_OPTS_SHORT:
                write_paths.append(arg[2:])
                i += 1
                continue

        # --opt value 形式 / -o value 形式（次の引数が値）
        if arg in _ALL_WRITE_OPTS and i + 1 < len(parts):
            write_paths.append(parts[i + 1])
            i += 2
            continue

        i += 1
    return write_paths


def _check_write_paths_safe(parts: List[str]) -> None:
    """書き込み先が CWD の外なら UnsafeCommandError を送出する。"""
    cwd = Path.cwd().resolve()
    for dest in _extract_all_write_paths(parts):
        if not dest:
            continue
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
# Tcl バランスブレース解析（Fix 1）
# ---------------------------------------------------------------------------

def _extract_brace_args(text: str) -> Optional[List[str]]:
    """Tcl のブレース引数を対応を数えて分割する。

    例: "{cond} {body}" → ["{cond}", "{body}"]
    返り値は生のトークン（ブレース含む）のリスト。
    解析できない場合は None を返す。
    """
    text = text.strip()
    args = []
    i = 0
    n = len(text)
    while i < n:
        # 空白スキップ
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        if text[i] == "{":
            # ブレースカウント
            depth = 0
            start = i
            while i < n:
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            if depth != 0:
                return None  # 対応が崩れている
            args.append(text[start:i])
        elif text[i] == '"':
            start = i
            i += 1
            while i < n and text[i] != '"':
                if text[i] == '\\':
                    i += 1
                i += 1
            if i >= n:
                return None
            i += 1
            args.append(text[start:i])
        else:
            # 単語トークン
            start = i
            while i < n and text[i] not in " \t{}\"":
                i += 1
            args.append(text[start:i])
    return args


def _check_when_body(cond_str: str, body_str: str) -> None:
    """when の条件と本体を検査する。

    - 本体の中に '{' があれば拒否（制御構文の入れ子を許さない）
    - 本体のコマンドが SESSION_WHEN_BODY_ALLOWED 以外なら拒否
    - 解析できないものは拒否
    """
    # 条件式の中に [...] があれば拒否
    if "[" in cond_str:
        raise UnsafeCommandError(
            f"when 条件式の中に [...] は使えません: {cond_str!r}"
        )

    # 本体のブレース除去
    if body_str.startswith("{") and body_str.endswith("}"):
        body_inner = body_str[1:-1].strip()
    else:
        body_inner = body_str.strip()

    # 本体に '{' があれば（入れ子の制御構文）拒否
    if "{" in body_inner:
        raise UnsafeCommandError(
            f"when 本体に入れ子のブレースが含まれています。"
            f"制御構文の入れ子は許可されません: {body_inner!r}"
        )

    # 本体のコマンドを検査
    body_cmds = _extract_tcl_commands(body_inner)
    for bc in body_cmds:
        if bc not in SESSION_WHEN_BODY_ALLOWED:
            raise UnsafeCommandError(
                f"when 本体のコマンド {bc!r} は許可されていません。"
                f"許可: {sorted(SESSION_WHEN_BODY_ALLOWED)}"
            )


# ---------------------------------------------------------------------------
# Tcl コマンド名抽出
# ---------------------------------------------------------------------------

def _extract_tcl_commands(text: str) -> List[str]:
    """Tcl テキストから実行されるコマンド名を抽出する。"""
    commands = []
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


# ---------------------------------------------------------------------------
# セッション内コマンドの深い検査（Fix 1 改訂）
# ---------------------------------------------------------------------------

def check_session_command_deep(
    llm_output: str,
    allowed: frozenset = SESSION_ALLOWED_COMMANDS,
    when_body_allowed: frozenset = SESSION_WHEN_BODY_ALLOWED,
) -> Tuple[str, str]:
    """対話セッション内のコマンドを深く検証し (cmd, rest) を返す。"""
    # [...] を含む提案は一律拒否
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

    if _SHELL_CHARS_RE.search(cmd_line):
        raise UnsafeCommandError(f"シェル記号が含まれているため拒否しました: {cmd_line!r}")

    parts = cmd_line.split(None, 1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    BLOCKED = frozenset({"ask", "proc", "source", "exec", "eval", "uplevel",
                         "namespace", "interp", "load", "package"})
    if cmd in BLOCKED:
        raise UnsafeCommandError(f"コマンド {cmd!r} は対話セッション内で使用できません")

    if cmd not in allowed:
        raise UnsafeCommandError(
            f"対話コマンド {cmd!r} は実行できません。許可: {sorted(allowed)}"
        )

    # when の引数を対応ブレースで解析（Fix 1）
    if cmd == "when" and rest:
        brace_args = _extract_brace_args(rest)
        if brace_args is None:
            raise UnsafeCommandError(
                f"when の引数のブレース対応が崩れています: {rest!r}"
            )
        # when は {条件} {本体} の2引数のみ許可
        if len(brace_args) != 2:
            raise UnsafeCommandError(
                f"when の引数は {{条件}} {{本体}} の2つのみ許可されます。"
                f"({len(brace_args)} 個): {brace_args}"
            )
        cond_token, body_token = brace_args
        # ブレース内容を取り出す
        cond_inner = cond_token[1:-1] if (cond_token.startswith("{")
                                           and cond_token.endswith("}")) else cond_token
        _check_when_body(cond_inner, body_token)

    return cmd, rest


# ---------------------------------------------------------------------------
# CLI ask 用チェック
# ---------------------------------------------------------------------------

def check_command(llm_output: str, check_write: bool = True) -> Tuple[str, List[str], List[str]]:
    """LLM の出力からコマンドを検証し (raw_cmd, subcmd_and_args, write_paths) を返す。"""
    cmd_line = _extract_cmd_line(llm_output)
    if not cmd_line:
        raise UnsafeCommandError(
            f"LLM の出力に deltasim コマンドが含まれていません: {llm_output[:80]!r}"
        )

    if _SHELL_CHARS_RE.search(cmd_line):
        raise UnsafeCommandError(f"シェル記号が含まれているため拒否しました: {cmd_line!r}")

    if '[' in cmd_line:
        raise UnsafeCommandError(f"コマンド置換 [...] は拒否しました")

    parts = _split_command(cmd_line)
    if not parts:
        raise UnsafeCommandError("空のコマンドは実行できません")

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
    write_paths = _extract_all_write_paths(rest_parts)
    if check_write:
        _check_write_paths_safe(rest_parts)

    return cmd_line, [subcmd] + rest_parts, write_paths


def _extract_cmd_line(text: str) -> Optional[str]:
    """テキストから deltasim で始まる最初の行を抽出する。"""
    lines = text.splitlines()
    non_empty = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]

    cmd_lines = [l for l in non_empty if l.lower().startswith("deltasim")]
    if len(cmd_lines) > 1:
        raise UnsafeCommandError(f"複数のコマンドが含まれているため拒否しました: {len(cmd_lines)} 行")

    for line in non_empty:
        stripped = line.strip()
        stripped = re.sub(r'^```\w*\s*', '', stripped)
        stripped = re.sub(r'```$', '', stripped).strip()
        if stripped.lower().startswith("deltasim"):
            return stripped
    return None
