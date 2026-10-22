"""GUI アダプタ層 — M5〜v0.2.2 公開 API (deltasim.api) の薄いラッパー。

変更履歴:
  PR#9〜#14 merge: E_NO_CASE が API 正式実装されたため adapter のラップを削除。
  §15.12/13: warnings[] を TestResult・CaseResult の両方からレスポンスに含める。
  §18.4 (本PR): gen(), 一時 VCD の vcd_path 解決, load() のトップ候補一覧を追加。

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
    if "total_cycles" in sse:
        sse["total"] = sse["total_cycles"]
    try:
        callback(sse)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# _get_spec_vcd(): YAML の sim.vcd を取得する（内部ヘルパー）
# ---------------------------------------------------------------------------

def _get_spec_vcd(spec_path: str) -> Optional[str]:
    """YAML スペックの sim.vcd を返す（設定なければ None）。読み込み失敗は無視。"""
    try:
        import yaml as _yaml
        with open(spec_path, encoding="utf-8") as f:
            data = _yaml.safe_load(f) or {}
        v = data.get("sim", {}).get("vcd")
        return str(v) if v else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# load(): RTL ファイルを解析してモジュール情報を返す
# ---------------------------------------------------------------------------

def _find_top_candidates(mods_catalog: dict) -> dict:
    """instantiated でないモジュール名を top 候補として返す。

    全モジュールの InstDecl をスキャンし、どこからもインスタンス化されていない
    モジュールを top 候補（is_top_candidate=True）とする。
    公開 API の catalog（モジュール名 → Module オブジェクト）を使う。
    """
    try:
        from deltasim.parser.ast_nodes import InstDecl
        instantiated: set = set()
        for mod in mods_catalog.values():
            for item in mod.body:
                if isinstance(item, InstDecl):
                    instantiated.add(item.module_name)
        return {
            name: {"is_top_candidate": name not in instantiated}
            for name in mods_catalog
        }
    except Exception:
        return {name: {"is_top_candidate": True} for name in mods_catalog}


def load(
    files: List[str],
    top: Optional[str] = None,
    *,
    param_overrides: Optional[Dict[str, int]] = None,
) -> dict:
    """RTL ファイルをパース・エラボレーションしてモジュール情報を返す。

    §18.4: all_modules にすべての解析済みモジュール一覧と is_top_candidate フラグを返す。
    """
    import deltasim.api as ds

    di = ds.load([str(f) for f in files], top=top, params=param_overrides or {})

    # §18.4: トップ候補判定（catalog から）
    all_modules: List[dict] = []
    cat = getattr(di, "_catalog", None) or {}
    if cat:
        candidates = _find_top_candidates(cat)
        # トップ候補を先頭に
        top_cands = [
            {"name": n, "is_top_candidate": True}
            for n, v in candidates.items()
            if v["is_top_candidate"]
        ]
        non_top = [
            {"name": n, "is_top_candidate": False}
            for n, v in candidates.items()
            if not v["is_top_candidate"]
        ]
        all_modules = top_cands + non_top
    elif di.module:
        all_modules = [{"name": di.module, "is_top_candidate": True}]

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
        "all_modules": all_modules,
    }


# ---------------------------------------------------------------------------
# gen(): テストベンチと YAML スペックのスケルトンを生成する
# ---------------------------------------------------------------------------

def gen(
    sv_files: List[str],
    top: Optional[str] = None,
    *,
    param_overrides: Optional[Dict[str, int]] = None,
    out_dir: Optional[str] = None,
    tb_path: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> dict:
    """テストベンチ SV と YAML スペックのスケルトンを生成する。

    out_dir/tb_path/spec_path がすべて None の場合は文字列で返す（ファイル不要モード）。
    それ以外は指定パスにファイルを書き出し、パスを返す。
    """
    import deltasim.api as ds

    result = ds.gen(
        [str(f) for f in sv_files],
        top=top,
        params=param_overrides or {},
        out_dir=out_dir,
        tb_path=tb_path,
        spec_path=spec_path,
    )
    return result


# ---------------------------------------------------------------------------
# list_spec_cases(): YAML スペックからケース名一覧を返す
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
        return [sc._stem]
    except Exception:
        return []


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
    cancel_event=None,          # threading.Event | None
    filter_cases: Optional[List[str]] = None,
) -> dict:
    """テストを実行して結果 JSON を返す（cases[] ベース）。

    §18.4: vcd_path が None でも spec が sim.vcd を持つ場合は spec のパスを result に返す。

    filter_cases: ケース名のリスト（fnmatch ワイルドカード可）。
      - None / [] → 全ケース実行
      - 一致なし → API が直接 E_NO_CASE ERROR を返す（PR#9 で正式実装済み）
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

    # §17.1: CLI の test --json と同じ構造（TestResult.to_dict() を再利用）
    # deltasim_version / seed / elapsed_ms をトップレベルに含む
    d = result.to_dict()
    # エクスポート用: TestResult オブジェクトをそのまま保持（JSON 非直列化フィールド）
    d["_test_result"] = result

    # GUI フロントエンド用 summary カウント（CLI の to_dict() は counts を含まない）
    n_pass  = sum(1 for c in result.cases if c.status == "PASS")
    n_fail  = sum(1 for c in result.cases if c.status == "FAIL")
    n_error = sum(1 for c in result.cases if c.status == "ERROR")
    n_skip  = sum(1 for c in result.cases if c.status == "SKIP")
    total   = len(result.cases)
    if result.status == "ERROR" and total == 0:
        n_error = 1
        total   = 1

    d["summary"].update({
        "total": total,
        "pass": n_pass,
        "fail": n_fail,
        "error": n_error,
        "skip": n_skip,
        "cancelled": result.cancelled,
    })

    # §18.4: 有効な VCD パスを解決して GUI フロントエンドに返す
    # 優先順: 呼び出し側が指定した vcd_path > spec の sim.vcd > None
    if vcd_path is not None:
        effective_vcd = vcd_path
    else:
        effective_vcd = _get_spec_vcd(spec_path)
    d["vcd_path"] = effective_vcd

    # §12.3: truncated_at はケース単位で管理（TestResult レベルは API で未設定）
    d["truncated_at"] = next(
        (c.truncated_at for c in result.cases if c.truncated_at is not None), None
    )

    return d


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
    """シミュレーションを実行して結果を返す（公開 API 使用版）。"""
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
