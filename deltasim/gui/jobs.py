"""SSE ジョブマネージャ — テスト/シミュレーションを非同期で実行して進捗を SSE 配信する。

アーキテクチャ:
  1. クライアントが POST /api/test/start → job_id を受け取る
  2. クライアントが GET /api/test/{job_id}/events で SSE を購読
  3. テストはスレッドプールで実行し、進捗コールバックで asyncio.Queue にイベントを積む
  4. SSE レスポンスはキューからイベントを読んで "data: {...}\\n\\n" 形式で流す
  5. キャンセルは POST /api/test/{job_id}/cancel → threading.Event をセット

キャンセル限界（M5 待ち）:
  現状の TestRunner は単一の同期ループで動くため、シミュレーション途中での
  真の中断はできない。threading.Event をチェックするのは各サイクル境界のみで、
  シミュレーション開始前と完了後のイミュ時刻ポイントで止まる。
  真の mid-cycle キャンセルは M5 の公開 API (cancel_token) が入ったら差し替える。
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


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
    loop = asyncio.get_event_loop()
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
            # keepalive ping
            yield ": keepalive\n\n"
            continue
        except asyncio.CancelledError:
            break

        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        if event.get("event") in ("finish", "error"):
            break


# ---------------------------------------------------------------------------
# 進捗コールバック生成
# ---------------------------------------------------------------------------

def make_progress_callback(job: Job) -> Callable[[dict], None]:
    """スレッド内から呼ばれる進捗コールバックを生成する。"""
    def callback(event: dict) -> None:
        if job.loop and job.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                job.queue.put(event),
                job.loop,
            )
    return callback


# ---------------------------------------------------------------------------
# ジョブ実行ラッパー
# ---------------------------------------------------------------------------

async def run_test_job(
    job: Job,
    sv_files: list[str],
    spec: str,
    *,
    top: Optional[str] = None,
    seed: Optional[int] = None,
    vcd_path: Optional[str] = None,
    params: Optional[dict] = None,
) -> None:
    """テストジョブを非同期に実行する。"""
    from deltasim.gui.adapter import test as adapter_test

    progress_cb = make_progress_callback(job)

    def _run_in_thread():
        return adapter_test(
            sv_files,
            spec,
            top=top,
            seed=seed,
            vcd_path=vcd_path,
            param_overrides=params or {},
            on_progress=progress_cb,
            cancel_event=job.cancel_event,
        )

    try:
        result = await asyncio.to_thread(_run_in_thread)
        job.result = result
    except Exception as e:
        job.result = {"error": str(e)}
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True


async def run_sim_job(
    job: Job,
    sv_files: list[str],
    *,
    top: Optional[str] = None,
    cycles: int = 100,
    vcd_path: Optional[str] = None,
    params: Optional[dict] = None,
    half_period: int = 5,
    reset_cycles: int = 10,
) -> None:
    """シミュレーションジョブを非同期に実行する。"""
    from deltasim.gui.adapter import run as adapter_run

    progress_cb = make_progress_callback(job)

    def _run_in_thread():
        return adapter_run(
            sv_files,
            top=top,
            cycles=cycles,
            vcd_path=vcd_path,
            param_overrides=params or {},
            half_period=half_period,
            reset_cycles=reset_cycles,
            on_progress=progress_cb,
        )

    try:
        result = await asyncio.to_thread(_run_in_thread)
        job.result = result
        await job.queue.put({"event": "finish", "result": result})
    except Exception as e:
        job.result = {"error": str(e)}
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True
