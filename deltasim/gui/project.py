"""deltasim.project.yaml の読み書き。

プロジェクトファイル形式:
    version: 1
    sv_files:
      - /abs/path/counter.sv
    top: counter
    spec: /abs/path/counter_spec.yaml
    vcd: null          # VCD 出力パス（省略可）
    params: {}         # パラメータ上書き
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

PROJECT_FILENAME = "deltasim.project.yaml"


@dataclass
class DeltaSimProject:
    version: int = 1
    sv_files: List[str] = field(default_factory=list)
    top: Optional[str] = None
    spec: Optional[str] = None
    vcd: Optional[str] = None
    params: Dict[str, int] = field(default_factory=dict)
    _path: Optional[str] = field(default=None, compare=False)


def load_project(path: str | os.PathLike) -> DeltaSimProject:
    """プロジェクトファイルを読み込む。"""
    path = Path(path)
    if path.is_dir():
        path = path / PROJECT_FILENAME

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    proj = DeltaSimProject(_path=str(path))
    proj.version = int(data.get("version", 1))
    proj.sv_files = [str(f) for f in (data.get("sv_files") or [])]
    proj.top = data.get("top")
    proj.spec = data.get("spec")
    proj.vcd = data.get("vcd")
    proj.params = {str(k): int(v) for k, v in (data.get("params") or {}).items()}

    # 相対パスをプロジェクトファイルの親ディレクトリ基準で絶対化
    base = path.parent
    proj.sv_files = [_abs(f, base) for f in proj.sv_files]
    if proj.spec:
        proj.spec = _abs(proj.spec, base)
    if proj.vcd:
        proj.vcd = _abs(proj.vcd, base)

    return proj


def save_project(proj: DeltaSimProject, path: str | os.PathLike) -> None:
    """プロジェクトをファイルに保存する。"""
    path = Path(path)
    if path.is_dir():
        path = path / PROJECT_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {
        "version": proj.version,
        "sv_files": proj.sv_files,
        "top": proj.top,
        "spec": proj.spec,
        "vcd": proj.vcd,
        "params": proj.params,
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def project_to_dict(proj: DeltaSimProject) -> dict:
    return {
        "version": proj.version,
        "sv_files": proj.sv_files,
        "top": proj.top,
        "spec": proj.spec,
        "vcd": proj.vcd,
        "params": proj.params,
        "path": proj._path,
    }


def resolve_paths_from_f_file(f_file: str | os.PathLike) -> List[str]:
    """.f ファイルを読んで SV ファイルのリストを返す（後方互換ラッパー）。

    §18.2: deltasim.filelist.load_filelist を使って解析する。
    この関数は戻り値を List[str]（ファイルリスト）のみとするため、
    警告（W_FILELIST_OPT）とエラー（E001）は捨てられます。

    警告やエラーも受け取りたい場合は、load_filelist を直接使ってください::

        from deltasim.filelist import load_filelist
        fl = load_filelist(f_file)
        for w in fl.warnings:
            print(f"W ({w.code}): {w.file}:{w.line}: {w.message}")
        for e in fl.errors:
            print(f"ERROR ({e.code}): {e.file}:{e.line}: {e.message}")
        sv_files = fl.files
    """
    from deltasim.filelist import load_filelist
    fl = load_filelist(f_file)
    return fl.files


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _abs(p: str, base: Path) -> str:
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str((base / pp).resolve())
