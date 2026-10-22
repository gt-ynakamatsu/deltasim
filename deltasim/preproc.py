"""deltasim/preproc.py -- §18.9 SystemVerilog プリプロセッサ

対応指令:
  define（引数なし・引数あり・デフォルト値・複数行バックスラッシュ）
  undef
  ifdef / ifndef / elsif / else / endif
  include（"..." をファイルの場所→ +incdir+ の順に探す）
  timescale（受け付け; 時間単位への反映は将来課題 → W_TIMESCALE）
  default_nettype（受け付けるだけ）

非対応（W_UNKNOWN_DIRECTIVE を発行して無視）:
  pragma, line, __FILE__, __LINE__, その他

エラーコード:
  E_INCLUDE_LOOP  循環 include を検出
  E001            include 未発見・未定義マクロ・引数数不一致

ソースマップ:
  展開後テキストの各行番号 (1始まり) → SourceLoc(file, line) を返す。
  マクロ展開内のエラー → マクロを使用した行を指す。
  include 先のエラー → include 先ファイルの行を指す。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 公開型
# ---------------------------------------------------------------------------

class SourceLoc:
    """展開後の1行が対応する元の位置。"""
    __slots__ = ("file", "line")

    def __init__(self, file: str, line: int):
        self.file = file
        self.line = line

    def __repr__(self) -> str:
        return f"SourceLoc({self.file!r}, {self.line})"

    def __eq__(self, other):
        return isinstance(other, SourceLoc) and self.file == other.file and self.line == other.line


class PreprocError(Exception):
    """プリプロセッサエラー。"""
    def __init__(self, message: str, code: str = "E001",
                 file: str = "", line: int = 0):
        super().__init__(message)
        self.code = code
        self.file = file
        self.line = line


class PreprocWarning:
    """プリプロセッサ警告（エラーではない）。"""
    def __init__(self, message: str, code: str = "W_UNKNOWN_DIRECTIVE",
                 file: str = "", line: int = 0):
        self.message = message
        self.code = code
        self.file = file
        self.line = line

    def __repr__(self) -> str:
        return f"PreprocWarning({self.code}: {self.message!r})"


# ---------------------------------------------------------------------------
# プリプロセッサ本体
# ---------------------------------------------------------------------------

class Preprocessor:
    """SystemVerilog §18.9 プリプロセッサ。

    Parameters
    ----------
    defines:
        事前定義マクロ辞書 ``{"NAME": "VALUE"}`` または
        ``"+define+NAME=VALUE"`` 文字列リスト。
        辞書の value を None にすると空文字列マクロとして登録。
    incdirs:
        インクルードパス一覧（文字列 ``"+incdir+DIR"`` も可）。
    """

    def __init__(
        self,
        defines: Optional[Dict | List] = None,
        incdirs: Optional[List[str]] = None,
    ):
        # _macros[NAME] = (params, body)
        #   params = None              → 引数なし
        #   params = [(name, default), …]  → 引数あり
        self._macros: Dict[str, Tuple] = {}
        self._incdirs: List[str] = []
        self.warnings: List[PreprocWarning] = []

        if incdirs:
            for d in incdirs:
                self._incdirs.append(
                    d[len("+incdir+"):] if d.startswith("+incdir+") else d
                )

        if defines:
            if isinstance(defines, dict):
                for k, v in defines.items():
                    self._macros[k] = (None, v if v is not None else "")
            else:
                for s in defines:
                    self._parse_define_str(s)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def process_file(self, path: str) -> Tuple[str, Dict[int, SourceLoc]]:
        """ファイルをプリプロセスして (展開テキスト, ソースマップ) を返す。"""
        src = Path(path).read_text(encoding="utf-8")
        return self._process(src, str(Path(path).resolve()), [])

    def process_string(
        self, source: str, filename: str = "<string>"
    ) -> Tuple[str, Dict[int, SourceLoc]]:
        """文字列をプリプロセスして (展開テキスト, ソースマップ) を返す。"""
        return self._process(source, filename, [])

    # ------------------------------------------------------------------
    # 内部: メイン処理
    # ------------------------------------------------------------------

    def _process(
        self,
        source: str,
        filepath: str,
        inc_stack: List[str],
    ) -> Tuple[str, Dict[int, SourceLoc]]:
        """
        source を処理して (expanded_text, source_map) を返す。

        source_map: {expanded_line_1indexed: SourceLoc(original_file, original_line)}
        """
        # CRLF 正規化
        source = source.replace("\r\n", "\n").replace("\r", "\n")

        abs_path = _abspath(filepath)
        if abs_path in inc_stack:
            raise PreprocError(
                f"`include 循環: {filepath}",
                code="E_INCLUDE_LOOP",
                file=inc_stack[-1] if inc_stack else filepath,
                line=0,
            )
        new_stack = inc_stack + [abs_path]

        lines = source.split("\n")
        # 最後の空行は常に除去しない (行番号 1-indexed に対応させる)

        # 条件分岐スタック: [(active: bool, already_taken: bool)]
        # active: この段の条件が真か
        # already_taken: このチェーン (`ifdef chain) で既に真ブランチを通ったか
        cond_stack: List[Tuple[bool, bool]] = []

        # 出力: [(text, orig_file, orig_line), ...]
        # text は1行分（改行なし）
        out: List[Tuple[str, str, int]] = []

        i = 0
        while i < len(lines):
            raw_line = lines[i]
            orig_line_no = i + 1
            i += 1

            # バッククォートを含む行かどうかを判定（コメント・文字列内は除く）
            stripped, has_directive, has_macro = _scan_line_summary(raw_line)

            if not has_directive and not has_macro:
                # 高速パス: プリプロセッサ要素なし
                if self._active(cond_stack):
                    out.append((raw_line, filepath, orig_line_no))
                else:
                    out.append(("", filepath, orig_line_no))
                continue

            # 行全体を詳細解析
            # まず指令か確認（先頭非空白文字が ` か）
            sline = raw_line.lstrip()
            if sline.startswith("`"):
                # バッククォート指令・マクロ使用を先頭で処理
                name_m = re.match(r"`([A-Za-z_][A-Za-z0-9_$]*)", sline)
                if name_m:
                    name = name_m.group(1)
                    if name in _COND_DIRECTIVES or name in _BLOCK_DIRECTIVES:
                        # 指令処理
                        new_i, new_cond_stack, extra_lines = self._process_directive(
                            name, raw_line, orig_line_no, filepath,
                            lines, i, cond_stack, new_stack
                        )
                        i = new_i
                        cond_stack = new_cond_stack
                        out.extend(extra_lines)
                        continue

            # 通常テキスト行（マクロ使用 or 非指令バッククォート含む）
            if self._active(cond_stack):
                expanded = self._expand_line(
                    raw_line, filepath, orig_line_no, new_stack
                )
                out.append((expanded, filepath, orig_line_no))
            else:
                out.append(("", filepath, orig_line_no))

        if cond_stack:
            raise PreprocError(
                f"`ifdef/`ifndef に対応する `endif がありません",
                file=filepath, line=len(lines),
            )

        # ソースマップを構築
        source_map: Dict[int, SourceLoc] = {}
        texts = []
        for idx, (text, f, ln) in enumerate(out):
            texts.append(text)
            source_map[idx + 1] = SourceLoc(f, ln)

        expanded_text = "\n".join(texts)
        return expanded_text, source_map

    def _active(self, cond_stack: List[Tuple[bool, bool]]) -> bool:
        return all(a for a, _ in cond_stack)

    # ------------------------------------------------------------------
    # 指令処理
    # ------------------------------------------------------------------

    def _process_directive(
        self,
        name: str,
        raw_line: str,
        orig_line: int,
        filepath: str,
        lines: List[str],
        next_i: int,
        cond_stack: List[Tuple[bool, bool]],
        inc_stack: List[str],
    ) -> Tuple[int, List[Tuple[bool, bool]], List[Tuple[str, str, int]]]:
        """
        1つの指令を処理して (next_i, new_cond_stack, extra_out_lines) を返す。
        extra_out_lines は (text, file, line) のリスト。
        """
        active = self._active(cond_stack)
        extra: List[Tuple[str, str, int]] = []

        # 指令の引数（指令名の後ろ）
        m = re.match(r"\s*`" + re.escape(name) + r"\s*(.*)", raw_line, re.DOTALL)
        arg_rest = m.group(1).rstrip() if m else ""

        # --- 条件分岐 ---
        if name == "ifdef":
            macro_name = arg_rest.split()[0] if arg_rest.split() else ""
            if active:
                cond = macro_name in self._macros
                cond_stack = cond_stack + [(cond, cond)]
            else:
                cond_stack = cond_stack + [(False, True)]

        elif name == "ifndef":
            macro_name = arg_rest.split()[0] if arg_rest.split() else ""
            if active:
                cond = macro_name not in self._macros
                cond_stack = cond_stack + [(cond, cond)]
            else:
                cond_stack = cond_stack + [(False, True)]

        elif name == "elsif":
            if not cond_stack:
                raise PreprocError("`elsif without `ifdef/`ifndef",
                                   file=filepath, line=orig_line)
            macro_name = arg_rest.split()[0] if arg_rest.split() else ""
            parent_active = self._active(cond_stack[:-1])
            _cur_active, already_taken = cond_stack[-1]
            if already_taken or not parent_active:
                cond_stack = cond_stack[:-1] + [(False, True)]
            else:
                cond = macro_name in self._macros
                cond_stack = cond_stack[:-1] + [(cond, cond)]

        elif name == "else":
            if not cond_stack:
                raise PreprocError("`else without `ifdef/`ifndef",
                                   file=filepath, line=orig_line)
            parent_active = self._active(cond_stack[:-1])
            _cur_active, already_taken = cond_stack[-1]
            active_now = parent_active and not already_taken
            cond_stack = cond_stack[:-1] + [(active_now, True)]

        elif name == "endif":
            if not cond_stack:
                raise PreprocError("`endif without `ifdef/`ifndef",
                                   file=filepath, line=orig_line)
            cond_stack = cond_stack[:-1]

        # --- 定義・未定義 ---
        elif name == "define" and active:
            # 複数行対応: 行末の `\` で継続
            body_text = arg_rest
            while body_text.rstrip().endswith("\\"):
                body_text = body_text.rstrip()[:-1] + "\n"
                if next_i < len(lines):
                    body_text += lines[next_i]
                    next_i += 1
            self._register_define(body_text.strip(), filepath, orig_line)

        elif name == "undef" and active:
            uname = arg_rest.split()[0] if arg_rest.split() else ""
            self._macros.pop(uname, None)

        # --- include ---
        elif name == "include" and active:
            inc_m = re.match(r'"([^"]+)"|<([^>]+)>', arg_rest.strip())
            if not inc_m:
                raise PreprocError(
                    f"`include に有効なファイル名がありません: {arg_rest!r}",
                    file=filepath, line=orig_line,
                )
            inc_file = inc_m.group(1) or inc_m.group(2)
            resolved = self._resolve_include(inc_file, filepath, orig_line)
            sub_src = Path(resolved).read_text(encoding="utf-8")
            sub_text, sub_map = self._process(sub_src, resolved, inc_stack)
            # サブファイルの出力行を extra に追加
            sub_lines = sub_text.split("\n")
            for j, sl in enumerate(sub_lines):
                sub_orig = sub_map.get(j + 1, SourceLoc(resolved, j + 1))
                extra.append((sl, sub_orig.file, sub_orig.line))

        # --- timescale / default_nettype ---
        elif name == "timescale" and active:
            self.warnings.append(PreprocWarning(
                f"`timescale {arg_rest} — 時間単位への反映は未対応",
                code="W_TIMESCALE",
                file=filepath, line=orig_line,
            ))

        elif name == "default_nettype":
            pass  # 受け付けるだけ

        # --- 非対応 ---
        else:
            if active and name not in (
                "define", "undef", "ifdef", "ifndef", "elsif", "else", "endif",
                "include", "timescale", "default_nettype",
            ):
                self.warnings.append(PreprocWarning(
                    f"非対応のプリプロセッサ指令 `{name} を無視します",
                    code="W_UNKNOWN_DIRECTIVE",
                    file=filepath, line=orig_line,
                ))

        return next_i, cond_stack, extra

    # ------------------------------------------------------------------
    # `define 登録
    # ------------------------------------------------------------------

    def _register_define(self, body_text: str, filepath: str, line: int):
        """define 指令のボディをパースしてマクロ登録する。"""
        if not body_text:
            raise PreprocError("`define に名前がありません",
                               file=filepath, line=line)
        m = re.match(r'([A-Za-z_][A-Za-z0-9_$]*)', body_text)
        if not m:
            raise PreprocError(f"`define の名前が不正: {body_text[:20]!r}",
                               file=filepath, line=line)
        name = m.group(1)
        rest = body_text[len(name):]

        params: Optional[List[Tuple[str, Optional[str]]]] = None
        if rest.startswith("("):
            # 引数リスト
            depth = 1
            end = 1
            while end < len(rest) and depth > 0:
                if rest[end] == '(':
                    depth += 1
                elif rest[end] == ')':
                    depth -= 1
                end += 1
            params_str = rest[1:end - 1]
            rest = rest[end:]
            params = _parse_params(params_str)

        body = rest.lstrip(" \t")
        self._macros[name] = (params, body)

    # ------------------------------------------------------------------
    # マクロ展開（行内）
    # ------------------------------------------------------------------

    def _expand_line(
        self,
        line: str,
        filepath: str,
        orig_line: int,
        inc_stack: List[str],
    ) -> str:
        """1行中のマクロ使用を展開して文字列を返す。"""
        return self._expand_text(line, filepath, orig_line, inc_stack, depth=0)

    def _expand_text(
        self,
        text: str,
        filepath: str,
        orig_line: int,
        inc_stack: List[str],
        depth: int = 0,
        arg_map: Optional[Dict[str, str]] = None,
    ) -> str:
        """テキスト中のバッククォート式・マクロ引数を再帰的に展開する。"""
        if depth > 128:
            raise PreprocError(
                "マクロ展開が深すぎます（自己再帰の可能性）",
                file=filepath, line=orig_line,
            )
        if arg_map is None:
            arg_map = {}

        # 引数マップが空でない場合: 先にパラメータ名を置換（バッククォートなし参照）
        if arg_map:
            # 識別子境界で置換（文字列・コメント内は保護）
            text = _subst_params(text, arg_map)

        result = []
        pos = 0
        in_str = False
        in_line_comment = False
        in_block_comment = False

        while pos < len(text):
            c = text[pos]

            # ブロックコメント開始
            if not in_str and not in_line_comment and c == '/' and pos + 1 < len(text) and text[pos + 1] == '*':
                in_block_comment = True
                result.append(c)
                pos += 1
                result.append(text[pos])
                pos += 1
                continue
            if in_block_comment:
                result.append(c)
                if c == '*' and pos + 1 < len(text) and text[pos + 1] == '/':
                    result.append(text[pos + 1])
                    pos += 2
                    in_block_comment = False
                else:
                    pos += 1
                continue

            # 行コメント
            if not in_str and c == '/' and pos + 1 < len(text) and text[pos + 1] == '/':
                result.append(text[pos:])  # 残り全部をコピー
                break

            # 文字列リテラル
            if c == '"' and not in_line_comment:
                in_str = not in_str
                result.append(c)
                pos += 1
                continue
            if in_str:
                if c == '\\' and pos + 1 < len(text):
                    result.append(c)
                    result.append(text[pos + 1])
                    pos += 2
                else:
                    result.append(c)
                    pos += 1
                continue

            # バッククォート
            if c == '`':
                pos += 1
                # 名前読み取り
                name_start = pos
                while pos < len(text) and (text[pos].isalnum() or text[pos] == '_'):
                    pos += 1
                name = text[name_start:pos]
                if not name:
                    # `` `` など特殊形式 → そのまま
                    result.append('`')
                    continue

                # 引数マップで解決（マクロ内のマクロ引数名）
                if name in arg_map:
                    result.append(arg_map[name])
                    continue

                # マクロ名チェック
                if name in self._macros:
                    params, body = self._macros[name]
                    if params is None:
                        # 引数なし
                        expanded = self._expand_text(
                            body, filepath, orig_line, inc_stack, depth + 1, arg_map
                        )
                        result.append(expanded)
                    else:
                        # 引数付き: 後続の '(' を読む
                        # 空白をスキップ
                        while pos < len(text) and text[pos] in (' ', '\t'):
                            pos += 1
                        if pos < len(text) and text[pos] == '(':
                            raw_str, pos = _read_paren_args(text, pos)
                            raw_args = _split_args(raw_str)
                        else:
                            raw_args = []
                        expanded = self._call_macro(
                            name, params, body, raw_args,
                            filepath, orig_line, inc_stack, depth + 1, arg_map
                        )
                        result.append(expanded)
                elif name in _DIRECTIVES:
                    # 非指令バッククォート（directives already handled above）
                    self.warnings.append(PreprocWarning(
                        f"非対応の指令 `{name} を無視します",
                        code="W_UNKNOWN_DIRECTIVE",
                        file=filepath, line=orig_line,
                    ))
                else:
                    raise PreprocError(
                        f"未定義のマクロ: `{name}",
                        file=filepath, line=orig_line,
                    )
                continue

            result.append(c)
            pos += 1

        return "".join(result)

    def _call_macro(
        self,
        name: str,
        params: List[Tuple[str, Optional[str]]],
        body: str,
        raw_args: List[str],
        filepath: str,
        orig_line: int,
        inc_stack: List[str],
        depth: int,
        outer_arg_map: Optional[Dict[str, str]] = None,
    ) -> str:
        """引数付きマクロを展開する。"""
        required = sum(1 for _, d in params if d is None)
        max_args = len(params)
        if len(raw_args) < required or len(raw_args) > max_args:
            raise PreprocError(
                f"マクロ `{name}: 引数数不一致 "
                f"(定義 {max_args}, 実際 {len(raw_args)})",
                file=filepath, line=orig_line,
            )
        arg_map: Dict[str, str] = {}
        if outer_arg_map:
            arg_map.update(outer_arg_map)
        for j, (pname, default) in enumerate(params):
            if j < len(raw_args):
                val = raw_args[j].strip()
                # 引数の値内のマクロも展開
                val = self._expand_text(val, filepath, orig_line, inc_stack, depth)
                arg_map[pname] = val
            elif default is not None:
                arg_map[pname] = default
            else:
                raise PreprocError(
                    f"マクロ `{name}: 引数 {pname!r} が不足しています",
                    file=filepath, line=orig_line,
                )
        return self._expand_text(body, filepath, orig_line, inc_stack, depth, arg_map)

    # ------------------------------------------------------------------
    # インクルードパス解決
    # ------------------------------------------------------------------

    def _resolve_include(
        self, inc_file: str, from_file: str, line: int
    ) -> str:
        """インクルードファイルのパスを解決する。

        探索順:
          1. from_file のディレクトリからの相対パス
          2. incdirs の各ディレクトリ
        """
        from_dir = os.path.dirname(os.path.abspath(from_file))
        candidate = os.path.normpath(os.path.join(from_dir, inc_file))
        if os.path.isfile(candidate):
            return candidate

        for d in self._incdirs:
            candidate = os.path.normpath(os.path.join(d, inc_file))
            if os.path.isfile(candidate):
                return candidate

        raise PreprocError(
            f"`include ファイルが見つかりません: {inc_file!r} "
            f"(from {from_file}:{line})",
            code="E001",
            file=from_file,
            line=line,
        )

    # ------------------------------------------------------------------
    # +define+ 文字列のパース
    # ------------------------------------------------------------------

    def _parse_define_str(self, s: str):
        """+define+NAME または +define+NAME=VALUE 文字列を解析して登録。"""
        s = s.strip()
        if s.startswith("+define+"):
            s = s[len("+define+"):]
        if "=" in s:
            name, val = s.split("=", 1)
            self._macros[name.strip()] = (None, val)
        else:
            self._macros[s.strip()] = (None, "1")


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _abspath(path: str) -> str:
    if path.startswith("<"):
        return path
    return os.path.abspath(path)


def _scan_line_summary(line: str) -> Tuple[str, bool, bool]:
    """行を簡易スキャンして (stripped, has_directive, has_macro) を返す。"""
    has_bt = "`" in line
    return line, has_bt, has_bt


def _parse_params(
    params_str: str,
) -> List[Tuple[str, Optional[str]]]:
    """引数定義文字列 "A, B = default, C" をパースする。"""
    params = []
    for part in params_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            pname, default = part.split("=", 1)
            params.append((pname.strip(), default.strip()))
        else:
            params.append((part, None))
    return params


def _read_paren_args(text: str, pos: int) -> Tuple[str, int]:
    """'(' から ')' まで読んで (内側文字列, 次の pos) を返す。"""
    assert text[pos] == '(', f"Expected '(' at pos {pos}, got {text[pos]!r}"
    depth = 1
    pos += 1
    start = pos
    in_str = False
    while pos < len(text) and depth > 0:
        c = text[pos]
        if in_str:
            if c == '\\':
                pos += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
        pos += 1
    return text[start:pos - 1], pos


def _subst_params(text: str, arg_map: Dict[str, str]) -> str:
    """マクロ引数名をその値で置換する（識別子境界でマッチ）。

    文字列リテラル・コメント内は保護する。
    """
    if not arg_map:
        return text
    # 識別子パターンで置換
    pattern = re.compile(r'\b(' + '|'.join(re.escape(k) for k in arg_map) + r')\b')
    result = []
    pos = 0
    in_str = False
    i = 0
    while i < len(text):
        c = text[i]
        # コメント
        if not in_str and c == '/' and i + 1 < len(text) and text[i+1] == '/':
            result.append(text[i:])
            break
        if not in_str and c == '/' and i + 1 < len(text) and text[i+1] == '*':
            # ブロックコメント: スキップ
            end = text.find('*/', i + 2)
            if end >= 0:
                result.append(text[i:end + 2])
                i = end + 2
            else:
                result.append(text[i:])
                break
            continue
        # 文字列
        if c == '"':
            in_str = not in_str
            result.append(c)
            i += 1
            continue
        if in_str:
            if c == '\\' and i + 1 < len(text):
                result.append(c)
                result.append(text[i+1])
                i += 2
            else:
                result.append(c)
                i += 1
            continue
        # バッククォート: そのままスキップ（バッククォート処理は別途）
        if c == '`':
            result.append(c)
            i += 1
            continue
        # 識別子の開始
        if c.isalpha() or c == '_':
            id_start = i
            while i < len(text) and (text[i].isalnum() or text[i] == '_' or text[i] == '$'):
                i += 1
            ident = text[id_start:i]
            if ident in arg_map:
                result.append(arg_map[ident])
            else:
                result.append(ident)
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _split_args(text: str) -> List[str]:
    """カンマで引数を分割する（括弧・文字列内は無視）。"""
    args = []
    current: List[str] = []
    depth = 0
    in_str = False
    i = 0
    while i < len(text):
        c = text[i]
        if in_str:
            current.append(c)
            if c == '\\' and i + 1 < len(text):
                i += 1
                current.append(text[i])
            elif c == '"':
                in_str = False
        elif c == '"':
            current.append(c)
            in_str = True
        elif c in ('(', '['):
            depth += 1
            current.append(c)
        elif c in (')', ']'):
            depth -= 1
            current.append(c)
        elif c == ',' and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(c)
        i += 1
    args.append("".join(current))
    return args


# ---------------------------------------------------------------------------
# 指令名セット
# ---------------------------------------------------------------------------

_COND_DIRECTIVES = frozenset({
    "ifdef", "ifndef", "elsif", "else", "endif",
})
_BLOCK_DIRECTIVES = frozenset({
    "define", "undef", "include", "timescale", "default_nettype",
    "pragma", "line", "__FILE__", "__LINE__",
    "begin_keywords", "end_keywords", "resetall",
    "unconnected_drive", "nounconnected_drive",
    "celldefine", "endcelldefine",
})
_DIRECTIVES = _COND_DIRECTIVES | _BLOCK_DIRECTIVES


# ---------------------------------------------------------------------------
# 便利関数
# ---------------------------------------------------------------------------

def preprocess_file(
    path: str,
    defines: Optional[Dict | List] = None,
    incdirs: Optional[List[str]] = None,
) -> Tuple[str, Dict[int, SourceLoc]]:
    """ファイルをプリプロセスして (展開テキスト, ソースマップ) を返す。"""
    pp = Preprocessor(defines=defines, incdirs=incdirs)
    return pp.process_file(path)


def preprocess_string(
    source: str,
    filename: str = "<string>",
    defines: Optional[Dict | List] = None,
    incdirs: Optional[List[str]] = None,
) -> Tuple[str, Dict[int, SourceLoc]]:
    """文字列をプリプロセスして (展開テキスト, ソースマップ) を返す。"""
    pp = Preprocessor(defines=defines, incdirs=incdirs)
    return pp.process_string(source, filename)
