"""ファイルアクセスセキュリティ — パストラバーサル・シンボリックリンク脱出・Windowsドライブを防ぐ。

許可ルート:
  - ユーザーのホームディレクトリ（os.path.expanduser("~")）
  - プロセスのカレントディレクトリ（os.getcwd()）
  - CLI から渡された追加ルート

ルール:
  1. Path.resolve() でシンボリックリンクを解決してから判定（脱出防止）
  2. 解決済みパスが許可ルートのいずれかのサブパスであること
  3. Windows ドライブレター（C:\ など）も Path.is_relative_to() で正しく処理
  4. 書き込み対象もすべて同じチェックを通す
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# グローバル許可ルート（CLI / server 起動時に設定）
# ---------------------------------------------------------------------------

_extra_roots: List[Path] = []


def add_extra_root(p: str | os.PathLike) -> None:
    """CLI から渡された追加の許可ルートを登録する。"""
    resolved = Path(p).resolve()
    if resolved not in _extra_roots:
        _extra_roots.append(resolved)


def clear_extra_roots() -> None:
    _extra_roots.clear()


def get_allowed_roots() -> List[Path]:
    """許可ルート一覧を返す（ホーム + cwd + 追加分）。"""
    roots = [
        Path.home().resolve(),
        Path.cwd().resolve(),
    ]
    roots.extend(_extra_roots)
    # 重複排除
    seen: set[Path] = set()
    unique: List[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

class PathSecurityError(PermissionError):
    """パスセキュリティ違反"""


def safe_path(raw: str | os.PathLike, *, must_exist: bool = False) -> Path:
    """パスを検証して解決済みの絶対パスを返す。

    Parameters
    ----------
    raw:
        ユーザー指定の生パス
    must_exist:
        True の場合、実際に存在しなければ FileNotFoundError を送出する

    Raises
    ------
    PathSecurityError:
        パストラバーサルまたは許可ルート外へのアクセス
    FileNotFoundError:
        must_exist=True かつ存在しない
    """
    resolved = Path(raw).resolve()

    if not _is_under_roots(resolved):
        raise PathSecurityError(
            f"パスへのアクセスが拒否されました: {raw!r}\n"
            f"許可ルート: {[str(r) for r in get_allowed_roots()]}"
        )

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"ファイルが見つかりません: {raw!r}")

    return resolved


def is_safe(raw: str | os.PathLike) -> bool:
    """パスが許可ルート内かどうかを返す（例外を投げない）。"""
    try:
        safe_path(raw)
        return True
    except (PathSecurityError, OSError, ValueError):
        return False


def list_dir(raw: str | os.PathLike) -> List[dict]:
    """ディレクトリ内容を返す。

    Returns
    -------
    [{"name": str, "type": "file"|"dir", "path": str, "size": int|None}, ...]
    """
    p = safe_path(raw, must_exist=True)
    if not p.is_dir():
        raise NotADirectoryError(f"ディレクトリではありません: {raw!r}")

    entries: List[dict] = []
    for child in sorted(p.iterdir()):
        try:
            child_resolved = child.resolve()
            if not _is_under_roots(child_resolved):
                continue  # 許可ルート外へのシンボリックリンクを除外
            stat = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "path": str(child_resolved),
                "size": stat.st_size if child.is_file() else None,
                "ext": child.suffix.lower() if child.is_file() else None,
            })
        except (OSError, PermissionError):
            continue
    return entries


def read_text(raw: str | os.PathLike, encoding: str = "utf-8") -> str:
    """ファイルを安全に読み込んで文字列を返す。"""
    p = safe_path(raw, must_exist=True)
    if not p.is_file():
        raise IsADirectoryError(f"ファイルではありません: {raw!r}")
    return p.read_text(encoding=encoding, errors="replace")


def write_text(raw: str | os.PathLike, content: str, encoding: str = "utf-8") -> None:
    """ファイルを安全に書き込む（ディレクトリは自動作成）。"""
    p = safe_path(raw)
    if p.is_dir():
        raise IsADirectoryError(f"ディレクトリへは書き込めません: {raw!r}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding=encoding)


# ---------------------------------------------------------------------------
# 内部ヘルパー
# ---------------------------------------------------------------------------

def _is_under_roots(resolved: Path) -> bool:
    """解決済みパスが許可ルートのいずれか配下かを返す。"""
    for root in get_allowed_roots():
        try:
            # Python 3.9+ の Path.is_relative_to() を使う
            if resolved == root or resolved.is_relative_to(root):
                return True
        except AttributeError:
            # Python 3.9 未満のフォールバック
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
    return False
