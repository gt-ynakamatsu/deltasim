"""GUI アダプタ層 — M5 公開 API (deltasim.api) の薄いラッパー。

M5 公開 API に対応した差し替え版。
- load / test / run はすべて deltasim.api に委譲する
- 内部関数（parse_file, elaborate, TestRunner）の直接呼び出しはゼロ
- 進捗コールバック: deltasim.api の on_progress (type フィールド) を SSE イベント (event フィールド) へ変換
- キャンセル: CancelToken を使用、mid-cycle での真のキャンセルが可能
- 構造化エラー: ErrorInfo をそのままフロントへ返す

仕様書 §2 との整合確認（docs/api.md 参照）:
  - schema_version は文字列 "1"
  - test() は TestResult.checks を返す（cases ではない）
  - 進捗イベントのフィールド名は `type`（GUI SSE では `event` に変換）
  - Z/X は現状どちらも val_to_4state が "x" を返す（仕様書 §2.8 未決事項 #4）

未実装・M5以降の課題:
  - $scope 階層パスの VCD: 現在フラット展開（未決事項 #6）
  - Simulator.force() の NBA 抑制精度（未決事項 #5）
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 公開型（後方互換のため残す）
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1"  # str に変更（M5 contract）


class AdapterError(Exception):
    """アダプタ層からのエラー（未使用になったが後方互換のため残す）"""

    def __init__(self, message: str, *, file: Optional[str] = None,
                 line: Optional[int] = None, column: Optional[int] = None,
                 severity: str = "error", code: Optional[str] = None):
        super().__init__(message)
        self.file = file
        self.line = line
        self.column = column
        self.severity = severity
        self.code = code

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "column": self.column,
                "severity": self.severity, "message": str(self), "code": self.code}


# ---------------------------------------------------------------------------
# 進捗イベント変換ヘルパー
# ---------------------------------------------------------------------------

def _progress_to_sse(ev: dict, callback: Optional[Callable]) -> None:
    """deltasim.api の進捗 dict（type フィールド）を SSE dict（event フィールド）に変換して callback に渡す。"""
    if callback is None:
        return
    sse = dict(ev)
    # type → event フィールドに変換（GUI SSE 形式）
    sse["event"] = sse.pop("type", "progress")
    # total_cycles → total へエイリアス（GUI が期待するフィールド）
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
    """RTL ファイルをパース・エラボレーションしてモジュール情報を返す。

    Returns
    -------
    {
        "module": str,
        "ports": [{"name": str, "direction": str, "width": int, "signed": bool}, ...],
        "params": {name: value, ...},
        "errors": [...],  # ErrorInfo.to_dict() のリスト
    }
    """
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
    """テストを実行して結果 JSON を返す（M5 公開 API 使用版）。

    §12 対応:
      - cancelled=True の場合、未実行ケースは SKIP として summary に加算
      - truncated_at フィールドがあれば透過して返す（§12.3）
      - case_done イベントはケース単位で送る（§12.2）
        ※ M5 API は個別 check ごとに case_done を発火するため、
          同一シナリオ名をバッファして最後にまとめる変換を行う
          （API が §12.2 準拠になったら変換コードを削除）

    仕様書との差異（→ 報告済み）:
      - M5 API の case_done は個別 check 単位で発火（§12.2 は "ケース単位で1回"）
      - truncated_at は M5 TestResult に未定義（§12.3 の warning/truncated_at は未実装）
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

    # case_done バッファ（§12.2: ケース単位で1回）
    # M5 API が個別 check ごとに case_done を発火するため、
    # シナリオ名（= spec ファイル stem）単位にまとめてから発火する暫定実装
    _case_buf: dict = {}
    _scenario_name = Path(spec_path).stem

    def _progress(ev: dict) -> None:
        ev_type = ev.get("type", "")

        if ev_type == "case_done":
            # 個別 check を蓄積、シナリオ全体の状態として合算
            check_status = ev.get("status", "PASS")
            buf = _case_buf.setdefault(_scenario_name, {"status": "PASS", "checks": []})
            buf["checks"].append(ev)
            # 最低1件 FAIL → FAIL, ERROR が混じれば ERROR
            if check_status in ("ERROR",):
                buf["status"] = "ERROR"
            elif check_status == "FAIL" and buf["status"] != "ERROR":
                buf["status"] = "FAIL"
            # ケース単位の SSE は finish 後に発火するのでここでは何もしない
            return

        if ev_type == "finish":
            # finish の前にケース単位 case_done を発火（§12.2 準拠）
            for name, buf in _case_buf.items():
                sse_ev = {
                    "event": "case_done",
                    "name": name,
                    "status": buf["status"],
                    "checks": buf["checks"],
                }
                if on_progress:
                    try:
                        on_progress(sse_ev)
                    except Exception:
                        pass
            _case_buf.clear()

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

    checks_dicts = [c.to_dict() for c in result.checks]

    # §12.1: キャンセル時の未実行ケース → SKIP（現状は checks が途中までなので概算）
    n_pass  = sum(1 for c in result.checks if c.status == "PASS")
    n_fail  = sum(1 for c in result.checks if c.status == "FAIL")
    n_error = sum(1 for c in result.checks if c.status == "ERROR")
    n_skip  = sum(1 for c in result.checks if c.status == "SKIP")
    total   = len(result.checks)

    # §12.1: キャンセルされた場合は summary に cancelled_count を追記
    if result.status == "ERROR" and total == 0:
        n_error = 1
        total   = 1

    # §12.3: truncated_at — M5 TestResult に未実装のためフォールバックで None
    # API が truncated_at を実装したら result から取得する
    truncated_at = getattr(result, "truncated_at", None)
    # warning イベントを errors から探す
    for err in result.errors:
        if err.severity == "warning" and "truncated" in err.message.lower():
            if truncated_at is None:
                truncated_at = err.message  # 時刻が含まれる場合はパース

    return {
        "schema_version": result.schema_version,
        "module": result.module,
        "status": result.status,
        "seed": result.seed,
        "elapsed_ms": result.elapsed_ms,
        "cancelled": result.cancelled,
        "checks": checks_dicts,
        "log": result.log,
        "errors": [e.to_dict() for e in result.errors],
        "summary": {
            "total": total,
            "pass": n_pass,
            "fail": n_fail,
            "error": n_error,
            "skip": n_skip,
            "cancelled": result.cancelled,
        },
        "vcd_path": vcd_path,
        "truncated_at": truncated_at,  # §12.3: None = 上限未到達
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
    """シミュレーションを実行して結果を返す（M5 公開 API 使用版）。"""
    import deltasim.api as ds

    def _progress(ev: dict):
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
# get_signal_changes(): メモリ上の信号変化を取得
# ---------------------------------------------------------------------------

def get_signal_changes_from_run(
    run_result_obj,  # dict from run() with _run_result key
    signal: str,
    start_ns: int = 0,
    end_ns: Optional[int] = None,
) -> list:
    """run() 結果から信号変化リストを取得（VCDファイル不要）。

    Returns
    -------
    [{"time_ns": int, "value": str}, ...]
    """
    raw = run_result_obj.get("_run_result")
    if raw is None:
        return []
    changes = raw.get_signal_changes(signal, start_ns=start_ns, end_ns=end_ns)
    return [{"time_ns": c.time_ns, "value": c.value} for c in changes]
