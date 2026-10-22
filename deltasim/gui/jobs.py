"""SSE ジョブマネージャ — テスト/シミュレーションを非同期で実行して進捗を SSE 配信する。

アーキテクチャ:
  1. クライアントが POST /api/test/start → job_id を受け取る
  2. クライアントが GET /api/test/{job_id}/events で SSE を購読
  3. テストはスレッドプールで実行し、on_progress コールバックで asyncio.Queue にイベントを積む
  4. SSE レスポンスはキューからイベントを読んで "data: {...}\\n\\n" 形式で流す
  5. キャンセルは POST /api/test/{job_id}/cancel → threading.Event をセット
     → adapter.py が CancelToken を通じて deltasim.api に mid-cycle キャンセルを通知

イベントフィールド:
  - GUI SSE は "event" フィールドを使う
  - deltasim.api は "type" フィールドを使う
  - 変換は adapter._progress_to_sse() が行う

一時 VCD（§18.4）:
  vcd_path が指定されず spec に sim.vcd も無い場合、OS 一時ディレクトリに VCD を自動生成する。
  古い一時 VCD は次の実行開始時に削除する（Windows のファイルロックに備え PermissionError を無視）。
  サーバー終了時は cleanup_all_temp_vcds() を呼んですべて削除を試みる。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 一時 VCD 管理（§18.4）
# ---------------------------------------------------------------------------

_temp_vcds: List[str] = []
_temp_vcds_lock = threading.Lock()


def _peek_spec_vcd(spec_path: str) -> Optional[str]:
    """YAML スペックの sim.vcd を返す（存在しなければ None）。読み込み失敗は無視。"""
    try:
        import yaml as _yaml
        with open(spec_path, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        v = data.get("sim", {}).get("vcd")
        return str(v) if v else None
    except Exception:
        return None


def _make_temp_vcd() -> str:
    """OS 一時ディレクトリに空の VCD ファイルを作成し、パスを返す。"""
    fd, path = tempfile.mkstemp(suffix=".vcd", prefix="deltasim_gui_")
    os.close(fd)
    with _temp_vcds_lock:
        _temp_vcds.append(path)
    return path


def _cleanup_old_temp_vcds(keep_last: int = 5) -> None:
    """古い一時 VCD ファイルを削除する（Windows ファイルロックを無視）。"""
    with _temp_vcds_lock:
        if keep_last <= 0:
            to_delete = list(_temp_vcds)
            _temp_vcds.clear()
        else:
            to_delete = _temp_vcds[:-keep_last]
            del _temp_vcds[:-keep_last]
    for path in to_delete:
        try:
            Path(path).unlink(missing_ok=True)
        except (PermissionError, OSError):
            pass


def cleanup_all_temp_vcds() -> None:
    """すべての一時 VCD を削除する（サーバー終了時に呼ぶ）。"""
    _cleanup_old_temp_vcds(keep_last=0)


# ---------------------------------------------------------------------------
# ジョブ状態
# ---------------------------------------------------------------------------

@dataclass
class Job:
    job_id: str
    queue: asyncio.Queue
    cancel_event: threading.Event = field(default_factory=threading.Event)
    task: Optional[asyncio.Task] = None
    result: Optional[dict] = None
    done: bool = False
    loop: Optional[asyncio.AbstractEventLoop] = None


# ---------------------------------------------------------------------------
# グローバルジョブレジストリ
# ---------------------------------------------------------------------------

_jobs: Dict[str, Job] = {}
_lock = threading.Lock()


def create_job() -> Job:
    job_id = str(uuid.uuid4())
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    job = Job(job_id=job_id, queue=asyncio.Queue(), loop=loop)
    with _lock:
        _jobs[job_id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def cancel_job(job_id: str) -> bool:
    """キャンセルリクエストを送る。True = 見つかった。"""
    job = get_job(job_id)
    if job is None:
        return False
    job.cancel_event.set()
    if job.task and not job.task.done():
        job.task.cancel()
    return True


def cleanup_job(job_id: str) -> None:
    with _lock:
        _jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# SSE ストリーム生成
# ---------------------------------------------------------------------------

async def event_stream(job: Job, timeout_sec: float = 300.0):
    """SSE イベントストリームのジェネレータ。

    Yields: "data: {...}\\n\\n" 形式の文字列
    """
    while True:
        try:
            event = await asyncio.wait_for(job.queue.get(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"
            continue
        except asyncio.CancelledError:
            break

        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        evt_type = event.get("event") or event.get("type")
        if evt_type in ("finish", "error"):
            break


# ---------------------------------------------------------------------------
# 進捗コールバック生成
# ---------------------------------------------------------------------------

def make_progress_callback(job: Job) -> Callable[[dict], None]:
    """スレッド内から呼ばれる進捗コールバック（すでに event フィールドに変換済み）。"""
    def callback(event: dict) -> None:
        if job.loop and job.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                job.queue.put(event),
                job.loop,
            )
    return callback


# ---------------------------------------------------------------------------
# テストジョブ実行（§18.4: 一時 VCD 自動生成）
# ---------------------------------------------------------------------------

async def run_test_job(
    job: Job,
    sv_files: list,
    spec: str,
    *,
    top: Optional[str] = None,
    seed: Optional[int] = None,
    vcd_path: Optional[str] = None,
    params: Optional[dict] = None,
    filter_cases: Optional[list] = None,
) -> None:
    """テストジョブを非同期に実行する。

    vcd_path が None かつ spec に sim.vcd が無い場合、OS 一時ディレクトリに
    VCD を自動生成して波形ビューアに渡す（§18.4 の要件）。
    """
    from deltasim.gui.adapter import test as adapter_test

    # §18.4: 一時 VCD の自動生成
    if vcd_path is None:
        spec_vcd = _peek_spec_vcd(spec)
        if spec_vcd is None:
            _cleanup_old_temp_vcds(keep_last=5)
            vcd_path = _make_temp_vcd()

    progress_cb = make_progress_callback(job)

    def _run_in_thread():
        return adapter_test(
            sv_files, spec,
            top=top, seed=seed, vcd_path=vcd_path,
            param_overrides=params or {},
            on_progress=progress_cb,
            cancel_event=job.cancel_event,
            filter_cases=filter_cases,
        )

    try:
        result = await asyncio.to_thread(_run_in_thread)
        job.result = result
    except Exception as e:
        job.result = {"error": str(e)}
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True


# ---------------------------------------------------------------------------
# シミュレーションジョブ実行（§18.4: 一時 VCD 自動生成）
# ---------------------------------------------------------------------------

async def run_sim_job(
    job: Job,
    sv_files: list,
    *,
    top: Optional[str] = None,
    cycles: int = 100,
    vcd_path: Optional[str] = None,
    params: Optional[dict] = None,
    half_period: int = 5,
    reset_cycles: int = 10,
    keep_changes: bool = False,
) -> None:
    """シミュレーションジョブを非同期に実行する。

    vcd_path が None の場合、OS 一時ディレクトリに VCD を自動生成する（§18.4）。
    """
    from deltasim.gui.adapter import run as adapter_run

    # §18.4: 一時 VCD の自動生成
    if vcd_path is None:
        _cleanup_old_temp_vcds(keep_last=5)
        vcd_path = _make_temp_vcd()

    progress_cb = make_progress_callback(job)

    def _run_in_thread():
        return adapter_run(
            sv_files,
            top=top, cycles=cycles, vcd_path=vcd_path,
            param_overrides=params or {},
            half_period=half_period, reset_cycles=reset_cycles,
            on_progress=progress_cb, keep_changes=keep_changes,
        )

    try:
        result = await asyncio.to_thread(_run_in_thread)
        job.result = result
        await job.queue.put({"event": "finish", "result": {
            k: v for k, v in result.items() if k != "_run_result"
        }})
    except Exception as e:
        job.result = {"error": str(e)}
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True
