"""GUI アダプタ層 — M5/PR#7/PR#8 公開 API (deltasim.api) の薄いラッパー。

PR#8（§13 マルチケース）で追加:
  - TestResult.cases[] (CaseResult) を使用（seed/elapsed_ms/log が per-case）
  - トップレベル elapsed_ms は非推奨 → summary.elapsed_ms を使用
  - API は常に TestResult を返す（None なし）
  - YAML マルチケース書式（cases: リスト）対応

非推奨フィールドを使用しない:
  - result.elapsed_ms (非推奨) → result.summary.elapsed_ms を参照
  - case_done バッファ → API が §12.2 準拠（PR#7 で解消済み）
  - truncated_at warning fallback → case.truncated_at を直接参照

残課題（docs/api.md §未決事項より管理）:
  - §未決事項 #5: Simulator.force() の NBA 抑制精度
  - §未決事項 #6: 階層シグナルパス（フラット展開のまま）
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "2"  # PR#7 で更新、PR#8 で維持


# ---------------------------------------------------------------------------
# 進捗イベント変換ヘルパー
# ---------------------------------------------------------------------------

def _progress_to_sse(ev: dict, callback: Optional[Callable]) -> None:
    """deltasim.api の進捗 dict（type フィールド）を SSE dict（event フィールド）に変換。"""
    if callback is None:
        return
    sse = dict(ev)
    sse["event"] = sse.pop("type", "progress")
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

def list_spec_cases(spec_path: str) -> list:
    """YAML スペックファイルからケース名一覧を返す（GUI フィルタ UI 用）。

    判定ロジックは GUI が持たない原則に従い、scenario.cases[] の name を返すだけ。
    旧式（cases: なし）は stem から生成される単一ケースを返す。
    """
    try:
        from deltasim.tb.scenario import load_scenario
        sc = load_scenario(spec_path)
        if sc.cases:
            return [c.name for c in sc.cases]
        # 旧式: ファイル stem が単一ケース名
        return [sc._stem]
    except Exception as e:
        return []  # エラーは呼び出し元でハンドリング


def test(
    sv_files: List[str],
    spec_path: str,
    *,
    top: Optional[str] = None,
    seed: Optional[int] = None,
    vcd_path: Optional[str] = None,
    param_overrides: Optional[Dict[str, int]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel_event=None,          # threading.Event | None
    filter_cases: Optional[List[str]] = None,  # ケース名フィルタ（ワイルドカード可）
) -> dict:
    """テストを実行して結果 JSON を返す（cases[] ベース）。

    filter_cases: ケース名のリスト（fnmatch ワイルドカード可）。
      - None / [] → 全ケース実行
      - 一致なし → E_NO_CASE エラーを返す（API が PASS/empty を返す場合に adapter でラップ）
    """
    import deltasim.api as ds
    from deltasim.result import CancelToken

    token = CancelToken()

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
        filter_cases=filter_cases or None,
    )

    # §13.2: cases[] を使用（per-case details）
    cases = [c.to_dict() for c in result.cases]

    # E_NO_CASE: filter_cases が指定されたが一致するケースが0件
    # 現状の API は PASS/empty を返すので adapter で ERROR に変換
    e_no_case = (
        bool(filter_cases)              # フィルタが指定されている
        and len(result.cases) == 0      # 一致するケースなし
        and result.status not in ("ERROR",)  # 既に ERROR でない
    )
    if e_no_case:
        patterns = ", ".join(f"'{p}'" for p in (filter_cases or []))
        no_case_msg = f"E_NO_CASE: フィルタ {patterns} に一致するケースがありません"
        if on_progress:
            _progress_to_sse({"type": "finish", "status": "ERROR"}, on_progress)
        return {
            "schema_version": SCHEMA_VERSION,
            "module": result.module or "",
            "status": "ERROR",
            "cancelled": False,
            "summary": {"total": 0, "pass": 0, "fail": 0, "error": 1, "skip": 0,
                        "elapsed_ms": 0},
            "cases": [],
            "checks": [],
            "log": [],
            "errors": [{"file": "", "line": 0, "column": 0, "severity": "error",
                        "message": no_case_msg, "code": "E_NO_CASE"}],
            "vcd_path": vcd_path,
            "truncated_at": None,
            "e_no_case": True,         # GUI で判別用フラグ
        }

    # summary: §13.1 summary.elapsed_ms が推奨
    total_elapsed = result.summary.get("elapsed_ms", result.elapsed_ms) \
        if hasattr(result, "summary") and isinstance(result.summary, dict) \
        else result.elapsed_ms

    # summary counts (cases ベース)
    n_pass  = sum(1 for c in result.cases if c.status == "PASS")
    n_fail  = sum(1 for c in result.cases if c.status == "FAIL")
    n_error = sum(1 for c in result.cases if c.status == "ERROR")
    n_skip  = sum(1 for c in result.cases if c.status == "SKIP")
    total   = len(result.cases)

    # エラー（パース失敗等）でケースが空の場合
    if result.status == "ERROR" and total == 0:
        n_error = 1
        total   = 1

    return {
        "schema_version": result.schema_version,
        "module": result.module,
        "status": result.status,
        "cancelled": result.cancelled,
        # §13.1: summary.elapsed_ms が推奨、トップレベルは非推奨なので含めない
        "summary": {
            "total": total,
            "pass": n_pass,
            "fail": n_fail,
            "error": n_error,
            "skip": n_skip,
            "elapsed_ms": total_elapsed,  # §13.1 推奨フィールド
            "cancelled": result.cancelled,
        },
        # §13.2: per-case details（name/status/seed/elapsed_ms/checks/log）
        "cases": cases,
        # backward compat: 集約 checks（非推奨、GUI は cases を使用）
        "checks": [c.to_dict() for c in result.checks],
        "log": result.log,
        "errors": [e.to_dict() for e in result.errors],
        "vcd_path": vcd_path,
        # §12.3: truncated_at はケースごとに cases[].truncated_at で確認
        "truncated_at": next(
            (c.truncated_at for c in result.cases if c.truncated_at is not None), None
        ),
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
    """シミュレーションを実行して結果を返す（PR#8 公開 API 使用版）。"""
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
        "_run_result": result,
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
