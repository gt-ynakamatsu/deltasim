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

一時 VCD（§18.4.1）:
  専用フォルダ: <tmpdir>/deltasim-gui/proc-{PID}/{proj_hash}/{timestamp}.vcd
  - プロジェクト（spec + sv_files のハッシュ）ごとに最大 _MAX_PER_PROJECT 件保持
  - サーバー起動時: 死亡プロセスのサブフォルダを削除
  - サーバー終了時: 自プロセスのサブフォルダを全削除
  - 削除は deltasim-gui/ 配下に限定し、それ以外には絶対に触らない
  - 複数の GUI プロセスが同時起動しても pid ごとに分離される
  - Windows のファイルロックで落ちないよう PermissionError/OSError を無視する
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 一時 VCD 管理（§18.4.1）
# ---------------------------------------------------------------------------

_DELTASIM_GUI_BASE = Path(tempfile.gettempdir()) / "deltasim-gui"
_OUR_PID = os.getpid()
_OUR_PROC_DIR: Optional[Path] = None
_PROC_DIR_LOCK = threading.Lock()
_MAX_PER_PROJECT = 5


def _is_within_base(path: Path, base: Path) -> bool:
    """パスが base 配下にあるか確認する（安全ガード）。"""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_rmtree(path: Path, base: Optional[Path] = None) -> None:
    """専用フォルダ配下のディレクトリを再帰削除（Windows ロックを無視）。"""
    b = base if base is not None else _DELTASIM_GUI_BASE
    if not _is_within_base(path, b):
        return
    try:
        shutil.rmtree(str(path), ignore_errors=True)
    except Exception:
        pass


def _safe_unlink(path: Path, base: Optional[Path] = None) -> None:
    """専用フォルダ配下のファイルを削除（Windows ロックを無視）。"""
    b = base if base is not None else _DELTASIM_GUI_BASE
    if not _is_within_base(path, b):
        return
    try:
        path.unlink(missing_ok=True)
    except (PermissionError, OSError):
        pass


def _is_pid_running(pid: int) -> bool:
    """プロセスが生存しているか確認する（Windows/Linux 両対応）。"""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # プロセスは存在するがシグナルを送る権限がない
    except OSError:
        return False


def _get_our_proc_dir(base: Optional[Path] = None) -> Path:
    """自プロセスの作業ディレクトリを返す（なければ作成）。

    base が指定された場合はキャッシュを使わず base 配下に作成する（テスト用）。
    """
    global _OUR_PROC_DIR
    if base is not None:
        d = base / f"proc-{_OUR_PID}"
        d.mkdir(parents=True, exist_ok=True)
        return d
    with _PROC_DIR_LOCK:
        if _OUR_PROC_DIR is None:
            d = _DELTASIM_GUI_BASE / f"proc-{_OUR_PID}"
            d.mkdir(parents=True, exist_ok=True)
            _OUR_PROC_DIR = d
        return _OUR_PROC_DIR


def _project_hash(spec: Optional[str], sv_files: Optional[List[str]]) -> str:
    """spec + sv_files の組からプロジェクト識別ハッシュを生成する。"""
    key = (spec or "") + "|" + "|".join(sorted(sv_files or []))
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


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


def _make_temp_vcd(
    spec: Optional[str] = None,
    sv_files: Optional[List[str]] = None,
    base: Optional[Path] = None,
) -> str:
    """一時 VCD ファイルを作成し、プロジェクト単位で最大 _MAX_PER_PROJECT 件に整理する。

    § 18.4.1 の要件:
    - 専用フォルダ配下のプロジェクトハッシュサブフォルダに作成
    - そのプロジェクトで古い VCD を削除して最大 5 件に保つ
    - Windows ファイルロックでも落ちない
    """
    proc_dir = _get_our_proc_dir(base)
    proj_key = _project_hash(spec, sv_files)
    proj_dir = proc_dir / proj_key
    proj_dir.mkdir(parents=True, exist_ok=True)
    b = base if base is not None else _DELTASIM_GUI_BASE

    with _PROC_DIR_LOCK:
        existing = sorted(
            [f for f in proj_dir.iterdir() if f.suffix == ".vcd"],
            key=lambda p: p.stat().st_mtime,
        )
        for old in existing[: max(0, len(existing) - (_MAX_PER_PROJECT - 1))]:
            _safe_unlink(old, b)

        ts = int(time.monotonic() * 1_000_000)
        path = proj_dir / f"{ts}.vcd"
        path.touch()

    return str(path)


def startup_cleanup_stale_vcds(base: Optional[Path] = None) -> None:
    """起動時に前回の異常終了で残った一時 VCD を削除する（§18.4.1）。

    base 配下の proc-{PID} ディレクトリを調べ、死亡した PID のものを削除する。
    削除は base 配下のみに限定し、他には絶対に触らない。
    """
    b = base if base is not None else _DELTASIM_GUI_BASE
    if not b.exists():
        return
    try:
        children = list(b.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir():
            continue
        if not child.name.startswith("proc-"):
            continue
        try:
            pid = int(child.name[5:])
        except ValueError:
            continue
        if pid == _OUR_PID:
            continue
        if not _is_pid_running(pid):
            _safe_rmtree(child, b)


def cleanup_all_temp_vcds(base: Optional[Path] = None) -> None:
    """サーバー終了時に自プロセスの全一時 VCD を削除する（§18.4.1）。"""
    b = base if base is not None else _DELTASIM_GUI_BASE
    if base is not None:
        proc_dir = base / f"proc-{_OUR_PID}"
        _safe_rmtree(proc_dir, b)
        return
    if _OUR_PROC_DIR is not None and _OUR_PROC_DIR.exists():
        _safe_rmtree(_OUR_PROC_DIR, b)


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
    test_result: Optional[object] = None   # TestResult オブジェクト（エクスポート用）
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
# テストジョブ実行（§18.4.1: 専用フォルダへの一時 VCD 自動生成）
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

    vcd_path が None かつ spec に sim.vcd が無い場合、専用フォルダに
    VCD を自動生成して波形ビューアに渡す（§18.4.1 の要件）。
    TestResult オブジェクトは job.test_result に保持し、エクスポートに使う。
    """
    from deltasim.gui.adapter import test as adapter_test

    # §18.4.1: 一時 VCD の自動生成（専用フォルダ・プロジェクト単位）
    if vcd_path is None:
        spec_vcd = _peek_spec_vcd(spec)
        if spec_vcd is None:
            vcd_path = _make_temp_vcd(spec=spec, sv_files=list(sv_files))

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
        result_dict = await asyncio.to_thread(_run_in_thread)
        # TestResult オブジェクトをジョブに保持（エクスポート用）
        job.test_result = result_dict.pop("_test_result", None)
        job.result = result_dict
    except Exception as e:
        job.result = {"error": str(e)}
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True


# ---------------------------------------------------------------------------
# シミュレーションジョブ実行（§18.4.1: 専用フォルダへの一時 VCD 自動生成）
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

    vcd_path が None の場合、専用フォルダに VCD を自動生成する（§18.4.1）。
    """
    from deltasim.gui.adapter import run as adapter_run

    # §18.4.1: 一時 VCD の自動生成（専用フォルダ・プロジェクト単位）
    if vcd_path is None:
        vcd_path = _make_temp_vcd(sv_files=list(sv_files))

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
