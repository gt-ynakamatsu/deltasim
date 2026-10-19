"""Test runner: drives simulation from a Scenario and checks expected values.

Flow
----
1. Parse + elaborate RTL
2. Load scenario (YAML)
3. Build stimulus schedule
4. Load reference model (if specified)
5. Run engine with pre/post-posedge hooks:
   * pre:  apply inputs from schedule
   * post: check expected values + compare with model
6. Return TestRunResult with per-check pass/fail
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from deltasim.elab.elaborator import ElabDesign
from deltasim.sim.engine import SimEngine, SimResult
from deltasim.tb.scenario import Scenario, StimulusSchedule, _expand_at


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    cycle: int
    signal: str
    expected: object
    actual: int
    passed: bool
    source: str = "spec"   # 'spec' or 'model'

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return (f"[{status}] cycle={self.cycle} {self.signal}: "
                f"expected={self.expected!r}, actual={self.actual}")


@dataclass
class TestRunResult:
    module_name: str
    checks: List[CheckResult] = field(default_factory=list)
    log: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def n_pass(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def n_fail(self) -> int:
        return sum(1 for c in self.checks if not c.passed)


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

class TestRunner:
    """Runs a simulation driven by a :class:`~deltasim.tb.scenario.Scenario`.

    Parameters
    ----------
    design:
        Elaborated design.
    scenario:
        Loaded scenario (YAML spec).
    verbose:
        Print $display / check results inline.
    on_progress:
        Optional callback(dict) receiving progress events (M5 API §2.4).
    """

    def __init__(
        self,
        design: ElabDesign,
        scenario: Scenario,
        verbose: bool = False,
        on_progress: Optional[Callable] = None,
    ):
        self.design = design
        self.scenario = scenario
        self.verbose = verbose
        self._on_progress = on_progress

        hp = scenario.clock.period_ns // 2
        rc = scenario.reset.duration_cycles

        self.engine = SimEngine(
            design,
            half_period_ns=hp,
            reset_cycles=rc,
            verbose=verbose,
        )

        if scenario.clock.signal:
            self.engine.set_clk(scenario.clock.signal)
        if scenario.reset.signal:
            self.engine.set_rst(
                scenario.reset.signal,
                active_low=scenario.reset.active_low,
            )

    # ------------------------------------------------------------------

    def run(self) -> TestRunResult:
        sc = self.scenario
        engine = self.engine
        result = TestRunResult(module_name=self.design.module_name)

        def _emit(ev_type: str, **kwargs):
            if self._on_progress:
                import time as _time
                ev = {"type": ev_type, "timestamp_ms": _time.time() * 1000}
                ev.update(kwargs)
                self._on_progress(ev)

        _emit("start", phase="sim", module=self.design.module_name)

        stim = StimulusSchedule(
            sc.inputs, sc.sim.cycles, seed=sc.sim.seed
        )
        model = self._load_model() if sc.model else None

        # Attach VCD
        vcd_obj = None
        vcd_path = sc.sim.vcd
        if vcd_path:
            from deltasim.vcd.writer import VCDWriter
            vcd_obj = VCDWriter(vcd_path, self.design)
            vcd_obj.record_initial(0, engine.signals)
            engine.vcd_writer = vcd_obj

        if model and hasattr(model, "reset"):
            model.reset()

        hp = engine.half_period
        period_ns = hp * 2

        # §15.5: Initialize — assert reset ONCE at start
        from deltasim.sim.engine import SimResult, _InitialScheduler
        engine._time = 0
        engine._result = SimResult()
        engine._initial_scheduler = _InitialScheduler()
        if engine._rst is not None:
            engine._write(engine._rst, 0 if engine._rst_active_low else 1)
        engine._eval_comb_loop()
        engine._result.history.append(dict(engine.signals))

        # Build expected schedule: (cycle, offset_ns) -> list of entries
        exp_sched = {}
        for exp in sc.expected:
            for c in _expand_at(exp.at, sc.sim.cycles):
                off = exp.offset_ns if hasattr(exp, 'offset_ns') else 0
                exp_sched.setdefault((c, off), []).append(exp)

        def exp_offs(cycle):
            return {o for (c, o) in exp_sched if c == cycle}

        # §15.4: apply_stimulus with edge detection
        def apply_stimulus(vals):
            changed = []
            for s, v in vals.items():
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

        def drive_clock(val):
            prev = engine.signals.get(engine._clk, 0) if engine._clk else 0
            if engine._clk:
                engine._write(engine._clk, val)
            nba = engine._collect_nba_for_edge(engine._clk, prev, val) if engine._clk else []
            engine._apply_nba(nba)
            engine._eval_comb_loop()

        try:
            for cycle_idx in range(sc.sim.cycles):
                N = cycle_idx + 1
                t0_ns = cycle_idx * period_ns + hp

                # §15.5: release reset once
                if engine._rst is not None and cycle_idx == engine.reset_cycles:
                    apply_stimulus({engine._rst: 1 if engine._rst_active_low else 0})

                stim_offs = stim.offsets_for_cycle(N)
                exp_offs_n = exp_offs(N)
                all_offs = sorted({0, hp} | stim_offs | exp_offs_n)

                for off in all_offs:
                    engine._time = t0_ns + off

                    vals = stim.get(N, off)
                    if vals:
                        apply_stimulus(vals)

                    if off == 0 and engine._clk:
                        drive_clock(1)
                    elif off == hp and engine._clk:
                        drive_clock(0)

                    if vcd_obj:
                        vcd_obj.record(engine._time, engine.signals)

                    snap = dict(engine.signals)
                    for exp in exp_sched.get((N, off), []):
                        self._check_expected_entry(result, exp, N, off, engine._time, snap)

                    if off == 0 and model:
                        self._check_model(result, engine, model, N, snap)

                engine._result.history.append(dict(engine.signals))
                _emit("progress", phase="sim",
                      cycle=N, total_cycles=sc.sim.cycles,
                      time_ns=engine._time)

        finally:
            if vcd_obj:
                vcd_obj.close()

        result.log.extend(engine._result.log)
        _emit("finish", status="PASS" if result.passed else "FAIL")
        return result

    # ------------------------------------------------------------------
    # Expected value checking (offset-aware)
    # ------------------------------------------------------------------

    def _check_expected_entry(
        self,
        result: TestRunResult,
        exp,
        cycle_num: int,
        offset_ns: int,
        time_ns: int,
        snap: dict,
    ) -> None:
        if exp.check:
            for sig, expected_val in exp.check.items():
                actual = snap.get(sig, 0)
                passed = actual == expected_val
                cr = CheckResult(
                    cycle=cycle_num,
                    signal=sig,
                    expected=expected_val,
                    actual=actual,
                    passed=passed,
                    source="spec",
                )
                result.checks.append(cr)
                if self.verbose:
                    print(cr)

        elif exp.expr:
            ns = {k: v for k, v in snap.items()}
            ns["_cycle"] = cycle_num
            try:
                passed = bool(eval(exp.expr, {"__builtins__": {}}, ns))  # noqa: S307
                actual_str = repr(ns.get(exp.expr.split()[0], snap))
            except Exception as e:
                passed = False
                actual_str = f"<eval error: {e}>"
            cr = CheckResult(
                cycle=cycle_num,
                signal="<expr>",
                expected=exp.expr,
                actual=actual_str,
                passed=passed,
                source="spec",
            )
            result.checks.append(cr)
            if self.verbose:
                print(cr)

    def _check_expected(
        self,
        result: TestRunResult,
        sc: Scenario,
        cycle_num: int,
        snap: dict,
    ) -> None:
        """Legacy method — kept for backward compat; delegates to _check_expected_entry."""
        for exp in sc.expected:
            cycles = _expand_at(exp.at, sc.sim.cycles)
            if cycle_num not in cycles:
                continue
            off = exp.offset_ns if hasattr(exp, 'offset_ns') else 0
            t_ns = (cycle_num - 1) * (self.engine.half_period * 2) + self.engine.half_period
            self._check_expected_entry(result, exp, cycle_num, off, t_ns + off, snap)

    # ------------------------------------------------------------------
    # Reference model comparison
    # ------------------------------------------------------------------

    def _check_model(
        self,
        result: TestRunResult,
        engine: SimEngine,
        model,
        cycle_num: int,
        snap: dict,
    ) -> None:
        input_vals = {
            name: engine.signals.get(name, 0)
            for name, info in self.design.signals.items()
            if info.direction == "input"
        }
        try:
            expected_outputs = model.step(input_vals)
        except Exception as e:
            result.log.append(f"[model error @ cycle {cycle_num}] {e}")
            return

        for sig, expected_val in expected_outputs.items():
            actual = snap.get(sig, 0)
            passed = actual == expected_val
            cr = CheckResult(
                cycle=cycle_num,
                signal=sig,
                expected=expected_val,
                actual=actual,
                passed=passed,
                source="model",
            )
            result.checks.append(cr)
            if self.verbose:
                print(cr)

    # ------------------------------------------------------------------
    # Model loader
    # ------------------------------------------------------------------

    def _load_model(self):
        ms = self.scenario.model
        base = self.scenario._base_dir
        file_path = ms.file if os.path.isabs(ms.file) else os.path.join(base, ms.file)
        spec = importlib.util.spec_from_file_location("_deltasim_model", file_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, ms.class_name)
        return cls(**{str(k): v for k, v in ms.params.items()})
