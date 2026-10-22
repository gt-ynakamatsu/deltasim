"""deltasim/filelist.py — §18.2 .f ファイルリスト読み込み

公開 API:
    load_filelist(path) -> FileList
        .f ファイルを解析して FileList を返す。

FileList:
    files:    List[str]       絶対パスに解決された SV ファイルリスト
    incdirs:  List[str]       +incdir+ で指定されたインクルードディレクトリ
    defines:  Dict[str, str]  +define+NAME[=VAL] で定義されたマクロ
    warnings: List[FileListWarning]  W_FILELIST_OPT などの警告

対応書式:
    - 1行1ファイル（SV ファイルパス）
    - // または # 以降はコメント
    - 空行は無視
    - +incdir+<dir>  +define+NAME[=VAL]
    - -f <nested.f>（入れ子 .f、循環検出あり）
    - 相対パスは .f ファイルのある場所を基準に解決
    - CRLF / LF、UTF-8 BOM 付き対応
    - Windows の \\ パス区切りも受け付ける

未対応オプション（W_FILELIST_OPT 警告を出してスキップ）:
    -v、-y、+libext+、その他 - や + で始まるオプション
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# 公開データ型
# ---------------------------------------------------------------------------

@dataclass
class FileListWarning:
    """W_FILELIST_OPT: 未対応オプションの警告"""
    code: str      # "W_FILELIST_OPT"
    message: str
    file: str      # .f ファイルのパス
    line: int      # 行番号

    def to_dict(self) -> dict:
        return {
            "code":    self.code,
            "message": self.message,
            "file":    self.file,
            "line":    self.line,
        }


@dataclass
class FileListError(Exception):
    """存在しないファイルや解析エラー"""
    code: str
    message: str
    file: str   # .f ファイルのパス
    line: int   # 行番号

    def to_dict(self) -> dict:
        return {
            "code":    self.code,
            "message": self.message,
            "file":    self.file,
            "line":    self.line,
        }


@dataclass
class FileList:
    """load_filelist の戻り値。"""
    files:    List[str]               = field(default_factory=list)
    incdirs:  List[str]               = field(default_factory=list)
    defines:  Dict[str, str]          = field(default_factory=dict)
    warnings: List[FileListWarning]   = field(default_factory=list)
    errors:   List[FileListError]     = field(default_factory=list)


# ---------------------------------------------------------------------------
# パーサ内部
# ---------------------------------------------------------------------------

def _read_lines(path: Path) -> List[tuple[int, str]]:
    """(行番号, テキスト) のリストを返す。BOM・CRLF を正規化。"""
    text = path.read_bytes()
    # UTF-8 BOM
    if text.startswith(b"\xef\xbb\xbf"):
        text = text[3:]
    text = text.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [(i + 1, line) for i, line in enumerate(text.splitlines())]


def _strip_comment(line: str) -> str:
    """// と # 以降をコメントとして除去する。"""
    # // コメント
    idx = line.find("//")
    if idx >= 0:
        line = line[:idx]
    # # コメント（行頭以外でも有効）
    idx = line.find("#")
    if idx >= 0:
        line = line[:idx]
    return line.strip()


def _resolve(p: str, base: Path) -> str:
    """相対パスを base ディレクトリ基準で解決して絶対パス文字列を返す。"""
    # Windows / Unix のパス区切りを正規化
    p = p.replace("\\", "/")
    pp = Path(p)
    if not pp.is_absolute():
        pp = base / pp
    return str(pp.resolve())


def _parse_filelist(
    f_path: Path,
    fl: FileList,
    visited: Set[str],
) -> None:
    """1つの .f ファイルを再帰的に解析する。"""
    abs_key = str(f_path.resolve())
    if abs_key in visited:
        fl.warnings.append(FileListWarning(
            code="W_FILELIST_OPT",
            message=f"循環する -f を検出しました: {f_path}",
            file=str(f_path),
            line=0,
        ))
        return
    visited.add(abs_key)

    if not f_path.exists():
        fl.errors.append(FileListError(
            code="E_FILE_NOT_FOUND",
            message=f".f ファイルが見つかりません: {f_path}",
            file=str(f_path),
            line=0,
        ))
        return

    base = f_path.parent
    lines = _read_lines(f_path)

    for lineno, raw in lines:
        line = _strip_comment(raw)
        if not line:
            continue

        # -f <nested.f>
        m = re.match(r'^-f\s+(.+)$', line, re.IGNORECASE)
        if m:
            nested = _resolve(m.group(1).strip(), base)
            _parse_filelist(Path(nested), fl, visited)
            continue

        # +incdir+<dir>[+<dir>...]
        if line.startswith("+incdir+"):
            dirs = line[len("+incdir+"):].split("+")
            for d in dirs:
                d = d.strip()
                if d:
                    fl.incdirs.append(_resolve(d, base))
            continue

        # +define+NAME[=VAL][+NAME[=VAL]...]
        if line.startswith("+define+"):
            defs = line[len("+define+"):].split("+")
            for dfn in defs:
                dfn = dfn.strip()
                if not dfn:
                    continue
                if "=" in dfn:
                    k, v = dfn.split("=", 1)
                    fl.defines[k.strip()] = v.strip()
                else:
                    fl.defines[dfn] = "1"
            continue

        # その他の + または - オプション（未対応）→ W_FILELIST_OPT
        # - で始まり、最初のトークンがオプション名（英字のみ）ならオプション扱い
        is_unsupported_opt = False
        if line.startswith("+"):
            is_unsupported_opt = True
        elif line.startswith("-"):
            # -f は上で処理済み。最初の単語がオプションフラグ（英字のみ）か判定
            first_token = line.split()[0] if line.split() else ""
            # -v, -y, --xxx などのオプションフラグ（ファイルパスは / や \ を含む）
            if re.match(r'^-[a-zA-Z_-]', first_token) and "/" not in first_token and "\\" not in first_token:
                is_unsupported_opt = True
        if is_unsupported_opt:
            fl.warnings.append(FileListWarning(
                code="W_FILELIST_OPT",
                message=f"未対応のオプション {line.split()[0]!r} は無視されます",
                file=str(f_path),
                line=lineno,
            ))
            continue

        # SV/V ファイルパス
        resolved = _resolve(line, base)
        if not Path(resolved).exists():
            fl.errors.append(FileListError(
                code="E_FILE_NOT_FOUND",
                message=f"ファイルが見つかりません: {resolved} ({f_path}:{lineno})",
                file=str(f_path),
                line=lineno,
            ))
        else:
            fl.files.append(resolved)

    visited.discard(abs_key)  # allow re-include from other paths (only cycle detection)


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def load_filelist(path: str | os.PathLike) -> FileList:
    """§18.2: .f ファイルを解析して FileList を返す。

    Args:
        path: .f ファイルのパス

    Returns:
        FileList(files, incdirs, defines, warnings, errors)

    Example::

        fl = load_filelist("files.f")
        for w in fl.warnings:
            print(f"W: {w.message}")
        ds.load(fl.files, top="top")
    """
    fl = FileList()
    _parse_filelist(Path(path), fl, visited=set())
    return fl


def expand_filelist_args(
    sv_files: tuple | list,
    filelist_args: tuple | list,
) -> tuple[list, FileList]:
    """sv_files と -f ファイルをまとめて解決する。

    CLI コマンドから呼ばれる。sv_files は通常の位置引数、
    filelist_args は --filelist (-f) で渡されたファイルリスト。

    Returns:
        (all_sv_files, combined_filelist)
        all_sv_files: 順序を保った SV ファイルのリスト（重複なし）
        combined_filelist: 全 .f ファイルのマージ結果（警告含む）
    """
    combined = FileList()
    for f_path in filelist_args:
        fl = load_filelist(f_path)
        combined.files.extend(fl.files)
        combined.incdirs.extend(fl.incdirs)
        combined.defines.update(fl.defines)
        combined.warnings.extend(fl.warnings)
        combined.errors.extend(fl.errors)

    # 通常引数と .f 由来ファイルを結合（重複除去しつつ順序保持）
    seen: set = set()
    all_files: list = []
    for f in list(sv_files) + combined.files:
        if f not in seen:
            seen.add(f)
            all_files.append(f)

    combined.files = all_files
    return all_files, combined
