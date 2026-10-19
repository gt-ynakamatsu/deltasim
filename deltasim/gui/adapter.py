"""GUI アダプタ層 — 既存の内部 API を GUI 向けの JSON 契約に変換する。

この 1 ファイルに閉じた薄い橋渡し層です。
M5 で公開 API（load / gen / test / run）が main に入ったら、
各関数をそちらへ差し替える前提で設計しています。

現状（M5 未実装）:
  - load()   → parse_file + elaborate を呼ぶ
  - test()   → TestRunner を直接呼ぶ
  - run()    → SimEngine を直接呼ぶ

結果 JSON の schema_version: 1（GUI 仕様書 §2 の契約に合わせる）

ステータス: PASS / FAIL / ERROR / SKIP
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# 公開型
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1


class AdapterError(Exception):
    """パース・エラボレーション・シミュレーション時の構造化エラー"""

    def __init__(
        self,
        message: str,
        *,
        file: Optional[str] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
        severity: str = "error",
        code: Optional[str] = None,
    ):
        super().__init__(message)
        self.file = file
        self.line = line
        self.column = column
        self.severity = severity
        self.code = code

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
            "message": str(self),
            "code": self.code,
        }


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
        "ports": [{"name": str, "direction": str, "width": int}, ...],
        "params": {name: value, ...},
        "errors": [],   # 構造化エラーリスト
    }
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError

    if param_overrides is None:
        param_overrides = {}

    errors: list[dict] = []
    modules = []

    for sv_path in files:
        sv_path = str(sv_path)
        if not os.path.isfile(sv_path):
            raise AdapterError(
                f"ファイルが見つかりません: {sv_path!r}",
                file=sv_path,
                severity="error",
            )
        try:
            mod = parse_file(sv_path)
            modules.append(mod)
        except ParseError as e:
            raise AdapterError(str(e), file=sv_path, severity="error", code="parse") from e

    if not modules:
        raise AdapterError("解析対象のモジュールがありません", severity="error")

    if top is not None:
        module = next((m for m in modules if m.name == top), None)
        if module is None:
            names = [m.name for m in modules]
            raise AdapterError(
                f"モジュール {top!r} が見つかりません。利用可能: {names}",
                severity="error",
                code="module_not_found",
            )
    else:
        module = modules[0]

    try:
        design = elaborate(module, param_overrides=param_overrides)
    except ElabError as e:
        raise AdapterError(str(e), severity="error", code="elab") from e

    ports = [
        {
            "name": name,
            "direction": info.direction,
            "width": info.width,
        }
        for name, info in design.signals.items()
    ]

    return {
        "module": design.module_name,
        "ports": ports,
        "params": design.params,
        "errors": errors,
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
) -> dict:
    """テストを実行して結果 JSON を返す。

    仕様書 §2 の契約:
      - schema_version: int
      - cases: [{name, status, seed, duration_sec, message, failures, log}, ...]
      - summary: {total, pass, fail, error, skip}

    on_progress: 進捗コールバック。start / case_done / progress / finish イベントを受け取る。
    現状はシングルケースとして扱い、M5 で複数ケース対応に差し替え予定。
    """
    import time as _time

    from deltasim.parser.parser import ParseError
    from deltasim.elab.elaborator import elaborate, ElabError
    from deltasim.tb.scenario import load_scenario, ScenarioError
    from deltasim.tb.runner import TestRunner
    from deltasim.sim.engine import SimError

    if param_overrides is None:
        param_overrides = {}

    # --- 進捗: start ---
    _emit(on_progress, {"event": "start", "spec": spec_path})

    t0 = _time.monotonic()

    # Load scenario
    try:
        scenario = load_scenario(spec_path)
    except ScenarioError as e:
        _emit(on_progress, {"event": "finish", "status": "error"})
        return _error_result(f"spec エラー: {e}", spec_path)

    # Seed override
    if seed is not None:
        scenario.sim.seed = seed

    # VCD path override
    if vcd_path:
        scenario.sim.vcd = vcd_path
    elif vcd_path is None and scenario.sim.vcd is None:
        # 自動的に一時ファイルに VCD を書く（波形表示のため）
        tmp = tempfile.NamedTemporaryFile(
            suffix=".vcd", delete=False, prefix="deltasim_"
        )
        tmp.close()
        scenario.sim.vcd = tmp.name

    # Determine top module
    top_name = top or scenario.top
    sv_files = [str(p) for p in sv_files]

    # Parse + elaborate
    from deltasim.parser.parser import parse_file
    modules = []
    for sv_path in sv_files:
        try:
            modules.append(parse_file(sv_path))
        except ParseError as e:
            _emit(on_progress, {"event": "finish", "status": "error"})
            return _error_result(f"parse エラー: {e}", spec_path, sv_path)

    if not modules:
        _emit(on_progress, {"event": "finish", "status": "error"})
        return _error_result("解析対象のモジュールがありません", spec_path)

    module = (
        next((m for m in modules if m.name == top_name), None)
        if top_name
        else modules[0]
    )
    if module is None:
        _emit(on_progress, {"event": "finish", "status": "error"})
        return _error_result(
            f"モジュール {top_name!r} が見つかりません", spec_path
        )

    try:
        design = elaborate(module, param_overrides=param_overrides)
    except ElabError as e:
        _emit(on_progress, {"event": "finish", "status": "error"})
        return _error_result(f"elab エラー: {e}", spec_path)

    # 進捗コールバック付き TestRunner
    runner = TestRunner(design, scenario, verbose=False)

    # 進捗: progress (サイクル / 合計)
    total_cycles = scenario.sim.cycles
    original_run = runner.engine.run

    def _instrumented_run(cycles, **kwargs):
        _emit(on_progress, {
            "event": "progress",
            "phase": "sim",
            "cycle": 0,
            "total": cycles,
            "time_ns": 0,
        })
        result = original_run(cycles, **kwargs)
        _emit(on_progress, {
            "event": "progress",
            "phase": "sim",
            "cycle": cycles,
            "total": cycles,
            "time_ns": runner.engine._time,
        })
        return result

    runner.engine.run = _instrumented_run

    try:
        run_result = runner.run()
    except SimError as e:
        _emit(on_progress, {"event": "finish", "status": "error"})
        return _error_result(f"sim エラー: {e}", spec_path)

    duration = _time.monotonic() - t0

    # ケース名はスペックファイル名から生成（M5 で複数ケース対応）
    case_name = Path(spec_path).stem

    failures = [
        {
            "cycle": cr.cycle,
            "signal": cr.signal,
            "expected": cr.expected,
            "actual": cr.actual,
            "time_ns": cr.cycle * scenario.clock.period_ns if scenario.clock.period_ns else 0,
            "source": cr.source,
        }
        for cr in run_result.checks
        if not cr.passed
    ]

    status = "PASS" if run_result.passed else "FAIL"
    if not run_result.checks:
        status = "SKIP"

    cases = [
        {
            "name": case_name,
            "status": status,
            "seed": scenario.sim.seed,
            "duration_sec": round(duration, 4),
            "message": None if run_result.passed else f"{len(failures)} 件の不一致",
            "failures": failures,
            "log": run_result.log,
        }
    ]

    n_pass = 1 if status == "PASS" else 0
    n_fail = 1 if status == "FAIL" else 0
    n_error = 1 if status == "ERROR" else 0
    n_skip = 1 if status == "SKIP" else 0

    result = {
        "schema_version": SCHEMA_VERSION,
        "module": design.module_name,
        "cases": cases,
        "summary": {
            "total": 1,
            "pass": n_pass,
            "fail": n_fail,
            "error": n_error,
            "skip": n_skip,
        },
        "vcd_path": scenario.sim.vcd,
    }

    _emit(on_progress, {"event": "case_done", "name": case_name, "status": status})
    _emit(on_progress, {"event": "finish", "status": status})

    return result


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
) -> dict:
    """シミュレーションを実行して VCD を書き出す（テスト判定なし）。

    Returns
    -------
    {
        "module": str,
        "cycles": int,
        "time_ns": int,
        "vcd_path": str | None,
        "log": [...],
        "finished": bool,
    }
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError
    from deltasim.sim.engine import SimEngine, SimError
    from deltasim.vcd.writer import VCDWriter

    if param_overrides is None:
        param_overrides = {}

    sv_files = [str(p) for p in sv_files]
    modules = []
    for sv_path in sv_files:
        try:
            modules.append(parse_file(sv_path))
        except ParseError as e:
            raise AdapterError(str(e), file=sv_path, severity="error", code="parse") from e

    if not modules:
        raise AdapterError("解析対象のモジュールがありません")

    module = next((m for m in modules if m.name == top), None) if top else modules[0]
    if module is None:
        raise AdapterError(f"モジュール {top!r} が見つかりません")

    try:
        design = elaborate(module, param_overrides=param_overrides)
    except ElabError as e:
        raise AdapterError(str(e), severity="error", code="elab") from e

    engine = SimEngine(design, half_period_ns=half_period, reset_cycles=reset_cycles)

    # 一時 VCD
    if vcd_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".vcd", delete=False, prefix="deltasim_")
        tmp.close()
        vcd_path = tmp.name

    vcd_obj = None
    if vcd_path:
        from deltasim.vcd.writer import VCDWriter
        vcd_obj = VCDWriter(vcd_path, design)
        vcd_obj.record_initial(0, engine.signals)
        engine.vcd_writer = vcd_obj

    try:
        sim_result = engine.run(cycles, reset_cycles=reset_cycles)
    except SimError as e:
        raise AdapterError(str(e), severity="error", code="sim") from e
    finally:
        if vcd_obj:
            vcd_obj.close()

    return {
        "module": design.module_name,
        "cycles": cycles,
        "time_ns": engine._time,
        "vcd_path": vcd_path,
        "log": sim_result.log,
        "finished": sim_result.finished,
    }


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _emit(cb: Optional[Callable], event: dict) -> None:
    if cb is not None:
        try:
            cb(event)
        except Exception:
            pass


def _error_result(message: str, spec_path: str, sv_path: Optional[str] = None) -> dict:
    case_name = Path(spec_path).stem
    return {
        "schema_version": SCHEMA_VERSION,
        "module": None,
        "cases": [
            {
                "name": case_name,
                "status": "ERROR",
                "seed": None,
                "duration_sec": 0,
                "message": message,
                "failures": [],
                "log": [],
            }
        ],
        "summary": {"total": 1, "pass": 0, "fail": 0, "error": 1, "skip": 0},
        "vcd_path": None,
    }
