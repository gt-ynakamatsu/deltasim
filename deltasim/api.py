"""deltasim public Python API — M5 contract.

This module exposes four top-level functions that are the sole interface
between the simulation core and any higher-level consumer (GUI, CI scripts,
Jupyter notebooks, etc.).  Every function is also reachable through the
``deltasim`` CLI with equivalent semantics.

Interface contract: docs/api.md  (deltasim GUI spec §2)

Functions
---------
load(files, top, params, catalog)
    Parse + elaborate one or more SV files.  Returns :class:`LoadResult`.

gen(files, top, params, out_dir)
    Generate testbench SV + YAML skeleton.  Returns :class:`GenResult`.

test(files, spec, *, top, params, seed, vcd, on_progress, cancel)
    Run simulation from a YAML spec and judge results.  Returns
    :class:`TestResult`.

run(files, *, top, params, cycles, vcd, reset_cycles, half_period,
    keep_changes, on_progress, cancel)
    Run simulation and return waveform / log data.  Returns
    :class:`RunResult`.

Error handling
--------------
All functions return their result object even on error; check
``result.status == "ERROR"`` and ``result.errors`` for details.
They never raise unless there is a programming error in the caller.
"""

from __future__ import annotations

import time
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from deltasim.result import (
    SCHEMA_VERSION,
    CheckInfo,
    ErrorInfo,
    LoadResult,
    ProgressEvent,
    RunResult,
    SignalChangeDB,
    SimulationCancelled,
    TestResult,
    CancelToken,
    val_to_4state,
)


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------

def load(
    files: List[Union[str, Path]],
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    catalog: Optional[Dict] = None,
) -> LoadResult:
    """Parse and elaborate SV files.

    Parameters
    ----------
    files:
        List of ``.sv`` file paths.
    top:
        Top module name.  Auto-detected from first file if *None*.
    params:
        Parameter overrides, e.g. ``{"WIDTH": 4}``.
    catalog:
        Pre-built module catalog (avoids re-parsing shared libraries).

    Returns
    -------
    LoadResult
        Always returned; check ``.status`` and ``.errors``.
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError

    params = params or {}
    result = LoadResult(schema_version=SCHEMA_VERSION)

    try:
        mods, cat = _parse_files(files)
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E001"))
        return result

    if catalog:
        cat.update(catalog)

    mod = _select_top(mods, top)
    if mod is None:
        result.status = "ERROR"
        result.errors.append(ErrorInfo(
            message=f"Top module {top!r} not found in {[m.name for m in mods]}",
            code="E002",
        ))
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
        {
            "name": name,
            "direction": info.direction,
            "width": info.width,
            "signed": info.signed,
        }
        for name, info in design.signals.items()
        if info.direction is not None
    ]
    result.signals = [
        {"name": name, "width": info.width, "signed": info.signed}
        for name, info in design.signals.items()
    ]
    return result


# ---------------------------------------------------------------------------
# gen()
# ---------------------------------------------------------------------------

def gen(
    files: List[Union[str, Path]],
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    out_dir: Union[str, Path] = ".",
    tb_path: Optional[Union[str, Path]] = None,
    spec_path: Optional[Union[str, Path]] = None,
) -> dict:
    """Generate testbench SV and YAML scenario skeleton.

    Returns a dict with keys ``tb_path`` and ``spec_path`` on success,
    or ``status: ERROR`` and ``errors`` list on failure.
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError
    from deltasim.tb.generator import generate_tb_sv, generate_yaml_spec

    params = params or {}
    out_dir = Path(out_dir)

    try:
        mods, cat = _parse_files(files)
        mod = _select_top(mods, top)
        if mod is None:
            return {"status": "ERROR", "errors": [
                ErrorInfo(message=f"Top module not found", code="E002").to_dict()
            ]}
        design = elaborate(mod, param_overrides=params, catalog=cat)
    except Exception as e:
        return {"status": "ERROR", "errors": [_exc_to_error(e, "E003").to_dict()]}

    top_name = design.module_name
    tb_out = Path(tb_path) if tb_path else out_dir / f"{top_name}_tb.sv"
    spec_out = Path(spec_path) if spec_path else out_dir / f"{top_name}_spec.yaml"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
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
    files: List[Union[str, Path]],
    spec: Union[str, Path],
    *,
    top: Optional[str] = None,
    params: Optional[Dict[str, int]] = None,
    seed: Optional[int] = None,
    vcd: Optional[Union[str, Path]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel: Optional[CancelToken] = None,
) -> TestResult:
    """Run simulation driven by a YAML scenario spec.

    Parameters
    ----------
    files:
        RTL ``.sv`` file paths.
    spec:
        YAML scenario file path.
    top:
        Top module name (overrides spec ``top:``).
    params:
        Parameter overrides (merged over spec defaults).
    seed:
        Random seed override.
    vcd:
        VCD output path.  Overrides ``sim.vcd`` in spec.
    on_progress:
        Callback receiving :meth:`ProgressEvent.to_dict` dicts.
    cancel:
        :class:`CancelToken` to interrupt the run.

    Returns
    -------
    TestResult
        Always returned.  If cancelled mid-run, ``cancelled=True`` and
        partial check results are included.
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError
    from deltasim.tb.scenario import load_scenario, ScenarioError
    from deltasim.tb.runner import TestRunner
    from deltasim.sim.engine import SimError

    t0 = time.time()
    params = params or {}
    result = TestResult(schema_version=SCHEMA_VERSION, seed=seed or 0)

    def _emit(ev: ProgressEvent):
        if on_progress:
            on_progress(ev.to_dict())

    _emit(ProgressEvent(type="start", phase="parse"))

    # Parse
    try:
        mods, cat = _parse_files(files)
        scenario = load_scenario(str(spec))
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E001"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit(ProgressEvent(type="finish", status="ERROR", elapsed_ms=result.elapsed_ms))
        return result

    _emit(ProgressEvent(type="progress", phase="elab"))

    # Elaborate
    top_name = top or scenario.top
    mod = _select_top(mods, top_name)
    if mod is None:
        result.status = "ERROR"
        result.errors.append(ErrorInfo(message=f"Top module {top_name!r} not found", code="E002"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit(ProgressEvent(type="finish", status="ERROR", elapsed_ms=result.elapsed_ms))
        return result

    try:
        design = elaborate(mod, param_overrides=params, catalog=cat)
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E003"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit(ProgressEvent(type="finish", status="ERROR", elapsed_ms=result.elapsed_ms))
        return result

    result.module = design.module_name
    if seed is not None:
        scenario.sim.seed = seed
    if vcd:
        scenario.sim.vcd = str(vcd)

    _emit(ProgressEvent(type="progress", phase="sim", cycle=0,
                        total_cycles=scenario.sim.cycles, time_ns=0))

    # Run
    from deltasim.sim.engine import SimEngine

    engine = SimEngine(design,
                       half_period_ns=scenario.clock.period_ns // 2,
                       reset_cycles=scenario.reset.duration_cycles)
    if scenario.clock.signal:
        engine.set_clk(scenario.clock.signal)
    if scenario.reset.signal:
        engine.set_rst(scenario.reset.signal, active_low=scenario.reset.active_low)

    from deltasim.tb.scenario import StimulusSchedule, _expand_at
    stim = StimulusSchedule(scenario.inputs, scenario.sim.cycles, seed=scenario.sim.seed)

    # Load model
    runner_obj = TestRunner(design, scenario)
    model = None
    if scenario.model:
        try:
            model = runner_obj._load_model()
        except Exception as e:
            result.errors.append(_exc_to_error(e, "W001", severity="warning"))

    # VCD
    vcd_obj = None
    if scenario.sim.vcd:
        from deltasim.vcd.writer import VCDWriter
        vcd_obj = VCDWriter(scenario.sim.vcd, design)
        vcd_obj.record_initial(0, engine.signals)
        engine.vcd_writer = vcd_obj

    # Model init
    if model and hasattr(model, "reset"):
        model.reset()

    change_db = SignalChangeDB()

    checks_out: List[CheckInfo] = []
    overall_status = "PASS"

    def _pre_hook(cycle_num: int, sigs: dict):
        if cancel and cancel.is_cancelled:
            raise SimulationCancelled()
        for sig, val in stim.get(cycle_num).items():
            engine._write(sig, val)

    def _post_hook(cycle_num: int, snap: dict):
        nonlocal overall_status
        time_ns = cycle_num * scenario.clock.period_ns

        # Record changes to DB
        for sname, sinfo in design.signals.items():
            val = snap.get(sname)
            change_db.record(time_ns, sname, val, sinfo.width)

        # Spec checks
        for exp in scenario.expected:
            cycles = _expand_at(exp.at, scenario.sim.cycles)
            if cycle_num not in cycles:
                continue
            if exp.check:
                for sig, expected_val in exp.check.items():
                    actual_raw = snap.get(sig, 0)
                    w = design.signals.get(sig, None)
                    w_bits = w.width if w else 1
                    passed = (actual_raw == expected_val)
                    status = "PASS" if passed else "FAIL"
                    if not passed:
                        overall_status = "FAIL"
                    ci = CheckInfo(
                        name=f"{sig}@cycle{cycle_num}[spec]",
                        status=status,
                        signal=sig,
                        cycle=cycle_num,
                        time_ns=time_ns,
                        expected=val_to_4state(expected_val, w_bits),
                        actual=val_to_4state(actual_raw, w_bits),
                        source="spec",
                        seed=scenario.sim.seed,
                        message=None if passed else f"expected {expected_val}, got {actual_raw}",
                    )
                    checks_out.append(ci)
            elif exp.expr:
                ns = dict(snap)
                ns["_cycle"] = cycle_num
                try:
                    passed = bool(eval(exp.expr, {"__builtins__": {}}, ns))
                except Exception as exc:
                    passed = False
                status = "PASS" if passed else "FAIL"
                if not passed:
                    overall_status = "FAIL"
                checks_out.append(CheckInfo(
                    name=f"expr@cycle{cycle_num}[spec]",
                    status=status,
                    signal="<expr>",
                    cycle=cycle_num,
                    time_ns=time_ns,
                    expected=str(exp.expr),
                    actual="",
                    source="spec",
                    seed=scenario.sim.seed,
                ))

        # Model checks
        if model:
            input_vals = {
                n: engine.signals.get(n, 0)
                for n, info in design.signals.items()
                if info.direction == "input"
            }
            try:
                exp_out = model.step(input_vals)
            except Exception:
                exp_out = {}
            for sig, expected_val in exp_out.items():
                if sig not in snap:
                    continue
                actual_raw = snap.get(sig, 0)
                w = design.signals.get(sig, None)
                w_bits = w.width if w else 1
                passed = (actual_raw == expected_val)
                status = "PASS" if passed else "FAIL"
                if not passed:
                    overall_status = "FAIL"
                checks_out.append(CheckInfo(
                    name=f"{sig}@cycle{cycle_num}[model]",
                    status=status,
                    signal=sig,
                    cycle=cycle_num,
                    time_ns=time_ns,
                    expected=val_to_4state(expected_val, w_bits),
                    actual=val_to_4state(actual_raw, w_bits),
                    source="model",
                    seed=scenario.sim.seed,
                    message=None if passed else f"expected {expected_val}, got {actual_raw}",
                ))

        # Emit progress
        _emit(ProgressEvent(type="progress", phase="sim",
                            cycle=cycle_num,
                            total_cycles=scenario.sim.cycles,
                            time_ns=time_ns))

    try:
        engine.run(
            scenario.sim.cycles,
            pre_posedge_hook=_pre_hook,
            post_posedge_hook=_post_hook,
        )
    except SimulationCancelled:
        result.cancelled = True
        overall_status = "FAIL" if any(c.status == "FAIL" for c in checks_out) else overall_status
    except Exception as e:
        overall_status = "ERROR"
        result.errors.append(_exc_to_error(e, "E005"))
    finally:
        if vcd_obj:
            vcd_obj.close()

    result.status = overall_status if not result.cancelled else (
        "FAIL" if any(c.status == "FAIL" for c in checks_out) else "PASS"
    )
    result.checks = checks_out
    result.log = engine._result.log if engine._result else []
    result.elapsed_ms = (time.time() - t0) * 1000
    result.seed = scenario.sim.seed

    _emit(ProgressEvent(type="finish", status=result.status,
                        elapsed_ms=result.elapsed_ms,
                        cancelled=result.cancelled))
    return result


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

def run(
    files: List[Union[str, Path]],
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
) -> RunResult:
    """Run a bare simulation without a spec file.

    Parameters
    ----------
    files:
        RTL ``.sv`` file paths.
    top, params:
        Top module and parameter overrides.
    cycles:
        Number of clock cycles to simulate.
    vcd:
        VCD output file path.
    reset_cycles:
        Cycles to hold reset (0 = no reset management).
    half_period:
        Clock half-period in ns.
    keep_changes:
        If True, record every signal change in a :class:`SignalChangeDB`.
    on_progress:
        Progress callback.
    cancel:
        :class:`CancelToken`.
    """
    from deltasim.parser.parser import parse_file, ParseError
    from deltasim.elab.elaborator import elaborate, ElabError
    from deltasim.sim.engine import SimEngine, SimError

    t0 = time.time()
    params = params or {}
    result = RunResult(schema_version=SCHEMA_VERSION)

    def _emit(ev: ProgressEvent):
        if on_progress:
            on_progress(ev.to_dict())

    _emit(ProgressEvent(type="start", phase="parse"))

    try:
        mods, cat = _parse_files(files)
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E001"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit(ProgressEvent(type="finish", status="ERROR", elapsed_ms=result.elapsed_ms))
        return result

    _emit(ProgressEvent(type="progress", phase="elab"))

    mod = _select_top(mods, top)
    if mod is None:
        result.status = "ERROR"
        result.errors.append(ErrorInfo(message=f"Top module {top!r} not found", code="E002"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit(ProgressEvent(type="finish", status="ERROR", elapsed_ms=result.elapsed_ms))
        return result

    try:
        design = elaborate(mod, param_overrides=params, catalog=cat)
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E003"))
        result.elapsed_ms = (time.time() - t0) * 1000
        _emit(ProgressEvent(type="finish", status="ERROR", elapsed_ms=result.elapsed_ms))
        return result

    result.module = design.module_name
    _emit(ProgressEvent(type="progress", phase="sim", cycle=0, total_cycles=cycles, time_ns=0))

    engine = SimEngine(design, half_period_ns=half_period, reset_cycles=reset_cycles)

    change_db = SignalChangeDB() if keep_changes else None

    vcd_obj = None
    if vcd:
        from deltasim.vcd.writer import VCDWriter
        vcd_obj = VCDWriter(str(vcd), design)
        vcd_obj.record_initial(0, engine.signals)
        engine.vcd_writer = vcd_obj

    PROGRESS_INTERVAL = max(1, cycles // 20)

    def _post_hook(cycle_num: int, snap: dict):
        if cancel and cancel.is_cancelled:
            raise SimulationCancelled()
        if change_db:
            t_ns = cycle_num * (half_period * 2)
            for sname, sinfo in design.signals.items():
                change_db.record(t_ns, sname, snap.get(sname), sinfo.width)
        if cycle_num % PROGRESS_INTERVAL == 0:
            _emit(ProgressEvent(type="progress", phase="sim",
                                cycle=cycle_num, total_cycles=cycles,
                                time_ns=engine._time))

    try:
        sim_result = engine.run(cycles, post_posedge_hook=_post_hook)
        result.log = sim_result.log
        if sim_result.finished:
            result.log.append("$finish")
    except SimulationCancelled:
        result.cancelled = True
        result.log = engine._result.log if engine._result else []
    except SimError as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E005"))
    except Exception as e:
        result.status = "ERROR"
        result.errors.append(_exc_to_error(e, "E005"))
    finally:
        if vcd_obj:
            vcd_obj.close()

    result.cycles = cycles
    result.time_ns = engine._time
    result.vcd_path = str(vcd) if vcd else None
    if change_db:
        result._change_db = change_db
    result.elapsed_ms = (time.time() - t0) * 1000

    _emit(ProgressEvent(type="finish", status=result.status,
                        elapsed_ms=result.elapsed_ms, cancelled=result.cancelled))
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_files(files):
    from deltasim.parser.parser import parse_file, ParseError
    mods = []
    cat = {}
    for f in files:
        mod = parse_file(str(f))
        mods.append(mod)
        cat[mod.name] = mod
    return mods, cat


def _select_top(mods, top):
    if not mods:
        return None
    if top is None:
        return mods[0]
    for m in mods:
        if m.name == top:
            return m
    return None


def _exc_to_error(exc: Exception, code: str, severity: str = "error") -> ErrorInfo:
    msg = str(exc)
    file_ = ""
    line = 0
    col = 0
    # Try to extract file/line from ParseError / ElabError messages
    # Format: "path:line:col: message"
    import re
    m = re.match(r"^(.+?):(\d+):(\d+): (.+)$", msg)
    if m:
        file_ = m.group(1)
        line = int(m.group(2))
        col = int(m.group(3))
        msg = m.group(4)
    return ErrorInfo(file=file_, line=line, column=col,
                     severity=severity, message=msg, code=code)
