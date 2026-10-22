"""deltasim public Python API — M5 contract.

Functions
---------
load(files_or_design, top, params)   → DesignInfo
gen(files_or_design, ...)            → dict  (paths or strings)
test(files_or_design, spec, ...)     → TestResult
run(files_or_design, ...)            → RunResult

Classes
-------
Simulator(design_or_files, ...)      — interactive debugger (§2.5)

Every function and class in this module accepts **either**
  * A list of .sv file paths  (List[str | Path])
  * A DesignInfo object already returned by load()

The CLI delegates entirely to this module.

Error handling
--------------
Functions return their result even on error: check result.status == "ERROR".
They never raise unless there is a programming error in the caller.
"""

from __future__ import annotations

import time
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Union

from deltasim.result import (
    SCHEMA_VERSION,
    CheckInfo,
    DesignInfo,
    ErrorInfo,
    HierarchyNode,
    LoadResult,
    PortInfo,
    ProgressEvent,
    RunResult,
    SignalChangeDB,
    SignalSpec,
    SimSnapshot,
    SimulationCancelled,
    TestResult,
    CancelToken,
    val_to_4state,
)

# ---------------------------------------------------------------------------
# Type alias for "files or design"
# ---------------------------------------------------------------------------

FilesOrDesign = Union[List[Union[str, Path]], DesignInfo]


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

def load(
    files: FilesOrDesign,
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
) -> DesignInfo:
    """Parse and elaborate SV files.

    Parameters
    ----------
    files:
        List of .sv file paths **or** a DesignInfo (returned as-is after
        merging *params* overrides).
    top:
        Top module name.  Auto-detected if None.
    params:
        Parameter overrides, e.g. {"WIDTH": 4}.

    Returns
    -------
    DesignInfo
        Always returned; check ``.status`` and ``.errors``.
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError

    if isinstance(files, DesignInfo):
        return files  # already elaborated

    params = params or {}
    result = DesignInfo(schema_version=SCHEMA_VERSION)

    try:
        mods, cat = _parse_files(files)
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E001"))
        return result

    mod, top_err = _select_top(mods, top, catalog=cat)
    if top_err is not None:
        result.status = "ERROR"
        result.errors.append(top_err)
        return result
    if mod is None:
        result.status = "ERROR"
        result.errors.append(ErrorInfo(message="No module found", code="E002"))
        return result

    try:
        design = elaborate(mod, param_overrides=params, catalog=cat)
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E003"))
        return result

    result.status = "PASS"
    result.module = design.module_name
    result.params = dict(design.params)
    result.ports = [
        PortInfo(
            name=name, direction=info.direction or "input",
            width=info.width, signed=info.signed,
        )
        for name, info in design.signals.items()
        if info.direction is not None
    ]
    result.signals = [
        SignalSpec(name=name, width=info.width,
                   direction=info.direction, signed=info.signed)
        for name, info in design.signals.items()
    ]
    result.hierarchy = _build_hierarchy(design.module_name, design)
    result._design = design
    result._catalog = cat
    return result


# ---------------------------------------------------------------------------
# gen()
# ---------------------------------------------------------------------------

def gen(
    files: FilesOrDesign,
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    out_dir: Optional[Union[str, Path]] = None,
    tb_path: Optional[Union[str, Path]] = None,
    spec_path: Optional[Union[str, Path]] = None,
) -> dict:
    """Generate testbench SV and YAML scenario skeleton.

    If *out_dir* is None and *tb_path* / *spec_path* are None,
    returns ``{'tb_sv': str, 'spec_yaml': str}`` (strings, no file I/O).
    Otherwise writes files and returns ``{'tb_path': str, 'spec_path': str}``.
    """
    from deltasim.tb.generator import generate_tb_sv, generate_yaml_spec
    import io, textwrap

    di = load(files, top=top, params=params)
    if di.status != "PASS":
        return {"status": "ERROR", "errors": [e.to_dict() for e in di.errors]}

    design = di._design
    top_name = di.module

    # ---- string mode ----
    if out_dir is None and tb_path is None and spec_path is None:
        # Generate into strings
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            tb_out = Path(tmpdir) / f"{top_name}_tb.sv"
            spec_out = Path(tmpdir) / f"{top_name}_spec.yaml"
            generate_tb_sv(design, str(tb_out))
            generate_yaml_spec(design, str(spec_out))
            tb_sv = tb_out.read_text(encoding="utf-8")
            spec_yaml = spec_out.read_text(encoding="utf-8")
        return {
            "status": "PASS",
            "schema_version": SCHEMA_VERSION,
            "module": top_name,
            "tb_sv": tb_sv,
            "spec_yaml": spec_yaml,
        }

    # ---- file mode ----
    out = Path(out_dir) if out_dir else Path(".")
    tb_out = Path(tb_path) if tb_path else out / f"{top_name}_tb.sv"
    spec_out = Path(spec_path) if spec_path else out / f"{top_name}_spec.yaml"
    try:
        out.mkdir(parents=True, exist_ok=True)
        generate_tb_sv(design, str(tb_out))
        generate_yaml_spec(design, str(spec_out))
    except Exception as e:
        return {"status": "ERROR", "errors": [_exc_to_error(e, "E004").to_dict()]}
    return {
        "status": "PASS",
        "schema_version": SCHEMA_VERSION,
        "module": top_name,
        "tb_path": str(tb_out),
        "spec_path": str(spec_out),
    }


# ---------------------------------------------------------------------------
# test()
# ---------------------------------------------------------------------------

def test(
    files: FilesOrDesign,
    spec: Union[str, Path],
    *,
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    seed: Optional[int] = None,
    vcd: Optional[Union[str, Path]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel: Optional[CancelToken] = None,
    record_all: bool = False,
    wave_mem_mb: int = 512,
    filter_cases: Optional[List[str]] = None,
    max_delta: Optional[int] = None,
    max_loop: Optional[int] = None,
) -> TestResult:
    """Run simulation driven by a YAML scenario spec.

    §13.2: If the YAML has a ``cases:`` list, every case is run and the
    results are collected in ``TestResult.cases``.  Legacy YAMLs without
    ``cases:`` are treated as a single case whose name = the file stem.

    ``filter_cases`` is a list of name patterns (shell wildcards, e.g. ``*``).
    Cases that do NOT match any pattern are excluded from the result; they do
    NOT appear even as SKIP.  The return value is always a ``TestResult``
    (never None).

    ``elapsed_ms`` = §13.1 total elapsed (parse + elab + sim, same as
    ``summary.elapsed_ms``).  Per-case time is in ``cases[].elapsed_ms``.
    """
    import fnmatch as _fnmatch
    from deltasim.tb.scenario import load_scenario, ScenarioError, CaseSpec
    from deltasim.sim.engine import SimEngine, SimError

    t0 = time.time()
    params = params or {}
    result = TestResult(schema_version=SCHEMA_VERSION, seed=seed or 0)

    def _emit(type_: str, **kw):
        if on_progress:
            ev = ProgressEvent(type=type_, **kw)
            on_progress(ev.to_dict())

    _emit("start", phase="parse")

    # Pre-run cancel
    if cancel and cancel.is_cancelled:
        result.status = "SKIP"
        result.log.append("cancelled")
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit("finish", status="SKIP", elapsed_ms=result.elapsed_ms, cancelled=True)
        return result

    # Load spec
    try:
        scenario = load_scenario(str(spec))
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E001"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit("finish", status="ERROR", elapsed_ms=result.elapsed_ms)
        return result

    # §15.6: Validate offset_ns for all cases (pre-simulation)
    from deltasim.tb.scenario import validate_offset_ns
    offset_errors = validate_offset_ns(scenario, spec_path=str(spec))
    if offset_errors:
        result.status = "ERROR"
        result.errors.extend(offset_errors)
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit("finish", status="ERROR", elapsed_ms=result.elapsed_ms)
        return result

    # §15.12: Propagate scenario-level warnings to result
    from deltasim.result import WarningInfo
    if hasattr(scenario, "_warnings") and scenario._warnings:
        result.warnings.extend(scenario._warnings)

    # Elaborate
    _emit("progress", phase="elab")
    top_name = top or scenario.top
    di = load(files, top=top_name, params=params)
    if di.status != "PASS":
        result.status = "ERROR"
        result.errors = di.errors
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit("finish", status="ERROR", elapsed_ms=result.elapsed_ms)
        return result

    result.module = di.module
    design = di._design

    if vcd:
        scenario.sim.vcd = str(vcd)

    # Build case list (§13.2)
    if scenario.cases:
        all_cases = scenario.cases
    else:
        # Legacy: one case using top-level settings
        legacy_case = CaseSpec(name=scenario._stem)
        legacy_case.skip = scenario.skip
        legacy_case.skip_reason = scenario.skip_reason
        legacy_case.inputs = scenario.inputs
        legacy_case.expected = scenario.expected
        all_cases = [legacy_case]

    # §12.1: filter by --case patterns (fnmatch wildcards)
    def _matches(cname: str) -> bool:
        if not filter_cases:
            return True
        return any(_fnmatch.fnmatch(cname, p) for p in filter_cases)

    run_cases = [c for c in all_cases if _matches(c.name)]
    # Cases that don't match are excluded entirely (not in result)

    # §14.1: all cases excluded → ERROR
    if filter_cases and len(run_cases) == 0:
        patterns_str = ", ".join(filter_cases)
        result.status = "ERROR"
        result.errors.append(ErrorInfo(
            severity="error",
            message=f"--case フィルタ {patterns_str!r} に一致するケースがありません",
            code="E_NO_CASE",
        ))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit("finish", status="ERROR", elapsed_ms=result.elapsed_ms)
        return result

    _emit("progress", phase="sim", cycle=0, total_cycles=scenario.sim.cycles, time_ns=0)

    # Run each case
    all_checks: List[CheckInfo] = []
    overall_status = "PASS"

    for case in run_cases:
        if cancel and cancel.is_cancelled:
            # Remaining cases are cancelled (not run, not included)
            result.cancelled = True
            break

        case_result = _run_one_case(
            case=case,
            scenario=scenario,
            seed_override=seed,
            di=di,
            design=design,
            on_progress=on_progress,
            cancel=cancel,
            record_all=record_all,
            wave_mem_mb=wave_mem_mb,
            t_parent=t0,
            max_delta=max_delta,
            max_loop=max_loop,
        )
        result.cases.append(case_result)
        all_checks.extend(case_result.checks)

        # Update overall status: ERROR > FAIL > SKIP > PASS
        if case_result.status == "ERROR":
            overall_status = "ERROR"
            # Propagate case errors to top-level errors (§0.2.1)
            result.errors.extend(case_result.errors)
        elif case_result.status == "FAIL" and overall_status not in ("ERROR",):
            overall_status = "FAIL"
        elif case_result.status == "SKIP" and overall_status == "PASS":
            overall_status = "SKIP"

        _emit("case_done",
              name=case_result.name,
              status=case_result.status,
              elapsed_ms=case_result.elapsed_ms,
              message=case_result.message,
              cancelled=case_result.cancelled)

    # §18.5 E_NO_CHECK: チェックが0件で全ケースが skip でない場合はエラー
    non_skip_cases = [cr for cr in result.cases if cr.status != "SKIP"]
    if non_skip_cases and all_checks == [] and overall_status == "PASS":
        overall_status = "ERROR"
        result.errors.append(ErrorInfo(
            code="E_NO_CHECK",
            message=(
                "テストにチェックが0件です。"
                "YAML スペックに expected/check を追加してください。"
            ),
            severity="error",
        ))

    result.status = overall_status
    result.checks = all_checks
    # Propagate cancelled flag from any case
    result.cancelled = result.cancelled or any(cr.cancelled for cr in result.cases)
    # Aggregate logs from all cases into top-level log
    all_logs: List[str] = []
    for cr in result.cases:
        all_logs.extend(cr.log)
    result.log = all_logs
    result.seed = scenario.sim.seed
    # §13.1: elapsed_ms = total (same as summary.elapsed_ms, non-deprecated)
    result.elapsed_ms = (time.time() - t0) * 1000

    _emit("finish", status=result.status, elapsed_ms=result.elapsed_ms,
          cancelled=result.cancelled)
    return result


def _run_one_case(
    case,
    scenario,
    seed_override,
    di,
    design,
    on_progress,
    cancel,
    record_all,
    wave_mem_mb,
    t_parent,
    max_delta=None,
    max_loop=None,
) -> "CaseResult":
    """Run a single CaseSpec and return CaseResult (§15.3 simulation loop)."""
    from deltasim.result import CaseResult, WarningInfo
    from deltasim.tb.scenario import StimulusSchedule, _expand_at
    from deltasim.sim.engine import (SimEngine, SimError, DeltaLimitError, LoopLimitError,
                                     _InitialScheduler, SimResult,
                                     MAX_DELTA as _DEF_MAX_DELTA,
                                     MAX_FOREVER_TICKS as _DEF_MAX_LOOP)

    case_t0 = time.time()
    cr = CaseResult(name=case.name)

    # Propagate scenario-level warnings to case result
    if hasattr(scenario, "_warnings"):
        cr.warnings = list(scenario._warnings)

    # §14.3 SKIP
    if case.skip:
        cr.status = "SKIP"
        cr.message = case.skip_reason if case.skip_reason else "skipped"
        if case.skip_reason:
            cr.log.append(case.skip_reason)
        cr.elapsed_ms = (time.time() - case_t0) * 1000
        return cr

    # Effective parameters (case overrides top-level)
    eff_seed = case.seed if case.seed is not None else (
        seed_override if seed_override is not None else scenario.sim.seed
    )
    eff_clock = case.clock or scenario.clock
    eff_reset = case.reset or scenario.reset
    eff_cycles = case.cycles if case.cycles is not None else scenario.sim.cycles
    eff_inputs = case.inputs if case.inputs else scenario.inputs
    eff_expected = case.expected if case.expected else scenario.expected
    eff_model_spec = case.model or scenario.model

    cr.seed = eff_seed

    # §18.5 E_UNKNOWN_PORT: YAML に書かれた信号名がポートに存在するか検査
    # stimulus の set: input ポートのみ許可
    # expected の check: ポート（input/output）のみ許可（内部信号は不可）
    from deltasim.tb.scenario import (
        ConstraintError as _ConstraintError,
        ConstraintExprError as _ConstraintExprError,
    )
    import re as _re

    input_ports = {n for n, info in design.signals.items() if info.direction == "input"}
    all_ports    = {n for n, info in design.signals.items() if info.direction is not None}

    def _port_error(msg):
        _err = ErrorInfo(code="E_UNKNOWN_PORT", message=msg, severity="error")
        cr.status = "ERROR"; cr.errors.append(_err); cr.message = msg
        cr.elapsed_ms = (time.time() - case_t0) * 1000

    for entry in eff_inputs:
        for sig in (entry.set_values or {}):
            if sig not in input_ports:
                in_all = sig in all_ports
                hint = ("output/inout ポートは stimulus で設定できません（force を使ってください）"
                        if in_all else f"既知の input ポート: {sorted(input_ports)}")
                _port_error(f"stimulus/set の '{sig}' は input ポートではありません。{hint}")
                return cr
        # §18.6 Fix 1: random_spec のキーも input ポートに限定
        for sig in (entry.random_spec or {}):
            if sig not in input_ports:
                in_all = sig in all_ports
                hint = ("output/inout ポートは stimulus で設定できません"
                        if in_all else f"既知の input ポート: {sorted(input_ports)}")
                _port_error(f"stimulus/random の '{sig}' は input ポートではありません。{hint}")
                return cr
    for entry in eff_expected:
        for sig in (entry.check or {}):
            if sig not in all_ports:
                _port_error(f"expected/check の '{sig}' はポートではありません。"
                            f"既知のポート: {sorted(all_ports)}")
                return cr

    # §18.6 Fix 2: 制約式中の識別子が input ポートか事前検査
    eff_constraints = case.constraints or scenario.constraints
    eff_max_attempts = (case.max_constraint_attempts
                        or scenario.max_constraint_attempts)
    if eff_constraints:
        for cstr in eff_constraints:
            try:
                from deltasim.interp.expr import _tokenize as _tok
                # §18.7.1: bare x/z は常に識別子なので除外しない
                idents = [t[1] for t in _tok(cstr) if t[0] == "ID"]
            except Exception:
                idents = _re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', cstr)
            for ident in idents:
                if ident not in input_ports:
                    _port_error(
                        f"制約式 '{cstr}' の識別子 '{ident}' は input ポートではありません。"
                        f"既知の input ポート: {sorted(input_ports)}"
                    )
                    return cr

    period_ns = eff_clock.period_ns       # §15.2 effective period
    hp = period_ns // 2                   # half-period (posedge→negedge)

    # Engine setup
    _eff_max_delta = max_delta if max_delta is not None else _DEF_MAX_DELTA
    _eff_max_loop  = max_loop  if max_loop  is not None else _DEF_MAX_LOOP
    engine = SimEngine(design, half_period_ns=hp, reset_cycles=eff_reset.duration_cycles,
                       max_delta=_eff_max_delta, max_loop=_eff_max_loop)
    if eff_clock.signal:
        engine.set_clk(eff_clock.signal)
    if eff_reset.signal:
        engine.set_rst(eff_reset.signal, active_low=eff_reset.active_low)

    # §18.6 Fix 4: 信号幅情報を StimulusSchedule に渡す
    _sig_widths = {n: info.width for n, info in design.signals.items() if info.direction == "input"}
    _sig_signed = {n: info.signed for n, info in design.signals.items() if info.direction == "input"}

    try:
        stim = StimulusSchedule(
            eff_inputs, eff_cycles, seed=eff_seed,
            constraints=eff_constraints,
            max_constraint_attempts=eff_max_attempts,
            signal_widths=_sig_widths,
            signal_signed=_sig_signed,
        )
    except _ConstraintError as _ce:
        err = ErrorInfo(code="E_CONSTRAINT", message=str(_ce), severity="error")
        cr.status = "ERROR"; cr.errors.append(err); cr.message = err.message
        cr.elapsed_ms = (time.time() - case_t0) * 1000
        return cr
    except _ConstraintExprError as _cee:
        err = ErrorInfo(code="E_CONSTRAINT_EXPR", message=str(_cee), severity="error")
        cr.status = "ERROR"; cr.errors.append(err); cr.message = err.message
        cr.elapsed_ms = (time.time() - case_t0) * 1000
        return cr

    # Reference model
    model = None
    if eff_model_spec:
        try:
            from deltasim.tb.runner import TestRunner as _TR
            _dummy = _TR(design, scenario)
            _dummy.scenario.model = eff_model_spec
            model = _dummy._load_model()
            if hasattr(model, "reset"):
                model.reset()
        except Exception as e:
            cr.status = "ERROR"
            err = _exc_to_error(e, "E006", message=f"参照モデルの読み込み失敗: {e}")
            cr.errors.append(err)
            cr.message = err.message
            cr.elapsed_ms = (time.time() - case_t0) * 1000
            return cr

    # VCD
    vcd_path = scenario.sim.vcd
    vcd_obj = None
    if vcd_path:
        from deltasim.vcd.writer import VCDWriter
        vcd_obj = VCDWriter(vcd_path, design)
        vcd_obj.record_initial(0, engine.signals)
        engine.vcd_writer = vcd_obj

    # Signal change DB
    record_set = None if record_all else _build_record_set(design, scenario)
    change_db = SignalChangeDB(
        max_bytes=wave_mem_mb * 1024 * 1024,
        record_set=record_set,
    )

    checks: List[CheckInfo] = []
    overall_status = "PASS"

    # ---------------------------------------------------------------
    # §15.5: Initialize engine — assert reset ONCE at start
    # ---------------------------------------------------------------
    engine._time = 0
    engine._result = SimResult()
    engine._initial_scheduler = _InitialScheduler()
    for ib in getattr(design, "initial_blocks", []):
        engine._initial_scheduler.add(ib, engine)

    if engine._rst is not None:
        rst_assert_val = 0 if engine._rst_active_low else 1
        engine._write(engine._rst, rst_assert_val)
    engine._result.history.append(dict(engine.signals))

    # ---------------------------------------------------------------
    # Build expected schedule: (cycle, offset_ns) -> list of dicts
    # ---------------------------------------------------------------
    exp_schedule: Dict = {}
    for exp in eff_expected:
        cycs = sorted(_expand_at(exp.at, eff_cycles))
        for c in cycs:
            exp_schedule.setdefault((c, exp.offset_ns), []).append(exp)

    def exp_offsets_for_cycle(cycle: int) -> set:
        return {off for (c, off) in exp_schedule if c == cycle}

    # ---------------------------------------------------------------
    # §15.4: Apply stimulus — write values + edge-triggered NBA
    # ---------------------------------------------------------------
    def apply_stimulus(vals: dict):
        changed = []
        for s, v in vals.items():
            # §18.7.1: 文字列 set 値（YAML 検証済み基数つきリテラル）を変換
            if isinstance(v, str):
                from deltasim.fourstate import parse_literal as _pl, to_legacy as _tl
                fs = _pl(v)   # parse 失敗は ScenarioError で弾かれるはずなので伝播
                v = _tl(fs)   # FourState → int/None/Z_VALUE（後方互換）
            old = engine.signals.get(s, 0)
            engine._write(s, v)
            new = engine.signals.get(s, 0)
            if new != old:
                changed.append((s, old, new))
        engine._eval_comb_loop()
        if changed:
            nba = []
            for s, o, n in changed:
                nba += engine._collect_nba_for_edge(s, o, n)
            engine._apply_nba(nba)
            engine._eval_comb_loop()
            if engine._initial_scheduler:
                for s, o, n in changed:
                    engine._initial_scheduler.notify_edge(s, o, n, engine._time, engine)

    def drive_clock(val: int):
        prev = engine.signals.get(engine._clk, 0) if engine._clk else 0
        if engine._clk:
            engine._write(engine._clk, val)
        nba = engine._collect_nba_for_edge(engine._clk, prev, val) if engine._clk else []
        engine._apply_nba(nba)
        engine._eval_comb_loop()
        if engine._initial_scheduler and engine._clk:
            engine._initial_scheduler.notify_edge(engine._clk, prev, val, engine._time, engine)

    def record_vcd_and_db(time_ns: int):
        if vcd_obj:
            vcd_obj.record(time_ns, engine.signals)
        for sname, sinfo in design.signals.items():
            change_db.record(time_ns, sname, engine.signals.get(sname), sinfo.width)

    def do_checks(cycle_num: int, off: int):
        nonlocal overall_status
        t_ns = (cycle_num - 1) * period_ns + hp + off
        for exp in exp_schedule.get((cycle_num, off), []):
            if exp.check:
                for sig, expected_val in exp.check.items():
                    actual_raw = engine.signals.get(sig, 0)
                    w = design.signals.get(sig)
                    w_bits = w.width if w else 1
                    # §18.7: 4値比較（x/z/? を含む期待値対応）
                    passed = _compare_signal_value(actual_raw, expected_val, w_bits)
                    st = "PASS" if passed else "FAIL"
                    if not passed:
                        overall_status = "FAIL"
                    # §15.7 name format
                    if off == 0:
                        name = f"{sig}@cycle{cycle_num}[spec]"
                    else:
                        name = f"{sig}@cycle{cycle_num}+{off}ns[spec]"
                    checks.append(CheckInfo(
                        name=name, status=st, signal=sig,
                        cycle=cycle_num, time_ns=t_ns, offset_ns=off,
                        expected=val_to_4state(expected_val, w_bits),
                        actual=val_to_4state(actual_raw, w_bits),
                        source="spec", seed=eff_seed,
                        message=None if passed else
                                f"expected {expected_val}, got {actual_raw}",
                    ))
            elif exp.expr:
                ns_ctx = dict(engine.signals); ns_ctx["_cycle"] = cycle_num
                try:
                    from deltasim.interp.expr import eval_bool as _eval_bool_sv
                    passed = _eval_bool_sv(exp.expr, ns_ctx)
                except Exception:
                    passed = False
                st = "PASS" if passed else "FAIL"
                if not passed:
                    overall_status = "FAIL"
                name_e = (f"expr@cycle{cycle_num}[spec]" if off == 0
                          else f"expr@cycle{cycle_num}+{off}ns[spec]")
                checks.append(CheckInfo(
                    name=name_e, status=st, signal="<expr>",
                    cycle=cycle_num, time_ns=t_ns, offset_ns=off,
                    expected=str(exp.expr), actual="",
                    source="spec", seed=eff_seed,
                    message=None if passed else f"expression failed: {exp.expr}",
                ))

    # ---------------------------------------------------------------
    # §15.3: Main simulation loop
    # ---------------------------------------------------------------
    try:
        engine._eval_comb_loop()  # initial combinational settle
        for cycle_idx in range(eff_cycles):
            if cancel and cancel.is_cancelled:
                raise SimulationCancelled()

            N = cycle_idx + 1
            t0_ns = cycle_idx * period_ns + hp   # posedge time (t = (N-1)*T + T/2)

            # §15.5: Release reset ONCE (at start of cycle reset_cycles+1)
            if engine._rst is not None and cycle_idx == engine.reset_cycles:
                rst_rel_val = 1 if engine._rst_active_low else 0
                apply_stimulus({engine._rst: rst_rel_val})

            # Collect all offsets for this cycle (posedge, negedge, + any stim/exp)
            stim_offs = stim.offsets_for_cycle(N)
            exp_offs = exp_offsets_for_cycle(N)
            all_offs = sorted({0, hp} | stim_offs | exp_offs)

            for off in all_offs:
                engine._time = t0_ns + off

                # §15.3.2: Apply stimuli for (N, off)
                vals = stim.get(N, off)
                if vals:
                    apply_stimulus(vals)

                # §15.3.4: Clock edge
                if off == 0 and engine._clk:
                    drive_clock(1)    # posedge
                elif off == hp and engine._clk:
                    drive_clock(0)    # negedge
                    # §18.5: $monitor check after negedge (full time step done)
                    from deltasim.sim.engine import _engine_check_monitor as _ecm
                    _ecm(engine)

                # §15.8: Record VCD and signal-change DB at this time
                record_vcd_and_db(engine._time)

                # §15.3.5: Check expects
                do_checks(N, off)

                # §15.3.6: Reference model (posedge only, offset_ns=0)
                if off == 0 and model:
                    inp = {nm: engine.signals.get(nm, 0)
                           for nm, info in design.signals.items()
                           if info.direction == "input"}
                    try:
                        exp_out = model.step(inp)
                    except Exception:
                        exp_out = {}
                    for sig, expected_val in exp_out.items():
                        if sig not in engine.signals:
                            continue
                        actual_raw = engine.signals.get(sig, 0)
                        w = design.signals.get(sig)
                        w_bits = w.width if w else 1
                        passed = (actual_raw == expected_val)
                        st = "PASS" if passed else "FAIL"
                        if not passed:
                            overall_status = "FAIL"
                        checks.append(CheckInfo(
                            name=f"{sig}@cycle{N}[model]",
                            status=st, signal=sig,
                            cycle=N, time_ns=t0_ns, offset_ns=0,
                            expected=val_to_4state(expected_val, w_bits),
                            actual=val_to_4state(actual_raw, w_bits),
                            source="model", seed=eff_seed,
                            message=None if passed else
                                    f"model: expected {expected_val}, got {actual_raw}",
                        ))

            engine._result.history.append(dict(engine.signals))
            _emit_fn = lambda type_, **kw: (
                on_progress(ProgressEvent(type=type_, **kw).to_dict())
                if on_progress else None
            )
            if on_progress:
                on_progress(ProgressEvent(
                    type="progress", phase="sim",
                    cycle=N, total_cycles=eff_cycles,
                    time_ns=engine._time,
                ).to_dict())

    except SimulationCancelled:
        cr.cancelled = True
        if not checks:
            overall_status = "SKIP"
            cr.log.append("cancelled")
    except DeltaLimitError as e:
        overall_status = "ERROR"
        err = _exc_to_error(e, "E_DELTA_LIMIT")
        cr.errors.append(err)
    except LoopLimitError as e:
        overall_status = "ERROR"
        err = _exc_to_error(e, "E_LOOP_LIMIT")
        cr.errors.append(err)
    except Exception as e:
        overall_status = "ERROR"
        err = _exc_to_error(e, "E005")
        cr.errors.append(err)
    finally:
        if vcd_obj:
            vcd_obj.close()

    if cr.cancelled and overall_status not in ("ERROR",):
        final_status = "FAIL" if any(c.status == "FAIL" for c in checks) else overall_status
    else:
        final_status = overall_status

    # §14.3 message
    if cr.cancelled and final_status == "SKIP":
        cr.message = "cancelled"
    elif final_status == "ERROR" and cr.errors:
        cr.message = cr.errors[0].message

    cr.status = final_status
    cr.checks = checks
    cr.log = engine._result.log if engine._result else []
    cr.truncated_at = change_db.truncated_at
    cr.elapsed_ms = (time.time() - case_t0) * 1000
    return cr


def run(
    files: FilesOrDesign,
    *,
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    cycles: int = 100,
    vcd: Optional[Union[str, Path]] = None,
    reset_cycles: int = 10,
    half_period: int = 5,
    keep_changes: bool = False,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel: Optional[CancelToken] = None,
    max_delta: Optional[int] = None,
    max_loop: Optional[int] = None,
) -> RunResult:
    """Run a bare simulation without a spec file.

    Parameters
    ----------
    files:
        RTL .sv file paths *or* a DesignInfo.
    keep_changes:
        Record every signal change in a SignalChangeDB accessible via
        RunResult.get_signal_changes().
    """
    from deltasim.sim.engine import (SimEngine, SimError, DeltaLimitError, LoopLimitError,
                                     MAX_DELTA as _DEF_MAX_DELTA,
                                     MAX_FOREVER_TICKS as _DEF_MAX_LOOP)

    t0 = time.time()
    params = params or {}
    result = RunResult(schema_version=SCHEMA_VERSION)

    def _emit(type_: str, **kw):
        if on_progress:
            on_progress(ProgressEvent(type=type_, **kw).to_dict())

    _emit("start", phase="parse")

    di = load(files, top=top, params=params)
    if di.status != "PASS":
        result.status = "ERROR"
        result.errors = di.errors
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit("finish", status="ERROR", elapsed_ms=result.elapsed_ms)
        return result

    _emit("progress", phase="sim", cycle=0, total_cycles=cycles, time_ns=0)
    result.module = di.module
    design = di._design

    _eff_max_delta = max_delta if max_delta is not None else _DEF_MAX_DELTA
    _eff_max_loop  = max_loop  if max_loop  is not None else _DEF_MAX_LOOP
    engine = SimEngine(design, half_period_ns=half_period, reset_cycles=reset_cycles,
                       max_delta=_eff_max_delta, max_loop=_eff_max_loop)
    change_db = SignalChangeDB() if keep_changes else None

    vcd_obj = None
    if vcd:
        from deltasim.vcd.writer import VCDWriter
        vcd_obj = VCDWriter(str(vcd), design)
        vcd_obj.record_initial(0, engine.signals)
        engine.vcd_writer = vcd_obj

    PROGRESS_INTERVAL = max(1, cycles // 20)

    def _post(cycle_num: int, snap: dict):
        if cancel and cancel.is_cancelled:
            raise SimulationCancelled()
        if change_db:
            t_ns = engine._time
            for sname, sinfo in design.signals.items():
                change_db.record(t_ns, sname, snap.get(sname), sinfo.width)
        if cycle_num % PROGRESS_INTERVAL == 0:
            _emit("progress", phase="sim", cycle=cycle_num,
                  total_cycles=cycles, time_ns=engine._time)

    try:
        sim_result = engine.run(cycles, post_posedge_hook=_post)
        result.log = sim_result.log
    except SimulationCancelled:
        result.cancelled = True
        result.log = engine._result.log if engine._result else []
    except DeltaLimitError as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E_DELTA_LIMIT"))
    except LoopLimitError as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E_LOOP_LIMIT"))
    except SimError as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E005"))
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E_INTERNAL"))
    finally:
        if vcd_obj:
            vcd_obj.close()

    result.cycles = cycles
    result.time_ns = engine._time
    result.vcd_path = str(vcd) if vcd else None
    if change_db:
        result._change_db = change_db
    result.elapsed_ms = (time.time() - t0) * 1000

    _emit("finish", status=result.status,
          elapsed_ms=result.elapsed_ms, cancelled=result.cancelled)
    return result


# ---------------------------------------------------------------------------
# Simulator (§2.5 — 対話デバッグ用)
# ---------------------------------------------------------------------------

class Simulator:
    """Interactive simulator for step-by-step debugging (§2.5).

    Parameters
    ----------
    design_or_files:
        DesignInfo from load() *or* a list of .sv file paths.
    top, params:
        Passed to load() if files are given.
    half_period, reset_cycles:
        Clock and reset configuration.
    keep_changes:
        If True, record every signal change in the internal SignalChangeDB.

    Examples
    --------
    >>> sim = Simulator(["counter.sv"], top="counter")
    >>> snap = sim.step(10)          # advance 10 cycles
    >>> sim.get("count")             # read signal
    >>> sim.set("rst_n", 1)          # drive signal
    >>> sim.add_breakpoint("count == 5")
    >>> snap = sim.run_until(200)    # run to 200 ns
    """

    def __init__(
        self,
        design_or_files: FilesOrDesign,
        top: Optional[str] = None,
        params: Optional[Dict[str, int]] = None,
        half_period: int = 5,
        reset_cycles: int = 10,
        keep_changes: bool = True,
    ):
        from deltasim.sim.engine import SimEngine

        di = (design_or_files if isinstance(design_or_files, DesignInfo)
              else load(design_or_files, top=top, params=params))
        if di.status != "PASS":
            raise ValueError(f"Elaboration failed: {di.errors}")

        self._di = di
        self._design = di._design
        self._engine = SimEngine(
            self._design,
            half_period_ns=half_period,
            reset_cycles=reset_cycles,
        )
        self._engine._result.history.append(dict(self._engine.signals))
        self._engine._eval_comb_loop()

        self._cycle: int = 0
        self._forced: Dict[str, int] = {}
        self._breakpoints: Dict[int, str] = {}
        self._next_bp_id: int = 0
        self._change_db: SignalChangeDB = SignalChangeDB()
        self._keep_changes: bool = keep_changes
        self._prev_signals: Dict[str, Optional[int]] = dict(self._engine.signals)
        self._pending_negedge_for: int = -1  # §18: cycle_idx of pending negedge (-1 = none)

    # ------------------------------------------------------------------
    # Core step operations
    # ------------------------------------------------------------------

    def step(self, n_cycles: int = 1) -> SimSnapshot:
        """Advance the simulation by *n_cycles* clock cycles.

        Returns a SimSnapshot with the signal state and any changes.
        Stops early if a breakpoint fires.
        """
        # Ensure initial scheduler is set up (needed for initial blocks)
        if self._engine._initial_scheduler is None:
            from deltasim.sim.engine import _InitialScheduler
            self._engine._initial_scheduler = _InitialScheduler()
            for ib in getattr(self._design, "initial_blocks", []):
                self._engine._initial_scheduler.add(ib, self._engine)

        changes_acc: list = []
        bp_hit: Optional[int] = None

        # §M6: engine._force_overrides already set by force() —
        # no extra pre_posedge_hook needed; _write() ignores RTL updates.

        for _ in range(max(1, n_cycles)):
            # Run one engine cycle
            self._engine._run_cycle(self._cycle)
            self._cycle += 1

            # Collect changes
            cur = self._engine.signals
            for sname, sinfo in self._design.signals.items():
                new_val = cur.get(sname)
                old_val = self._prev_signals.get(sname)
                if new_val != old_val:
                    changes_acc.append((sname, new_val))
                    if self._keep_changes:
                        self._change_db.record(
                            self._engine._time, sname, new_val, sinfo.width
                        )
                    self._prev_signals[sname] = new_val

            # Check breakpoints
            ns = dict(cur)
            for bp_id, cond in list(self._breakpoints.items()):
                try:
                    from deltasim.interp.expr import eval_bool as _eval_bool_sv
                    if _eval_bool_sv(cond, ns):
                        bp_hit = bp_id
                        break
                except Exception:
                    pass
            if bp_hit is not None:
                break

        return SimSnapshot(
            time_ns=self._engine._time,
            signals=dict(self._engine.signals),
            changes=list(changes_acc),
            breakpoint_hit=bp_hit,
        )

    def step_delta(self) -> SimSnapshot:
        """Evaluate one combinational delta cycle.

        Useful for inspecting intermediate states within a clock cycle.
        """
        prev = dict(self._engine.signals)
        self._engine._eval_comb_loop()
        cur = self._engine.signals
        changes = [(k, cur[k]) for k in cur if cur.get(k) != prev.get(k)]
        return SimSnapshot(
            time_ns=self._engine._time,
            signals=dict(cur),
            changes=changes,
        )

    def run_until(self, time_ns: int) -> SimSnapshot:
        """Run the simulation until *time_ns* (approximate, clock-cycle granularity).
        For exact ns stopping use step_to_ns().
        """
        return self.step_to_ns(time_ns)

    def step_to_ns(self, target_ns: int, bp_check=None) -> SimSnapshot:
        """Advance simulation to exactly *target_ns* (arbitrary ns precision).

        §18.1.1 revised design:
        - Stops at posedge / negedge that equals target_ns.
        - If target_ns is between clock edges, sets _time directly (no logic runs).
        - Uses deferred breakpoints: bp_check stores hit in engine._deferred_bp_hit
          so the full time step (NBA + negedge + VCD) always completes.
        - After each time step, checks for deferred bp hit and raises BreakpointHit.
        - Handles pending negedge (if previous call stopped after posedge only).
        """
        from deltasim.sim.engine import BreakpointHit as _BPHit

        hp = self._engine.half_period
        changes_acc: list = []

        def _collect_changes():
            cur = self._engine.signals
            for sname in self._design.signals:
                nv = cur.get(sname)
                ov = self._prev_signals.get(sname)
                if nv != ov:
                    changes_acc.append((sname, nv))
                    self._prev_signals[sname] = nv

        def _check_deferred_bp():
            hit = self._engine._deferred_bp_hit
            if hit:
                self._engine._deferred_bp_hit = None
                raise _BPHit(hit[0], hit[1], hit[2], hit[3])

        # Setup deferred bp_check (stores hit instead of raising)
        if bp_check is not None:
            engine = self._engine
            original_bp_check = bp_check

            def _deferred_check(sf, sl):
                if engine._deferred_bp_hit is None:
                    original_bp_check(sf, sl)  # this sets engine._deferred_bp_hit

            self._engine._active_bp_check = _deferred_check
        else:
            self._engine._active_bp_check = None

        try:
            # 1. Complete any pending negedge from a previous posedge-only run
            if self._pending_negedge_for >= 0:
                cycle_idx = self._pending_negedge_for
                negedge_time = (cycle_idx + 1) * 2 * hp
                if negedge_time <= target_ns:
                    self._engine._run_cycle(cycle_idx, negedge_only=True)
                    self._cycle += 1
                    self._pending_negedge_for = -1
                    _collect_changes()
                    _check_deferred_bp()
                    if self._engine._time >= target_ns:
                        return SimSnapshot(time_ns=self._engine._time,
                                           signals=dict(self._engine.signals),
                                           changes=list(changes_acc))
                elif target_ns < negedge_time:
                    # Target is between posedge and negedge — just advance time
                    self._engine._time = target_ns
                    return SimSnapshot(time_ns=target_ns,
                                       signals=dict(self._engine.signals),
                                       changes=list(changes_acc))

            # 2. Run cycles until we reach target_ns
            while self._engine._time < target_ns:
                cycle_idx = self._cycle
                posedge_time = cycle_idx * 2 * hp + hp
                negedge_time = (cycle_idx + 1) * 2 * hp

                if posedge_time > target_ns:
                    # No clock edge before target → advance time (no logic)
                    self._engine._time = target_ns
                    break

                if posedge_time == target_ns:
                    # Run posedge only, set pending negedge
                    self._engine._run_cycle(cycle_idx, posedge_only=True)
                    self._pending_negedge_for = cycle_idx
                    _collect_changes()
                    _check_deferred_bp()
                    break

                # posedge_time < target_ns
                if negedge_time <= target_ns:
                    # Run full cycle
                    self._engine._run_cycle(cycle_idx)
                    self._cycle += 1
                    _collect_changes()
                    _check_deferred_bp()
                    if self._engine._time >= target_ns:
                        break
                else:
                    # negedge_time > target_ns: run posedge then advance time
                    self._engine._run_cycle(cycle_idx, posedge_only=True)
                    self._pending_negedge_for = cycle_idx
                    # Advance time beyond posedge to the exact target
                    self._engine._time = target_ns
                    _collect_changes()
                    _check_deferred_bp()
                    break

        finally:
            self._engine._active_bp_check = None

        return SimSnapshot(
            time_ns=self._engine._time,
            signals=dict(self._engine.signals),
            changes=list(changes_acc),
            breakpoint_hit=None,
        )

    # ------------------------------------------------------------------
    # Signal access
    # ------------------------------------------------------------------

    def get(self, signal: str) -> Optional[int]:
        """Get current value of *signal* (None = X/Z)."""
        return self._engine.signals.get(signal)

    def set(self, signal: str, value: Optional[int]) -> None:
        """Drive *signal* to *value* and re-evaluate combinational logic.

        The signal is only driven once; use force() to persist across cycles.
        """
        self._engine._write(signal, value if value is not None else 0)
        self._engine._eval_comb_loop()

    def force(self, signal: str, value: Optional[int]) -> None:
        """Force *signal* to *value* persistently across clock edges.

        §M6: Uses engine._force_overrides so the value is preserved even
        after NBA updates (NBA to forced signals are silently discarded).
        """
        v = value if value is not None else 0
        self._forced[signal] = v
        # Register in engine so _write() ignores RTL-driven updates
        self._engine._force_overrides[signal] = v
        # Apply immediately, bypassing override (write directly)
        from deltasim.sim.engine import _write_signal
        _write_signal(self._engine.signals, signal, v, self._engine._widths)
        self._engine._eval_comb_loop()

    def release(self, signal: str) -> None:
        """Remove a previously forced value from *signal*."""
        self._forced.pop(signal, None)
        self._engine._force_overrides.pop(signal, None)

    # ------------------------------------------------------------------
    # Signal listing  (§2.5)
    # ------------------------------------------------------------------

    def list_signals(self) -> List[dict]:
        """List all signals with hierarchy paths, widths, and directions.

        Returns canonical signals + sub-instance aliases (port-connected signals
        are also listed under their sub-instance hierarchy path).
        Keys: name, path, width, direction, signed, value (4-state string)
        """
        result = []
        # Canonical signals
        for sname, sinfo in self._design.signals.items():
            val = self._engine.signals.get(sname)
            result.append({
                "name": sname,
                "path": sname,
                "width": sinfo.width,
                "direction": sinfo.direction,
                "signed": sinfo.signed,
                "value": val_to_4state(val, sinfo.width),
            })
        # Alias paths (port-connected signals in sub-scopes)
        for alias_path, canonical in self._design.signal_aliases.items():
            canon_info = self._design.signals.get(canonical)
            if canon_info is None:
                continue
            val = self._engine.signals.get(canonical)
            result.append({
                "name": alias_path,
                "path": alias_path,
                "width": canon_info.width,
                "direction": canon_info.direction,
                "signed": canon_info.signed,
                "value": val_to_4state(val, canon_info.width),
                "alias_of": canonical,
            })
        return result

    # ------------------------------------------------------------------
    # Breakpoints
    # ------------------------------------------------------------------

    def add_breakpoint(self, condition: str) -> int:
        """Add a conditional breakpoint.

        *condition* is a Python expression evaluated against the current
        signal dict.  Returns the breakpoint ID.

        Example::

            bp_id = sim.add_breakpoint("count == 10")
        """
        bp_id = self._next_bp_id
        self._breakpoints[bp_id] = condition
        self._next_bp_id += 1
        return bp_id

    def remove_breakpoint(self, bp_id: int) -> None:
        """Remove the breakpoint with *bp_id*."""
        self._breakpoints.pop(bp_id, None)

    def list_breakpoints(self) -> List[dict]:
        """List all active breakpoints."""
        return [{"id": k, "condition": v} for k, v in self._breakpoints.items()]

    def list_signals_dict(self) -> Dict[str, dict]:
        """Return signal info as {name: info_dict} for quick lookup."""
        return {s["name"]: s for s in self.list_signals()}

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset simulation to the initial state."""
        from deltasim.sim.engine import SimEngine
        self._engine = SimEngine(
            self._design,
            half_period_ns=self._engine.half_period,
            reset_cycles=self._engine.reset_cycles,
        )
        self._engine._eval_comb_loop()
        self._cycle = 0
        self._pending_negedge_for = -1
        self._prev_signals = dict(self._engine.signals)
        self._forced.clear()
        if self._keep_changes:
            from deltasim.result import SignalChangeDB
            self._change_db = SignalChangeDB()

    def snapshot(self) -> Dict[str, Optional[int]]:
        """Return a copy of the current signal state dict."""
        return dict(self._engine.signals)

    # ------------------------------------------------------------------
    # Signal changes (§2.6)
    # ------------------------------------------------------------------

    def get_signal_changes(
        self, signal: str, start_ns: int = 0, end_ns: Optional[int] = None
    ) -> list:
        """Get recorded value changes for *signal* within a time range.

        Accepts both canonical names and alias paths (sub-instance hierarchy).
        """
        # Resolve alias to canonical name if needed
        canonical = self._design.signal_aliases.get(signal, signal)
        return self._change_db.get(canonical, start_ns, end_ns)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def time_ns(self) -> int:
        """Current simulation time in ns."""
        return self._engine._time

    @property
    def cycle(self) -> int:
        """Current clock cycle count."""
        return self._cycle


# ---------------------------------------------------------------------------
# test_events() — generator interface for progress events
# ---------------------------------------------------------------------------

def test_events(
    files: FilesOrDesign,
    spec: Union[str, Path],
    *,
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    seed: Optional[int] = None,
    vcd: Optional[Union[str, Path]] = None,
    cancel: Optional[CancelToken] = None,
) -> Generator[dict, None, TestResult]:
    """Like test() but yields progress event dicts as they happen.

    Usage::

        for event in test_events(["counter.sv"], "spec.yaml"):
            print(event)   # {"type": "progress", ...}
        # After the loop, access the final result via .close() or catch StopIteration
    """
    import queue, threading

    q: "queue.Queue[Optional[dict]]" = queue.Queue()
    result_box: List[Optional[TestResult]] = [None]

    def _run():
        def _cb(ev):
            q.put(ev)

        result_box[0] = test(files, spec, top=top, params=params,
                             seed=seed, vcd=vcd,
                             on_progress=_cb, cancel=cancel)
        q.put(None)  # sentinel

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev

    t.join()
    return result_box[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compare_signal_value(actual, expected_val, width: int) -> bool:
    """§18.7: 4値対応シグナル比較。

    - expected_val が str ("4'b1x0z", "?" を含む) なら FourStatePattern で比較。
    - expected_val が int なら単純比較（x/z を含む actual は FAIL）。
    """
    from deltasim.fourstate import parse_expected_value, coerce, FourState
    from deltasim.result import Z_VALUE

    pattern = None
    if isinstance(expected_val, str):
        pattern = parse_expected_value(expected_val)
        if pattern is None:
            # §18.7.1: YAML バリデーション済みのはずだが念のため
            raise ValueError(
                f"期待値 {expected_val!r} は不正な SV リテラルです。"
                "基数つきリテラル（\"4'b1x0z\"、\"'bx\" 等）を使ってください。"
            )

    if pattern is not None:
        # 4値パターン比較
        if isinstance(actual, FourState):
            actual_fs = actual
        elif actual is None:
            from deltasim.fourstate import from_x
            actual_fs = from_x(width)
        elif actual is Z_VALUE:
            from deltasim.fourstate import from_z
            actual_fs = from_z(width)
        else:
            from deltasim.fourstate import from_int
            actual_fs = from_int(int(actual) & ((1 << width) - 1), width)
        return pattern.matches(actual_fs)
    else:
        # 従来の整数比較: actual に x/z があれば FAIL
        if isinstance(actual, FourState):
            if actual.bval != 0:
                return False  # x/z を含む実値は整数期待値に FAIL
            return actual.aval == expected_val
        if actual is None or actual is Z_VALUE:
            return False
        return actual == expected_val


def _build_record_set(design, scenario=None):
    """§12.3: Build the default signal record set.

    Records only top-level ports + signals referenced in the spec's expected checks.
    """
    # Top ports
    ports = {n for n, info in design.signals.items() if info.direction is not None}
    # Spec-referenced signals
    spec_refs: set = set()
    if scenario:
        for exp in scenario.expected:
            if exp.check:
                spec_refs.update(exp.check.keys())
    return ports | spec_refs


def _parse_files(files):
    from deltasim.parser.parser import parse_file
    mods = []
    cat = {}
    for f in files:
        mod = parse_file(str(f))
        mods.append(mod)
        cat[mod.name] = mod
    return mods, cat


def find_top_candidates(mods: list, catalog: dict = None) -> list:
    """§18.5: どこからもインスタンスされていないモジュール名のリストを返す。

    GUI から使う公開 API。catalog は将来の拡張用（現在は未使用）。
    generate ブロック内のインスタンスも含めて検索する。

    Returns:
        インスタンスされていないモジュール名のリスト（候補）
    """
    from deltasim.parser.ast_nodes import InstDecl, GenerateBlock

    all_names = {m.name for m in mods}
    if not all_names:
        return []

    def _collect(items, found):
        for item in items:
            if isinstance(item, InstDecl):
                found.add(item.module_name)
            elif isinstance(item, GenerateBlock):
                _collect(item.items, found)
            for attr in ("then_items", "else_items", "body_items"):
                sub = getattr(item, attr, None)
                if isinstance(sub, list):
                    _collect(sub, found)

    instanced = set()
    for m in mods:
        _collect(m.items, instanced)
    return sorted(all_names - instanced)


def _select_top(mods, top, catalog=None):
    if not mods:
        return None, None
    if top is not None:
        for m in mods:
            if m.name == top:
                return m, None
        return None, ErrorInfo(
            message=f"Top module {top!r} not found in {sorted(m.name for m in mods)}",
            code="E002",
        )
    # §18.5: どこからもインスタンスされていないモジュールを候補にする
    candidates = find_top_candidates(mods, catalog)
    if not candidates:
        # フォールバック: 先頭
        return mods[0], None
    if len(candidates) == 1:
        for m in mods:
            if m.name == candidates[0]:
                return m, None
        return mods[0], None
    # 複数候補 → E_AMBIGUOUS_TOP
    msg = (
        f"トップモジュールが一意に決まりません。"
        f"候補: {candidates}。"
        f"--top で明示してください。"
    )
    return None, ErrorInfo(message=msg, code="E_AMBIGUOUS_TOP")


def _exc_to_error(exc: Exception, code: str, severity: str = "error") -> ErrorInfo:
    import re
    msg = str(exc)
    file_ = ""
    line = 0
    col = 0
    m = re.match(r"^(.+?):(\d+):(\d+): (.+)$", msg)
    if m:
        file_ = m.group(1)
        line = int(m.group(2))
        col = int(m.group(3))
        msg = m.group(4)
    return ErrorInfo(file=file_, line=line, column=col,
                     severity=severity, message=msg, code=code)


def _build_hierarchy(top_name: str, design) -> HierarchyNode:
    """Build a simple hierarchy node from a flat elaborated design."""
    # Separate top-level signals from instance-prefixed signals
    top_signals = []
    children_map: Dict[str, List[str]] = {}

    for sname in design.signals:
        if "." in sname:
            inst = sname.split(".")[0]
            children_map.setdefault(inst, []).append(sname)
        else:
            top_signals.append(sname)

    children = [
        HierarchyNode(name=inst, module=inst, signals=sigs)
        for inst, sigs in children_map.items()
    ]
    return HierarchyNode(name=top_name, module=top_name,
                         signals=top_signals, children=children)
