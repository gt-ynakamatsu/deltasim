"""最近使ったプロジェクトの保存・読込（§18.4）

設定ディレクトリ:
  Windows  : %APPDATA%\\deltasim\\recent_projects.json
  Linux/Mac: $XDG_CONFIG_HOME/deltasim/ または ~/.config/deltasim/

最大10件保存。先頭ほど新しい。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

MAX_RECENT = 10


def _config_dir() -> Path:
    """プラットフォームに応じた設定ディレクトリを返す（存在しなければ作成）。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if not base:
            base = Path.home() / "AppData" / "Roaming"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = xdg if xdg else (Path.home() / ".config")
    d = Path(base) / "deltasim"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _recent_path(config_dir: Optional[Path] = None) -> Path:
    return (config_dir or _config_dir()) / "recent_projects.json"


def load_recent(config_dir: Optional[Path] = None) -> List[dict]:
    """最近使ったプロジェクト一覧を返す（新しい順、最大 MAX_RECENT 件）。"""
    p = _recent_path(config_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data[:MAX_RECENT]
        return []
    except Exception:
        return []


def add_recent(entry: dict, config_dir: Optional[Path] = None) -> None:
    """プロジェクトを先頭に追加する（重複排除・最大 MAX_RECENT 件）。

    重複判定: sv_files（ソート済み）と spec の組み合わせ。
    """
    entries = load_recent(config_dir)
    sv_key = tuple(sorted(entry.get("sv_files", [])))
    spec_key = entry.get("spec") or ""
    entries = [
        e for e in entries
        if (
            tuple(sorted(e.get("sv_files", []))) != sv_key
            or (e.get("spec") or "") != spec_key
        )
    ]
    entries.insert(0, entry)
    entries = entries[:MAX_RECENT]
    p = _recent_path(config_dir)
    try:
        p.write_text(
            json.dumps(entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def clear_recent(config_dir: Optional[Path] = None) -> None:
    """一覧を削除する（テスト用）。"""
    p = _recent_path(config_dir)
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
