"""波形表示セットの保存・読込（§18.4 B4）

保存先: <config_dir>/deltasim/wavesets/<project_hash>.json

形式:
{
  "sets": [
    {
      "name": "デフォルト",
      "signals": ["clk", "count", "rst_n"],
      "radix": {"count": "signed", "clk": "bin"}
    }
  ]
}

プロジェクトの識別は sv_files（ソート済み）と spec の MD5 ハッシュで行う。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


def _wavesets_dir(config_dir: Optional[Path] = None) -> Path:
    if config_dir is not None:
        d = config_dir / "wavesets"
        d.mkdir(parents=True, exist_ok=True)
        return d
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = xdg if xdg else (Path.home() / ".config")
    d = Path(base) / "deltasim" / "wavesets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _project_hash(sv_files: List[str], spec: Optional[str]) -> str:
    key = "|".join(sorted(sv_files)) + "|" + (spec or "")
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _waveset_path(sv_files: List[str], spec: Optional[str], config_dir: Optional[Path]) -> Path:
    h = _project_hash(sv_files, spec)
    return _wavesets_dir(config_dir) / f"{h}.json"


def load_wavesets(
    sv_files: List[str],
    spec: Optional[str] = None,
    config_dir: Optional[Path] = None,
) -> List[dict]:
    """プロジェクトの波形表示セット一覧を返す（新しい順）。"""
    p = _waveset_path(sv_files, spec, config_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("sets", [])
    except Exception:
        return []


def save_waveset(
    sv_files: List[str],
    spec: Optional[str],
    name: str,
    signals: List[str],
    radix: Dict[str, str],
    config_dir: Optional[Path] = None,
) -> None:
    """波形表示セットを保存する（同名があれば上書き）。"""
    p = _waveset_path(sv_files, spec, config_dir)
    sets = load_wavesets(sv_files, spec, config_dir)
    sets = [s for s in sets if s.get("name") != name]
    sets.insert(0, {"name": name, "signals": list(signals), "radix": dict(radix)})
    p.write_text(json.dumps({"sets": sets}, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_waveset(
    sv_files: List[str],
    spec: Optional[str],
    name: str,
    config_dir: Optional[Path] = None,
) -> bool:
    """名前で波形表示セットを削除する。削除できた場合 True を返す。"""
    p = _waveset_path(sv_files, spec, config_dir)
    sets = load_wavesets(sv_files, spec, config_dir)
    new_sets = [s for s in sets if s.get("name") != name]
    if len(new_sets) == len(sets):
        return False
    p.write_text(json.dumps({"sets": new_sets}, ensure_ascii=False, indent=2), encoding="utf-8")
    return True
