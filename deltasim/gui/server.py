"""deltasim GUI サーバ — FastAPI による REST API + SSE + 静的ファイル配信

エンドポイント一覧:
  GET  /                          → index.html
  GET  /health                    → {"ok": true}

  # ファイルブラウザ（127.0.0.1 限定・許可ルート外拒否）
  GET  /api/browse                → ディレクトリ一覧
  GET  /api/browse/roots          → 許可ルート一覧
  GET  /api/file/read             → ファイル内容取得
  POST /api/file/write            → ファイル書き込み（同じ制限）

  # プロジェクト管理
  GET  /api/project               → プロジェクトファイル読み込み
  POST /api/project               → プロジェクトファイル保存

  # RTL ロード
  POST /api/load                  → RTL ファイルをパース・エラボレーション
  GET  /api/examples              → examples/ フォルダのサンプル一覧

  # テスト実行（SSE + REST）
  POST /api/test/start            → テストジョブ開始 → job_id
  GET  /api/test/{job_id}/events  → SSE 進捗ストリーム
  POST /api/test/{job_id}/cancel  → テストキャンセル
  GET  /api/test/{job_id}/result  → ジョブ結果取得

  # シミュレーション実行（非同期）
  POST /api/run/start             → シミュジョブ開始 → job_id
  GET  /api/run/{job_id}/events   → SSE 進捗ストリーム
  POST /api/run/{job_id}/cancel   → キャンセル

  # 波形
  GET  /api/waveform/index        → 信号定義のみ取得（高速）
  GET  /api/waveform/range        → 指定信号・時間範囲の値変化取得
  GET  /api/waveform              → 後方互換（全データ）

セキュリティ:
  - 127.0.0.1 専用バインド（uvicorn レベルで制御）
  - ファイル操作は filesec.safe_path() で許可ルート外を拒否
  - パストラバーサル・シンボリックリンク脱出防止
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# ライフサイクル: サーバー終了時に一時 VCD をクリーンアップ（§18.4）
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(application: FastAPI):
    # 起動時: 前回の異常終了で残った一時 VCD を削除（§18.4.1）
    from deltasim.gui.jobs import startup_cleanup_stale_vcds, cleanup_all_temp_vcds
    await asyncio.to_thread(startup_cleanup_stale_vcds)
    yield
    # 終了時: 自プロセスの全一時 VCD を削除（§18.4.1）
    cleanup_all_temp_vcds()


# ---------------------------------------------------------------------------
# アプリ初期化
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"

try:
    from deltasim import __version__ as _deltasim_version
except ImportError:
    _deltasim_version = "0.0.0"

app = FastAPI(
    title="deltasim GUI",
    description="SystemVerilog シミュレータ GUI",
    version=_deltasim_version,
    lifespan=_lifespan,
)

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# ミドルウェア: localhost 限定チェック（§17.2: 常時有効・環境変数による無効化なし）
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _parse_host_header(host_header: str) -> str:
    """Host ヘッダからホスト部分を取り出す（ポートと IPv6 ブラケットを除去）。

    例: "127.0.0.1:8765" -> "127.0.0.1"
        "[::1]:8765"     -> "::1"
        "[::1]"          -> "::1"
        "localhost"      -> "localhost"
    """
    h = host_header.strip()
    if h.startswith("["):
        end = h.find("]")
        return h[1:end] if end != -1 else h[1:]
    if ":" in h:
        return h.rsplit(":", 1)[0]
    return h


@app.middleware("http")
async def localhost_only(request: Request, call_next):
    """§17.2: Host ヘッダが 127.0.0.1/localhost/[::1] でなければ 403 を返す。"""
    host_header = request.headers.get("host", "")
    host_part = _parse_host_header(host_header)
    if host_part not in _LOOPBACK_HOSTS:
        return JSONResponse(
            status_code=403,
            content={"detail": "このサーバはローカルホストからのみアクセスできます"},
        )
    return await call_next(request)


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


@app.get("/api/initial-project")
async def initial_project():
    """CLI 引数から渡された初期プロジェクト情報を返す（環境変数経由）。"""
    import json
    raw = os.environ.get("DELTASIM_INITIAL_PROJECT")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# /api/browse — サーバ側ファイルブラウザ
# ---------------------------------------------------------------------------

@app.get("/api/browse/roots")
async def browse_roots():
    """許可されているルートディレクトリ一覧を返す。"""
    from deltasim.gui.filesec import get_allowed_roots
    return {"roots": [str(r) for r in get_allowed_roots()]}


@app.get("/api/browse")
async def browse(path: str = Query(..., description="ディレクトリパス")):
    """ディレクトリ内容を返す（許可ルート外は 403）。"""
    from deltasim.gui.filesec import list_dir, PathSecurityError

    try:
        entries = list_dir(path)
    except PathSecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (FileNotFoundError, NotADirectoryError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"path": path, "entries": entries}


# ---------------------------------------------------------------------------
# /api/file — ファイル読み書き
# ---------------------------------------------------------------------------

@app.get("/api/file/read")
async def file_read(path: str = Query(..., description="ファイルパス")):
    """ファイル内容を返す（許可ルート外は 403）。"""
    from deltasim.gui.filesec import read_text, PathSecurityError

    try:
        content = read_text(path)
    except PathSecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IsADirectoryError as e:
        raise HTTPException(status_code=422, detail=str(e))

    ext = Path(path).suffix.lower()
    lang = "verilog" if ext in (".sv", ".v") else "yaml" if ext in (".yaml", ".yml") else "text"
    return {"path": path, "content": content, "lang": lang}


class FileWriteRequest(BaseModel):
    path: str
    content: str


@app.post("/api/file/write")
async def file_write(req: FileWriteRequest):
    """ファイルを書き込む（許可ルート外は 403）。"""
    from deltasim.gui.filesec import write_text, PathSecurityError

    try:
        write_text(req.path, req.content)
    except PathSecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except IsADirectoryError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {"ok": True, "path": req.path}


# ---------------------------------------------------------------------------
# /api/project — プロジェクトファイル管理
# ---------------------------------------------------------------------------

@app.get("/api/project")
async def get_project(path: str = Query(..., description="プロジェクトファイルパス")):
    from deltasim.gui.project import load_project, project_to_dict
    from deltasim.gui.filesec import PathSecurityError, safe_path

    try:
        safe_path(path, must_exist=True)
        proj = load_project(path)
    except PathSecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    return project_to_dict(proj)


class ProjectSaveRequest(BaseModel):
    path: str
    sv_files: List[str] = []
    top: Optional[str] = None
    spec: Optional[str] = None
    vcd: Optional[str] = None
    params: Dict[str, int] = {}


@app.post("/api/project")
async def save_project(req: ProjectSaveRequest):
    from deltasim.gui.project import DeltaSimProject, save_project as _save
    from deltasim.gui.filesec import PathSecurityError, safe_path

    try:
        safe_path(Path(req.path).parent)
    except PathSecurityError as e:
        raise HTTPException(status_code=403, detail=str(e))

    proj = DeltaSimProject(
        sv_files=req.sv_files, top=req.top, spec=req.spec,
        vcd=req.vcd, params=req.params,
    )
    try:
        _save(proj, req.path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "path": req.path}


# ---------------------------------------------------------------------------
# /api/examples — サンプルプロジェクト一覧
# ---------------------------------------------------------------------------

@app.get("/api/spec/cases")
async def spec_cases(path: str = Query(..., description="YAML スペックファイルパス")):
    """YAML スペックからケース名一覧を返す（ケースフィルタ UI 用）。

    旧式（cases: なし）は stem 名の 1 件を返す。
    """
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"スペックファイルが見つかりません: {path!r}")

    from deltasim.gui.adapter import list_spec_cases
    names = await asyncio.to_thread(list_spec_cases, path)
    return {"path": path, "case_names": names}


@app.get("/api/examples")
async def get_examples():
    examples_dir = Path(__file__).parent.parent.parent / "examples"
    if not examples_dir.exists():
        return {"examples": []}

    import yaml as _yaml
    projects: list[dict] = []
    seen_tops: set[str] = set()

    for yaml_file in sorted(examples_dir.glob("*_spec.yaml")):
        sv_name = yaml_file.stem.replace("_spec", "") + ".sv"
        sv_file = examples_dir / sv_name
        if not sv_file.exists():
            continue

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
# /api/load — RTL ロード
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
    from deltasim.gui.adapter import load

    try:
        result = load(req.files, req.top, param_overrides=req.params)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result


# ---------------------------------------------------------------------------
# /api/test — テスト実行 (SSE)
# ---------------------------------------------------------------------------

class TestStartRequest(BaseModel):
    sv_files: List[str]
    spec: str
    top: Optional[str] = None
    seed: Optional[int] = None
    vcd_out: Optional[str] = None
    params: Dict[str, int] = {}
    filter_cases: Optional[List[str]] = None  # ケース名フィルタ（ワイルドカード可）

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


@app.post("/api/test/start")
async def test_start(req: TestStartRequest):
    """テストジョブを開始して job_id を返す。"""
    from deltasim.gui.jobs import create_job, run_test_job

    job = create_job()
    job.task = asyncio.create_task(
        run_test_job(
            job,
            req.sv_files,
            req.spec,
            top=req.top,
            seed=req.seed,
            vcd_path=req.vcd_out,
            params=req.params,
            filter_cases=req.filter_cases,
        )
    )
    return {"job_id": job.job_id}


@app.get("/api/test/{job_id}/events")
async def test_events(job_id: str):
    """SSE でテスト進捗イベントを流す。"""
    from deltasim.gui.jobs import get_job, event_stream

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")

    return StreamingResponse(
        event_stream(job),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/test/{job_id}/cancel")
async def test_cancel(job_id: str):
    """テストジョブをキャンセルする。"""
    from deltasim.gui.jobs import cancel_job

    if not cancel_job(job_id):
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")
    return {"ok": True, "job_id": job_id}


@app.get("/api/test/{job_id}/result")
async def test_result(job_id: str):
    """ジョブの結果を返す（SSE 終了後に取得）。"""
    from deltasim.gui.jobs import get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")
    if not job.done:
        return {"done": False}
    return {"done": True, "result": job.result}


# ---------------------------------------------------------------------------
# /api/test (後方互換: 同期版)
# ---------------------------------------------------------------------------

class TestRequest(BaseModel):
    sv_files: List[str]
    spec: str
    top: Optional[str] = None
    seed: Optional[int] = None
    vcd_out: Optional[str] = None
    params: Dict[str, int] = {}
    filter_cases: Optional[List[str]] = None

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
    """テストを同期実行して結果 JSON を返す（後方互換）。

    §18.4.1: vcd_out 未指定かつ spec に sim.vcd も無い場合、専用フォルダに一時 VCD を自動生成する。
    """
    from deltasim.gui.adapter import test
    from deltasim.gui.jobs import _peek_spec_vcd, _make_temp_vcd

    vcd_out = req.vcd_out
    if vcd_out is None:
        spec_vcd = _peek_spec_vcd(req.spec)
        if spec_vcd is None:
            vcd_out = _make_temp_vcd(spec=req.spec, sv_files=req.sv_files)

    try:
        result = await asyncio.to_thread(
            test,
            req.sv_files,
            req.spec,
            top=req.top,
            seed=req.seed,
            vcd_path=vcd_out,
            param_overrides=req.params,
            filter_cases=req.filter_cases,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    result.pop("_test_result", None)  # 同期版ではエクスポート不要

    return result


# ---------------------------------------------------------------------------
# /api/run — シミュレーション実行（非同期）
# ---------------------------------------------------------------------------

class RunStartRequest(BaseModel):
    sv_files: List[str]
    top: Optional[str] = None
    cycles: int = 100
    vcd_out: Optional[str] = None
    params: Dict[str, int] = {}
    half_period: int = 5
    reset_cycles: int = 10


@app.post("/api/run/start")
async def run_start(req: RunStartRequest):
    from deltasim.gui.jobs import create_job, run_sim_job

    job = create_job()
    job.task = asyncio.create_task(
        run_sim_job(
            job,
            req.sv_files,
            top=req.top,
            cycles=req.cycles,
            vcd_path=req.vcd_out,
            params=req.params,
            half_period=req.half_period,
            reset_cycles=req.reset_cycles,
        )
    )
    return {"job_id": job.job_id}


@app.get("/api/run/{job_id}/events")
async def run_events(job_id: str):
    from deltasim.gui.jobs import get_job, event_stream

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")

    return StreamingResponse(
        event_stream(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/run/{job_id}/cancel")
async def run_cancel(job_id: str):
    from deltasim.gui.jobs import cancel_job

    if not cancel_job(job_id):
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# /api/run 後方互換（同期版）
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
    from deltasim.gui.adapter import run

    try:
        result = await asyncio.to_thread(
            run,
            req.sv_files,
            top=req.top,
            cycles=req.cycles,
            vcd_path=req.vcd_out,
            param_overrides=req.params,
            half_period=req.half_period,
            reset_cycles=req.reset_cycles,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # _run_result は内部オブジェクトなので除外して返す
    return {k: v for k, v in result.items() if k != "_run_result"}


# ---------------------------------------------------------------------------
# /api/simulator — 対話デバッグ（Simulator API §8）
# ---------------------------------------------------------------------------

class SimCreateRequest(BaseModel):
    sv_files: List[str]
    top: Optional[str] = None
    params: Dict[str, int] = {}
    half_period: int = 5
    reset_cycles: int = 10


@app.post("/api/simulator")
async def sim_create(req: SimCreateRequest):
    """新しい Simulator セッションを作成する（§8）。"""
    from deltasim.gui.sim_session import create_session

    for f in req.sv_files:
        if not os.path.isfile(f):
            raise HTTPException(status_code=422, detail=f"SV ファイルが見つかりません: {f!r}")

    try:
        sess = await asyncio.to_thread(
            create_session,
            req.sv_files, req.top, req.params,
            req.half_period, req.reset_cycles,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "session_id": sess.session_id,
        "module": sess.sim._di.module,
        "signals": sess.sim.list_signals(),
        "time_ns": sess.sim.time_ns,
        "cycle": sess.sim.cycle,
    }


@app.get("/api/simulator")
async def sim_list():
    """アクティブな Simulator セッション一覧を返す。"""
    from deltasim.gui.sim_session import list_sessions
    return {"sessions": list_sessions()}


@app.delete("/api/simulator/{session_id}")
async def sim_delete(session_id: str):
    """Simulator セッションを削除する。"""
    from deltasim.gui.sim_session import delete_session
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")
    return {"ok": True}


class SimStepRequest(BaseModel):
    n: int = 1


@app.post("/api/simulator/{session_id}/step")
async def sim_step(session_id: str, req: SimStepRequest):
    """n サイクル進める（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        snap = await asyncio.to_thread(sess.sim.step, req.n)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return snap.to_dict()


class SimRunUntilRequest(BaseModel):
    time_ns: int


@app.post("/api/simulator/{session_id}/run_until")
async def sim_run_until(session_id: str, req: SimRunUntilRequest):
    """指定時刻まで実行する（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        snap = await asyncio.to_thread(sess.sim.run_until, req.time_ns)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return snap.to_dict()


@app.get("/api/simulator/{session_id}/signals")
async def sim_signals(session_id: str):
    """信号一覧を返す（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    return {
        "session_id": session_id,
        "time_ns": sess.sim.time_ns,
        "cycle": sess.sim.cycle,
        "signals": sess.sim.list_signals(),
    }


class SimSetRequest(BaseModel):
    signal: str
    value: int


@app.post("/api/simulator/{session_id}/set")
async def sim_set(session_id: str, req: SimSetRequest):
    """信号値を設定する（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        sess.sim.set(req.signal, req.value)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "signal": req.signal, "value": req.value}


@app.post("/api/simulator/{session_id}/force")
async def sim_force(session_id: str, req: SimSetRequest):
    """信号を強制固定する（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        sess.sim.force(req.signal, req.value)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "signal": req.signal, "value": req.value, "forced": True}


class SimReleaseRequest(BaseModel):
    signal: str


@app.post("/api/simulator/{session_id}/release")
async def sim_release(session_id: str, req: SimReleaseRequest):
    """force を解除する（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        sess.sim.release(req.signal)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "signal": req.signal, "forced": False}


@app.post("/api/simulator/{session_id}/reset")
async def sim_reset(session_id: str):
    """シミュレーションをリセットする（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        sess.sim.reset()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "time_ns": sess.sim.time_ns, "cycle": sess.sim.cycle}


class SimBreakpointRequest(BaseModel):
    condition: str


@app.post("/api/simulator/{session_id}/breakpoints")
async def sim_add_bp(session_id: str, req: SimBreakpointRequest):
    """ブレークポイントを追加する（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        bp_id = sess.sim.add_breakpoint(req.condition)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True, "id": bp_id, "condition": req.condition}


@app.get("/api/simulator/{session_id}/breakpoints")
async def sim_list_bp(session_id: str):
    """ブレークポイント一覧を返す（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    return {"breakpoints": sess.sim.list_breakpoints()}


@app.delete("/api/simulator/{session_id}/breakpoints/{bp_id}")
async def sim_del_bp(session_id: str, bp_id: int):
    """ブレークポイントを削除する（§8）。"""
    from deltasim.gui.sim_session import get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    try:
        sess.sim.remove_breakpoint(bp_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


@app.get("/api/simulator/{session_id}/waveform")
async def sim_waveform(
    session_id: str,
    signals: Optional[str] = Query(None, description="カンマ区切り信号名"),
    t_start: int = Query(0),
    t_end: Optional[int] = Query(None),
):
    """Simulator の in-memory 変化履歴から波形データを返す（§2.6 get_signal_changes 使用）。

    VCD ファイル不要のメモリルート。§12.3 の truncated_at に対応する警告も返す。
    """
    from deltasim.gui.sim_session import get_all_signal_changes, get_session

    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    data = await asyncio.to_thread(
        get_all_signal_changes, session_id, t_start, t_end
    )

    if signals:
        sig_set = set(s.strip() for s in signals.split(","))
        data["signals"] = {k: v for k, v in data["signals"].items() if k in sig_set}

    return data


# ---------------------------------------------------------------------------
# /api/waveform — VCD 波形データ
# ---------------------------------------------------------------------------

@app.get("/api/waveform/index")
async def waveform_index(path: str = Query(..., description="VCD ファイルパス")):
    """VCD の信号定義のみを高速取得する（値変化は読まない）。"""
    from deltasim.gui.vcd_reader import read_vcd_index, VCDReadError

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"VCD ファイルが見つかりません: {path!r}")

    try:
        data = await asyncio.to_thread(read_vcd_index, path)
    except VCDReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return data


@app.get("/api/waveform/range")
async def waveform_range(
    path: str = Query(..., description="VCD ファイルパス"),
    signals: Optional[str] = Query(None, description="カンマ区切りの信号名"),
    t_start: int = Query(0, description="フィルタ開始時刻 (ns)"),
    t_end: Optional[int] = Query(None, description="フィルタ終了時刻 (ns)"),
):
    """指定信号・時間範囲の値変化を返す（遅延読み込み用）。"""
    from deltasim.gui.vcd_reader import read_vcd_range, VCDReadError

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"VCD ファイルが見つかりません: {path!r}")

    sig_list = [s.strip() for s in signals.split(",")] if signals else None

    try:
        data = await asyncio.to_thread(
            read_vcd_range, path,
            signal_names=sig_list, t_start=t_start, t_end=t_end
        )
    except VCDReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return data


@app.get("/api/waveform")
async def api_waveform(
    path: str = Query(..., description="VCD ファイルパス"),
    t_start: int = Query(0, description="フィルタ開始時刻 (ns)"),
    t_end: Optional[int] = Query(None, description="フィルタ終了時刻 (ns)"),
    signals: Optional[str] = Query(None, description="カンマ区切りの信号名リスト"),
):
    """後方互換 API（全データ取得）。"""
    from deltasim.gui.vcd_reader import read_vcd, VCDReadError

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"VCD ファイルが見つかりません: {path!r}")

    sig_list = [s.strip() for s in signals.split(",")] if signals else None

    try:
        data = await asyncio.to_thread(
            read_vcd, path, t_start=t_start, t_end=t_end, signals=sig_list
        )
    except VCDReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return data

# ---------------------------------------------------------------------------
# /api/gen — テストベンチ・スペック生成（§18.4: 生成ボタン）
# ---------------------------------------------------------------------------

class GenRequest(BaseModel):
    sv_files: List[str]
    top: Optional[str] = None
    params: Dict[str, int] = {}
    out_dir: Optional[str] = None
    tb_path: Optional[str] = None
    spec_path: Optional[str] = None
    force: bool = False  # 既存ファイルを上書きする場合 True

    @field_validator("sv_files")
    @classmethod
    def sv_files_must_exist(cls, v: List[str]) -> List[str]:
        for f in v:
            if not os.path.isfile(f):
                raise ValueError(f"SV ファイルが見つかりません: {f!r}")
        return v


@app.post("/api/gen")
async def api_gen(req: GenRequest):
    """テストベンチ SV と YAML スペックのスケルトンを生成する（§18.4 A: 生成ボタン）。

    ファイルモード（out_dir / tb_path / spec_path のいずれかを指定）:
      - force=False（既定）: 既存ファイルがあれば 409 を返す。
        レスポンス: {"conflict": ["path1", "path2"], "detail": "..."}
      - force=True: 既存ファイルを上書きする。

    文字列モード（すべて省略）: ファイルを書き出さず tb_sv / spec_yaml 文字列を返す。
    """
    from deltasim.gui.adapter import gen
    from deltasim.gui.filesec import PathSecurityError, safe_path

    is_file_mode = any([req.out_dir, req.tb_path, req.spec_path])

    if is_file_mode and not req.force:
        # 書き出し先を事前に確認して 409 を返す
        import deltasim.api as _ds
        di = _ds.load([str(f) for f in req.sv_files], top=req.top, params=req.params)
        if di.status == "PASS":
            top_name = di.module
            out = Path(req.out_dir) if req.out_dir else Path(".")
            tb_out = Path(req.tb_path) if req.tb_path else out / f"{top_name}_tb.sv"
            spec_out = Path(req.spec_path) if req.spec_path else out / f"{top_name}_spec.yaml"
            conflicts = [str(p) for p in [tb_out, spec_out] if p.exists()]
            if conflicts:
                return JSONResponse(
                    status_code=409,
                    content={"conflict": conflicts, "detail": "ファイルが既に存在します。上書きするには force=true を指定してください。"},
                )

    try:
        result = await asyncio.to_thread(
            gen,
            req.sv_files,
            req.top,
            param_overrides=req.params,
            out_dir=req.out_dir,
            tb_path=req.tb_path,
            spec_path=req.spec_path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return result


# ---------------------------------------------------------------------------
# /api/test/{job_id}/export — JUnit XML / JSON エクスポート（§18.4）
# ---------------------------------------------------------------------------

@app.get("/api/test/{job_id}/export")
async def test_export(
    job_id: str,
    fmt: str = Query("json", description="'json' または 'junit'"),
):
    """テスト結果を JSON または JUnit XML としてダウンロードする（§18.4）。

    CLI の `test --json` / `test --junit` と同じ出力を返す。
    job.test_result（TestResult オブジェクト）を直接使い、dict からの再構築は行わない。
    """
    from deltasim.gui.jobs import get_job

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")
    if not job.done or job.result is None:
        raise HTTPException(status_code=425, detail="ジョブはまだ完了していません")

    test_result = job.test_result

    if fmt == "junit":
        import tempfile as _tempfile
        from deltasim.report.junit import write_junit_v2

        if test_result is None:
            raise HTTPException(status_code=422, detail="TestResult オブジェクトが保存されていません")

        fd, tmp_path_str = _tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        try:
            await asyncio.to_thread(write_junit_v2, test_result, tmp_path_str)
            xml_body = Path(tmp_path_str).read_text(encoding="utf-8")
        finally:
            try:
                Path(tmp_path_str).unlink(missing_ok=True)
            except (PermissionError, OSError):
                pass

        return Response(
            content=xml_body,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="result_{job_id[:8]}.xml"'},
        )
    else:
        # JSON: CLI の test --json と同じ構造（TestResult.to_json() ベース）
        if test_result is not None:
            body = test_result.to_json()
        else:
            import json as _json
            body = _json.dumps(job.result, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="result_{job_id[:8]}.json"'},
        )


# ---------------------------------------------------------------------------
# /api/recent-projects — 最近使ったプロジェクト（§18.4）
# ---------------------------------------------------------------------------

class RecentProjectEntry(BaseModel):
    sv_files: List[str]
    top: Optional[str] = None
    spec: Optional[str] = None
    name: Optional[str] = None


@app.get("/api/recent-projects")
async def get_recent_projects():
    """最近使ったプロジェクト一覧を返す（§18.4）。

    存在しないパスは missing=true で識別できるよう付加して返す。
    """
    from deltasim.gui.recent import load_recent

    entries = load_recent()
    enriched = []
    for e in entries:
        item = dict(e)
        sv_missing = [f for f in item.get("sv_files", []) if not os.path.exists(f)]
        spec_missing = item.get("spec") and not os.path.exists(item["spec"])
        item["missing"] = bool(sv_missing or spec_missing)
        item["missing_paths"] = sv_missing + ([item["spec"]] if spec_missing else [])
        enriched.append(item)
    return {"recent": enriched}


@app.post("/api/recent-projects/add")
async def add_recent_project(req: RecentProjectEntry):
    """プロジェクトを最近使った一覧に追加する（§18.4）。"""
    from deltasim.gui.recent import add_recent

    entry = {
        "sv_files": req.sv_files,
        "top": req.top,
        "spec": req.spec,
        "name": req.name or (req.top or (Path(req.sv_files[0]).stem if req.sv_files else "unknown")),
    }
    await asyncio.to_thread(add_recent, entry)
    return {"ok": True}

# ---------------------------------------------------------------------------
# /api/waveform/edge — エッジ検索（§18.4 B2）
# ---------------------------------------------------------------------------

@app.get("/api/waveform/edge")
async def waveform_edge(
    path: str = Query(..., description="VCD ファイルパス"),
    signal: str = Query(..., description="検索対象の信号名"),
    from_ns: int = Query(0, description="検索の基準時刻 (ns)"),
    direction: str = Query("next", description="'next' または 'prev'"),
    edge: str = Query("any", description="'any' / 'rising' / 'falling'"),
):
    """指定信号の次/前のエッジ時刻を返す（§18.4 B2 エッジ検索）。

    Returns: {"signal": "...", "time_ns": N, "found": true/false}
    """
    from deltasim.gui.vcd_reader import read_vcd_range, VCDReadError
    from deltasim.gui.waveutil import find_edge

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"VCD ファイルが見つかりません: {path!r}")

    try:
        data = await asyncio.to_thread(read_vcd_range, path, signal_names=[signal])
    except VCDReadError as e:
        raise HTTPException(status_code=422, detail=str(e))

    sig_data = data.get("signals", {}).get(signal)
    if sig_data is None:
        raise HTTPException(status_code=404, detail=f"信号が見つかりません: {signal!r}")

    changes = sig_data.get("changes", [])  # [[t, v], ...]
    width = sig_data.get("width", 1)
    result_ns = find_edge(changes, from_ns, direction, edge, width)

    return {"signal": signal, "time_ns": result_ns, "found": result_ns is not None}


# ---------------------------------------------------------------------------
# /api/waveform/decode — 値デコード（§18.4 B3: 符号付き変換のサーバー側）
# ---------------------------------------------------------------------------

@app.get("/api/waveform/decode")
async def waveform_decode(
    value: str = Query(..., description="バイナリ文字列"),
    width: int = Query(..., description="ビット幅"),
    radix: str = Query("hex", description="'bin'/'hex'/'unsigned'/'signed'"),
):
    """バイナリ文字列を指定基数の表示文字列に変換する（テスト用・符号付き変換）。"""
    from deltasim.gui.waveutil import fmt_value
    if radix not in ("bin", "hex", "unsigned", "signed"):
        raise HTTPException(status_code=422, detail=f"不正な radix: {radix!r}")
    result = fmt_value(value, width, radix)
    return {"value": value, "width": width, "radix": radix, "result": result}


# ---------------------------------------------------------------------------
# /api/waveset — 波形表示セット保存・読込（§18.4 B4）
# ---------------------------------------------------------------------------

class WavesetEntry(BaseModel):
    sv_files: List[str]
    spec: Optional[str] = None
    name: str
    signals: List[str]
    radix: Dict[str, str] = {}


class WavesetDeleteRequest(BaseModel):
    sv_files: List[str]
    spec: Optional[str] = None
    name: str


@app.get("/api/waveset")
async def get_waveset(
    sv_files: str = Query(..., description="SV ファイルパスのカンマ区切り"),
    spec: Optional[str] = Query(None, description="スペックファイルパス"),
):
    """プロジェクトの波形表示セット一覧を返す（§18.4 B4）。"""
    from deltasim.gui.waveset import load_wavesets
    files = [f.strip() for f in sv_files.split(",") if f.strip()]
    sets = await asyncio.to_thread(load_wavesets, files, spec)
    return {"sets": sets}


@app.post("/api/waveset")
async def save_waveset_endpoint(req: WavesetEntry):
    """波形表示セットを保存する（同名上書き）（§18.4 B4）。"""
    from deltasim.gui.waveset import save_waveset
    await asyncio.to_thread(
        save_waveset,
        req.sv_files, req.spec, req.name, req.signals, req.radix,
    )
    return {"ok": True, "name": req.name}


@app.delete("/api/waveset")
async def delete_waveset_endpoint(req: WavesetDeleteRequest):
    """波形表示セットを名前で削除する（§18.4 B4）。"""
    from deltasim.gui.waveset import delete_waveset
    deleted = await asyncio.to_thread(
        delete_waveset, req.sv_files, req.spec, req.name,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"セットが見つかりません: {req.name!r}")
    return {"ok": True, "name": req.name}

# ---------------------------------------------------------------------------
# /api/console — GUI コンソール入力欄（§18.4）
# ---------------------------------------------------------------------------

class ConsoleExecRequest(BaseModel):
    command: str


@app.post("/api/console/{session_id}/exec/start")
async def console_exec_start(session_id: str, req: ConsoleExecRequest):
    """コンソールコマンドを非同期実行開始して job_id を返す（§18.4）。

    - 実行できるのは deltasim の対話コマンドのみ
    - do <file> のパスは filesec で検証
    - quit/exit はサーバーを止めず job の quit=True フラグとして返す
    """
    from deltasim.gui.sim_session import get_session as get_sim_session
    from deltasim.gui.console_session import get_or_create as get_console
    from deltasim.gui.jobs import create_job, run_console_job

    sess = get_sim_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    # do <file> のパス検証（filesec で安全性確認）
    cmd = req.command.strip()
    if cmd.lower().startswith("do "):
        raw_path = cmd[3:].strip().strip('"').strip("'")
        from deltasim.gui.filesec import safe_path, PathSecurityError
        try:
            safe_path(raw_path, must_exist=True)
        except PathSecurityError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {raw_path!r}")

    interp = get_console(session_id, sess.sim)
    job = create_job()
    job.task = asyncio.create_task(run_console_job(job, interp, cmd))
    return {"job_id": job.job_id, "session_id": session_id}


@app.get("/api/console/{session_id}/exec/{job_id}/events")
async def console_exec_events(session_id: str, job_id: str):
    """コンソールコマンドの進捗を SSE で配信する（§18.4）。"""
    from deltasim.gui.jobs import get_job, event_stream

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")
    return StreamingResponse(
        event_stream(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/console/{session_id}/exec/{job_id}/cancel")
async def console_exec_cancel(session_id: str, job_id: str):
    """実行中のコンソールコマンドをキャンセルする（§18.4）。"""
    from deltasim.gui.jobs import get_job
    from deltasim.gui.console_session import get as get_console

    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"ジョブが見つかりません: {job_id}")

    # インタプリタに中断を要求
    interp = get_console(session_id)
    if interp is not None and hasattr(interp, "request_cancel"):
        interp.request_cancel()

    job.cancel_event.set()
    if job.task and not job.task.done():
        job.task.cancel()
    return {"ok": True, "job_id": job_id}


@app.get("/api/console/{session_id}/state")
async def console_state(session_id: str):
    """コンソールセッションの現在状態を返す（§18.4）。"""
    from deltasim.gui.sim_session import get_session as get_sim_session
    from deltasim.gui.console_session import get as get_console

    sess = get_sim_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"セッションが見つかりません: {session_id}")

    interp = get_console(session_id)
    if interp is None:
        return {"session_id": session_id, "time_ns": 0, "cycle": 0, "breakpoints": [], "whens": []}

    engine = getattr(interp._sim, "_engine", None)
    time_ns = engine._time if engine else 0
    cycle = getattr(interp._sim, "cycle", 0)

    return {
        "session_id": session_id,
        "time_ns": time_ns,
        "cycle": cycle,
        "breakpoints": [
            {"id": b.id, "label": b.label(), "hit_count": b.hit_count}
            for b in getattr(interp, "_breakpoints", [])
        ],
        "whens": [
            {"id": b.id, "label": b.label(), "hit_count": b.hit_count}
            for b in getattr(interp, "_whens", [])
        ],
    }
