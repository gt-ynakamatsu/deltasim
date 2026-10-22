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
    """プロセスが生存しているか確認する（Windows/Linux 両対応）。

    警告: Windows では os.kill(pid, 0) は TerminateProcess(pid, 0) になり
    プロセスを強制終了してしまうため、Windows では ctypes で OpenProcess を
    使う。POSIX では os.kill(pid, 0) を使う（シグナルは送信されない）。
    """
    if os.name == "nt":
        return _is_pid_running_windows(pid)
    return _is_pid_running_posix(pid)


def _is_pid_running_posix(pid: int) -> bool:
    """POSIX 用: os.kill(pid, 0) でプロセス存在を確認する。
    
    os.kill(pid, 0) は POSIX ではシグナルを送らず存在確認のみ行う。
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # プロセスは存在するがシグナルを送る権限がない
    except OSError:
        return False


def _is_pid_running_windows(pid: int) -> bool:
    """Windows 用: ctypes の OpenProcess / GetExitCodeProcess でプロセス存在を確認する。

    os.kill(pid, 0) は Windows では TerminateProcess(pid, 0) になり
    無関係なプロセスを強制終了する危険があるため、使ってはならない。

    手順:
      1. OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid) でハンドルを取得
         - ハンドルが取れない場合:
           - ERROR_ACCESS_DENIED (5): プロセスは存在するが権限なし → 生存扱い
           - それ以外 (ERROR_INVALID_PARAMETER 等): プロセスは存在しない → 死亡
      2. ハンドルが取れた場合:
         - GetExitCodeProcess で終了コードを取得
         - STILL_ACTIVE (259) なら生存、それ以外は終了済み
         - CloseHandle でハンドルを閉じる
    """
    import ctypes
    import ctypes.wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = kernel32.GetLastError()
        if err == ERROR_ACCESS_DENIED:
            return True   # プロセスは存在するが権限がない
        return False      # プロセスが存在しない（ERROR_INVALID_PARAMETER 等）

    try:
        exit_code = ctypes.wintypes.DWORD()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == STILL_ACTIVE
        return False
    finally:
        kernel32.CloseHandle(handle)


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


# ---------------------------------------------------------------------------
# コンソールジョブ実行（§18.4 GUI コンソール）
# ---------------------------------------------------------------------------

async def run_console_job(
    job: Job,
    interp,          # interp.SimSession
    command: str,
) -> None:
    """Questa 風コマンドをコンソールセッション経由で非同期実行する。

    出力はリアルタイムで SSE に流す（_output_callback を使用）。
    quit/exit シグナルはジョブの quit=True フラグとして返し、サーバーは止めない。
    run -all など長時間コマンドは cancel_event / request_cancel() で中断可能。
    """
    from deltasim.interp.session import SimSession as InterpSession

    collected_output: List[str] = []

    def _on_output(msg: str) -> None:
        collected_output.append(msg)
        if job.loop and job.loop.is_running():
            asyncio.run_coroutine_threadsafe(
                job.queue.put({"event": "output", "lines": [msg]}),
                job.loop,
            )

    # キャンセルイベントを監視してインタプリタに伝える
    def _cancel_watcher():
        job.cancel_event.wait()
        if hasattr(interp, "request_cancel"):
            interp.request_cancel()
    threading.Thread(target=_cancel_watcher, daemon=True).start()

    def _run_in_thread():
        old_callback = interp._output_callback
        interp._output_callback = _on_output
        interp._output_lines.clear()
        quit_signal = False
        try:
            interp._tcl.eval(command)
        except InterpSession._QuitSignal:
            quit_signal = True
        except Exception as e:
            interp._out(f"ERROR: {e}")
        finally:
            interp._output_callback = old_callback

        engine = getattr(interp._sim, "_engine", None)
        time_ns = engine._time if engine else 0
        cycle = getattr(interp._sim, "cycle", 0)
        paused_bp = interp._paused_bp
        paused_info = None
        if paused_bp is not None:
            paused_info = {
                "id": paused_bp.id,
                "label": paused_bp.label(),
                "time_ns": time_ns,
            }
            interp._paused_bp = None  # 消費

        return {
            "quit": quit_signal,
            "output": list(interp._output_lines),
            "time_ns": time_ns,
            "cycle": cycle,
            "paused_bp": paused_info,
        }

    try:
        result = await asyncio.to_thread(_run_in_thread)
        job.result = result
        evt = {
            "event": "finish",
            "time_ns": result["time_ns"],
            "cycle": result["cycle"],
            "output": collected_output,
            "paused_bp": result["paused_bp"],
            "quit": result["quit"],
        }
        await job.queue.put(evt)
    except asyncio.CancelledError:
        # タスクがキャンセルされた場合、SSE ストリームを閉じるために finish イベントを送る
        engine = getattr(interp._sim, "_engine", None)
        time_ns = engine._time if engine else 0
        try:
            job.queue.put_nowait({
                "event": "finish",
                "time_ns": time_ns,
                "cycle": getattr(interp._sim, "cycle", 0),
                "output": collected_output,
                "paused_bp": None,
                "quit": False,
                "cancelled": True,
            })
        except Exception:
            pass
        raise
# LLM ジョブ（§18.3 GUI LLM ボタン）
# ---------------------------------------------------------------------------

async def run_llm_suggest_job(
    job: Job,
    text: str,
    sv_files: Optional[List[str]] = None,
    spec_file: Optional[str] = None,
    cfg=None,
    llm_fn=None,
    module_info: Optional[dict] = None,
    project_dir: Optional[str] = None,
) -> None:
    """コマンド提案ジョブ（suggest_command を非同期実行）。

    §18.3.2: ask には RTL の中身を渡さない。
    渡すのはファイル名・モジュール名・ポート一覧まで（パーサ結果から）。
    """
    from deltasim.gui.llm_session import store_suggestion

    # §18.3.2: ファイル名 + モジュール名 + ポート一覧のみ（RTL 中身なし）
    context = ""
    if sv_files:
        context += f"RTL ファイル: {', '.join(sv_files)}\n"
    if module_info:
        mod_name = module_info.get("module", "")
        if mod_name:
            context += f"モジュール名: {mod_name}\n"
        ports = module_info.get("ports", [])
        if ports:
            port_lines = [
                f"  {p.get('name', '')} ({p.get('direction', '')}, {p.get('width', 1)}bit)"
                for p in ports[:30]
            ]
            context += "ポート一覧:\n" + "\n".join(port_lines) + "\n"
    if spec_file:
        context += f"スペック: {spec_file}\n"

    def _suggest():
        from deltasim.llm import suggest_command, load_llm_config
        effective_cfg = cfg if cfg is not None else load_llm_config()
        if effective_cfg is None:
            raise RuntimeError("LLM が未設定です（E_LLM_NOT_CONFIGURED）")
        return suggest_command(text, cfg=effective_cfg, llm_fn=llm_fn, context=context)

    try:
        sug = await asyncio.to_thread(_suggest)
        sug_id = store_suggestion(sug, project_dir=project_dir)  # §18.5.1 (f)
        job.result = {"suggestion_id": sug_id, "safe": sug.safe, "command_line": sug.command_line}
        await job.queue.put({
            "event": "finish",
            "suggestion_id": sug_id,
            "command_line": sug.command_line,
            "safe": sug.safe,
            "reason": sug.reason,
            "write_paths": sug.write_paths,
        })
    except asyncio.CancelledError:
        try:
            job.queue.put_nowait({"event": "finish", "suggestion_id": None, "cancelled": True})
        except Exception:
            pass
        raise
    except RuntimeError as e:
        code = "E_LLM_NOT_CONFIGURED" if "未設定" in str(e) else "E_LLM_CONNECT"
        msg = str(e)
        job.result = {"error": msg, "code": code}
        await job.queue.put({"event": "error", "code": code, "message": msg})
    except Exception as e:
        code = "E_LLM_CONNECT" if "connect" in str(e).lower() or "timeout" in str(e).lower() else "E_LLM_UNKNOWN"
        msg = str(e)
        job.result = {"error": msg}
        await job.queue.put({"event": "error", "code": code, "message": msg})
    finally:
        job.done = True


async def run_llm_run_job(
    job: Job,
    suggestion_id: str,
    project_dir: Optional[str] = None,
) -> None:
    """提案IDを元に deltasim サブコマンドを subprocess で実行するジョブ。

    セキュリティ: suggestion_id はサーバーが発行したもので、任意コマンドは受け付けない。

    §18.5.1: 実行時の作業フォルダをプロジェクトフォルダ（SV ファイルのあるフォルダ）に設定する。
    deltasim.log が CLI を直接使ったときと同じ場所（プロジェクトフォルダ）に書かれる。
    コマンドには余分な引数を追加しない（表示コマンドと実行が一致する）。
    """
    import subprocess, sys as _sys
    from deltasim.gui.llm_session import get_suggestion

    sug = get_suggestion(suggestion_id)
    if sug is None:
        await job.queue.put({"event": "error", "code": "E_LLM_NO_SUGGESTION",
                             "message": f"提案が見つかりません: {suggestion_id}"})
        job.done = True
        return
    if not sug.safe:
        await job.queue.put({"event": "error", "code": "E_LLM_UNSAFE_CMD",
                             "message": f"安全でないコマンドです: {sug.reason}"})
        job.done = True
        return

    def _run():
        argv = [_sys.executable, "-m", "deltasim"] + sug.args
        # §18.5.1: cwd をプロジェクトフォルダに設定（引数は変えない）
        cwd = project_dir if project_dir and Path(project_dir).is_dir() else None
        proc = subprocess.run(argv, shell=False, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=cwd)
        return proc.returncode, proc.stdout, proc.stderr

    try:
        rc, out, err = await asyncio.to_thread(_run)
        job.result = {"exit_code": rc, "stdout": out, "stderr": err}
        await job.queue.put({"event": "finish", "exit_code": rc, "stdout": out, "stderr": err})
    except Exception as e:
        job.result = {"error": str(e)}
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True


async def run_llm_draft_job(
    job: Job,
    sv_files: List[str],
    top: Optional[str] = None,
    cfg=None,
    llm_fn=None,
) -> None:
    """YAML スペック下書き生成ジョブ（draft_spec を非同期実行）。"""

    def _draft():
        from deltasim.llm import draft_spec, load_llm_config
        effective_cfg = cfg if cfg is not None else load_llm_config()
        if effective_cfg is None:
            raise RuntimeError("LLM が未設定です（E_LLM_NOT_CONFIGURED）")
        return draft_spec(sv_files, top=top, cfg=effective_cfg, llm_fn=llm_fn)

    try:
        result = await asyncio.to_thread(_draft)
        job.result = {"yaml_text": result.yaml_text, "valid": result.valid, "errors": result.errors}
        await job.queue.put({
            "event": "finish",
            "yaml_text": result.yaml_text,
            "valid": result.valid,
            "errors": result.errors,
        })
    except asyncio.CancelledError:
        try:
            job.queue.put_nowait({"event": "finish", "yaml_text": "", "cancelled": True})
        except Exception:
            pass
        raise
    except RuntimeError as e:
        code = "E_LLM_NOT_CONFIGURED" if "未設定" in str(e) else "E_LLM_CONNECT"
        await job.queue.put({"event": "error", "code": code, "message": str(e)})
    except Exception as e:
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True


async def run_llm_explain_job(
    job: Job,
    result_data: dict,
    sv_files: Optional[List[str]] = None,
    spec_file: Optional[str] = None,
    cfg=None,
    llm_fn=None,
) -> None:
    """失敗解析ジョブ（explain_result を非同期実行）。"""

    def _explain():
        from deltasim.llm import explain_result, load_llm_config
        effective_cfg = cfg if cfg is not None else load_llm_config()
        if effective_cfg is None:
            raise RuntimeError("LLM が未設定です（E_LLM_NOT_CONFIGURED）")
        return explain_result(result_data, rtl_files=sv_files, spec_file=spec_file,
                              cfg=effective_cfg, llm_fn=llm_fn)

    try:
        text = await asyncio.to_thread(_explain)
        job.result = {"explanation": text}
        await job.queue.put({"event": "finish", "explanation": text})
    except asyncio.CancelledError:
        try:
            job.queue.put_nowait({"event": "finish", "explanation": "", "cancelled": True})
        except Exception:
            pass
        raise
    except RuntimeError as e:
        code = "E_LLM_NOT_CONFIGURED" if "未設定" in str(e) else "E_LLM_CONNECT"
        await job.queue.put({"event": "error", "code": code, "message": str(e)})
    except Exception as e:
        await job.queue.put({"event": "error", "message": str(e)})
    finally:
        job.done = True
