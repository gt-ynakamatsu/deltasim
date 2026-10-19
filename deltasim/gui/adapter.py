"""GUI アダプタ層 — M5/PR#7 公開 API (deltasim.api) の薄いラッパー。

PR#7（マイルストーン6）で解消した §12 の暫定コードをすべて削除:
  - case_done 集約バッファ → API が §12.2 準拠（シナリオ単位で1回発火）
  - truncated_at の warning fallback → result.truncated_at フィールドを直接参照
  - Z/X の区別 → val_to_4state が "z"/"x" を正しく返す
  - summary.elapsed_ms → TestResult.to_dict() が summary キーを返す

残課題（reports/api.md の未決事項として管理）:
  - §未決事項 #5: Simulator.force() の NBA 抑制精度
  - §未決事項 #6: 階層シグナルパス（$scope フラット展開のまま）
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# PR#7 で SCHEMA_VERSION = "2" に更新
SCHEMA_VERSION = "2"


# ---------------------------------------------------------------------------
# 進捗イベント変換ヘルパー
# ---------------------------------------------------------------------------

def _progress_to_sse(ev: dict, callback: Optional[Callable]) -> None:
    """deltasim.api の進捗 dict（type フィールド）を SSE dict（event フィールド）に変換。"""
    if callback is None:
        return
    sse = dict(ev)
    sse["event"] = sse.pop("type", "progress")
    # total_cycles → total エイリアス（GUI が期待するフィールド名）
    if "total_cycles" in sse:
        sse["total"] = sse["total_cycles"]
    try:
        callback(sse)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# load(): RTL ファイルを解析してモジュール情報を返す
# ---------------------------------------------------------------------------

def load(
    files: List[str],
    top: Optional[str] = None,
    *,
    param_overrides: Optional[Dict[str, int]] = None,
) -> dict:
    """RTL ファイルをパース・エラボレーションしてモジュール情報を返す。"""
    import deltasim.api as ds

    di = ds.load([str(f) for f in files], top=top, params=param_overrides or {})

    return {
        "module": di.module,
        "ports": [
            {"name": p.name, "direction": p.direction, "width": p.width, "signed": p.signed}
            for p in di.ports
        ],
        "params": di.params,
        "signals": [
            {"name": s.name, "width": s.width, "direction": s.direction or ""}
            for s in di.signals
        ],
        "hierarchy": di.hierarchy.to_dict() if di.hierarchy else None,
        "status": di.status,
        "errors": [e.to_dict() for e in di.errors],
    }


# ---------------------------------------------------------------------------
# test(): YAML スペックに従ってテストを実行し、結果 JSON を返す
# ---------------------------------------------------------------------------

def test(
    sv_files: List[str],
    spec_path: str,
    *,
    top: Optional[str] = None,
    seed: Optional[int] = None,
    vcd_path: Optional[str] = None,
    param_overrides: Optional[Dict[str, int]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel_event=None,  # threading.Event | None
) -> dict:
    """テストを実行して結果 JSON を返す（M5/PR#7 公開 API 使用版）。

    PR#7 で確定した §12 の各点:
      §12.1 SKIP: cancelled=True 時、result.cancelled フィールドを使用
      §12.2 case_done: API がシナリオ単位で1回発火（暫定バッファ不要）
      §12.3 truncated_at: result.truncated_at フィールドを直接参照
      §12.4 Z/X: val_to_4state が "z"/"x" を正しく返す（暫定コード不要）
      §12.5 summary.elapsed_ms: TestResult.to_dict() が summary キーを返す
    """
    import deltasim.api as ds
    from deltasim.result import CancelToken

    token = CancelToken()

    # threading.Event → CancelToken の変換スレッド
    if cancel_event is not None:
        def _watch():
            cancel_event.wait()
            token.cancel()
        threading.Thread(target=_watch, daemon=True).start()

    def _progress(ev: dict) -> None:
        _progress_to_sse(ev, on_progress)

    result = ds.test(
        [str(f) for f in sv_files],
        spec_path,
        top=top,
        seed=seed,
        vcd=vcd_path,
        params=param_overrides or {},
        on_progress=_progress,
        cancel=token,
    )

    # summary 構築（PR#7: result.to_dict() が summary を持つ）
    n_pass  = sum(1 for c in result.checks if c.status == "PASS")
    n_fail  = sum(1 for c in result.checks if c.status == "FAIL")
    n_error = sum(1 for c in result.checks if c.status == "ERROR")
    n_skip  = sum(1 for c in result.checks if c.status == "SKIP")
    total   = len(result.checks)

    if result.status == "ERROR" and total == 0:
        n_error = 1
        total   = 1

    return {
        "schema_version": result.schema_version,   # PR#7: "2"
        "module": result.module,
        "status": result.status,
        "seed": result.seed,
        "elapsed_ms": result.elapsed_ms,
        "cancelled": result.cancelled,
        "checks": [c.to_dict() for c in result.checks],
        "log": result.log,
        "errors": [e.to_dict() for e in result.errors],
        "summary": {
            "total": total,
            "pass": n_pass,
            "fail": n_fail,
            "error": n_error,
            "skip": n_skip,
            "elapsed_ms": result.elapsed_ms,  # §12.5: 全体の実時間
            "cancelled": result.cancelled,
        },
        "vcd_path": vcd_path,
        "truncated_at": result.truncated_at,  # §12.3: PR#7 で TestResult フィールドに昇格
    }


# ---------------------------------------------------------------------------
# run(): シミュレーション実行（波形取得用、テスト判定なし）
# ---------------------------------------------------------------------------

def run(
    sv_files: List[str],
    *,
    top: Optional[str] = None,
    cycles: int = 100,
    vcd_path: Optional[str] = None,
    param_overrides: Optional[Dict[str, int]] = None,
    half_period: int = 5,
    reset_cycles: int = 10,
    on_progress: Optional[Callable[[dict], None]] = None,
    keep_changes: bool = False,
) -> dict:
    """シミュレーションを実行して結果を返す（M5/PR#7 公開 API 使用版）。"""
    import deltasim.api as ds

    def _progress(ev: dict) -> None:
        _progress_to_sse(ev, on_progress)

    result = ds.run(
        [str(f) for f in sv_files],
        top=top,
        cycles=cycles,
        vcd=vcd_path,
        params=param_overrides or {},
        half_period=half_period,
        reset_cycles=reset_cycles,
        on_progress=_progress,
        keep_changes=keep_changes,
    )

    return {
        "module": result.module,
        "status": result.status,
        "cycles": result.cycles,
        "time_ns": result.time_ns,
        "vcd_path": result.vcd_path,
        "log": result.log,
        "cancelled": result.cancelled,
        "elapsed_ms": result.elapsed_ms,
        "errors": [e.to_dict() for e in result.errors],
        "_run_result": result,  # SignalChangeDB アクセス用（内部）
    }


# ---------------------------------------------------------------------------
# get_signal_changes_from_run(): メモリ上の信号変化を取得
# ---------------------------------------------------------------------------

def get_signal_changes_from_run(
    run_result_obj: dict,
    signal: str,
    start_ns: int = 0,
    end_ns: Optional[int] = None,
) -> list:
    """run() 結果から信号変化リストを取得（VCDファイル不要）。"""
    raw = run_result_obj.get("_run_result")
    if raw is None:
        return []
    changes = raw.get_signal_changes(signal, start_ns=start_ns, end_ns=end_ns)
    return [{"time_ns": c.time_ns, "value": c.value} for c in changes]
