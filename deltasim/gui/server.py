"""deltasim GUI サーバ — FastAPI による REST API + 静的ファイル配信

エンドポイント一覧:
  GET  /                  → index.html
  GET  /health            → {"ok": true}
  GET  /api/examples      → examples/ フォルダのプロジェクト一覧
  POST /api/load          → RTL ファイルを解析してモジュール情報を返す
  POST /api/test          → テストを実行して結果 JSON を返す
  GET  /api/waveform      → VCD ファイルを読んで波形データを返す

セキュリティ:
  - 127.0.0.1 専用バインド（uvicorn レベルで制御）
  - ファイルパスは絶対パスを許可（ローカルツールのため）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# アプリ初期化
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="deltasim GUI",
    description="SystemVerilog シミュレータ GUI",
    version="0.1.0",
)

# 静的ファイルを /static にマウント
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# ルート
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index():
    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="index.html が見つかりません")
    return FileResponse(str(html_path))


@app.get("/health")
async def health():
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/examples — サンプルプロジェクト一覧
# ---------------------------------------------------------------------------

@app.get("/api/examples")
async def get_examples():
    """examples/ フォルダのサンプルプロジェクトを返す。"""
    examples_dir = Path(__file__).parent.parent.parent / "examples"
    if not examples_dir.exists():
        return {"examples": []}

    projects: list[dict] = []
    seen_tops: set[str] = set()

    for yaml_file in sorted(examples_dir.glob("*_spec.yaml")):
        sv_name = yaml_file.stem.replace("_spec", "") + ".sv"
        sv_file = examples_dir / sv_name
        if not sv_file.exists():
            continue

        # YAML から top 名を取得
        import yaml as _yaml
        try:
            with open(yaml_file, encoding="utf-8") as f:
                spec = _yaml.safe_load(f) or {}
        except Exception:
            continue

        top = spec.get("top") or sv_file.stem
        if top in seen_tops:
            continue
        seen_tops.add(top)

        projects.append({
            "name": top,
            "sv_files": [str(sv_file)],
            "spec": str(yaml_file),
            "top": top,
        })

    return {"examples": projects}


# ---------------------------------------------------------------------------
# /api/load — RTL ファイル解析
# ---------------------------------------------------------------------------

class LoadRequest(BaseModel):
    files: List[str]
    top: Optional[str] = None
    params: Dict[str, int] = {}

    @field_validator("files")
    @classmethod
    def files_must_exist(cls, v: List[str]) -> List[str]:
        for f in v:
            if not os.path.isfile(f):
                raise ValueError(f"ファイルが見つかりません: {f!r}")
        return v


@app.post("/api/load")
async def api_load(req: LoadRequest):
    """RTL ファイルをパース・エラボレーションしてモジュール情報を返す。"""
    from deltasim.gui.adapter import load, AdapterError

    try:
        result = load(req.files, req.top, param_overrides=req.params)
    except AdapterError as e:
        raise HTTPException(status_code=422, detail=e.to_dict())

    return result


# ---------------------------------------------------------------------------
# /api/test — テスト実行
# ---------------------------------------------------------------------------

class TestRequest(BaseModel):
    sv_files: List[str]
    spec: str
    top: Optional[str] = None
    seed: Optional[int] = None
    vcd_out: Optional[str] = None
    params: Dict[str, int] = {}

    @field_validator("sv_files")
    @classmethod
    def sv_files_must_exist(cls, v: List[str]) -> List[str]:
        for f in v:
            if not os.path.isfile(f):
                raise ValueError(f"SV ファイルが見つかりません: {f!r}")
        return v

    @field_validator("spec")
    @classmethod
    def spec_must_exist(cls, v: str) -> str:
        if not os.path.isfile(v):
            raise ValueError(f"スペックファイルが見つかりません: {v!r}")
        return v


@app.post("/api/test")
async def api_test(req: TestRequest):
    """YAML スペックに従ってテストを実行して結果 JSON を返す。"""
    from deltasim.gui.adapter import test, AdapterError

    try:
        result = test(
            req.sv_files,
            req.spec,
            top=req.top,
            seed=req.seed,
            vcd_path=req.vcd_out,
            param_overrides=req.params,
        )
    except AdapterError as e:
        raise HTTPException(status_code=500, detail=e.to_dict())

    return result


# ---------------------------------------------------------------------------
# /api/waveform — VCD 読み込み・波形データ返却
# ---------------------------------------------------------------------------

@app.get("/api/waveform")
async def api_waveform(
    path: str = Query(..., description="VCD ファイルパス"),
    t_start: int = Query(0, description="フィルタ開始時刻 (ns)"),
    t_end: Optional[int] = Query(None, description="フィルタ終了時刻 (ns)"),
    signals: Optional[str] = Query(None, description="カンマ区切りの信号名リスト"),
):
    """VCD ファイルを読み込んで波形データを JSON で返す。"""
    from deltasim.gui.vcd_reader import read_vcd, VCDReadError

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"VCD ファイルが見つかりません: {path!r}")

    sig_list = [s.strip() for s in signals.split(",")] if signals else None

    try:
        data = read_vcd(path, t_start=t_start, t_end=t_end, signals=sig_list)
    except VCDReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return data


# ---------------------------------------------------------------------------
# /api/run — シミュレーション実行（テスト判定なし、波形取得用）
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    sv_files: List[str]
    top: Optional[str] = None
    cycles: int = 100
    vcd_out: Optional[str] = None
    params: Dict[str, int] = {}
    half_period: int = 5
    reset_cycles: int = 10


@app.post("/api/run")
async def api_run(req: RunRequest):
    """シミュレーションを実行して VCD を書き出す（テスト判定なし）。"""
    from deltasim.gui.adapter import run, AdapterError

    try:
        result = run(
            req.sv_files,
            top=req.top,
            cycles=req.cycles,
            vcd_path=req.vcd_out,
            param_overrides=req.params,
            half_period=req.half_period,
            reset_cycles=req.reset_cycles,
        )
    except AdapterError as e:
        raise HTTPException(status_code=500, detail=e.to_dict())

    return result
