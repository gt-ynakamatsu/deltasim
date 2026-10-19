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

        # Reset model
        if model and hasattr(model, "reset"):
            model.reset()

        def pre_hook(cycle_num: int, signals: dict):
            for sig, val in stim.get(cycle_num).items():
                engine._write(sig, val)

        def post_hook(cycle_num: int, snap: dict):
            self._check_expected(result, sc, cycle_num, snap)
            if model:
                self._check_model(result, engine, model, cycle_num, snap)
            _emit("progress", phase="sim",
                  cycle=cycle_num, total_cycles=sc.sim.cycles,
                  time_ns=engine._time)

        try:
            engine.run(
                sc.sim.cycles,
                pre_posedge_hook=pre_hook,
                post_posedge_hook=post_hook,
            )
        finally:
            if vcd_obj:
                vcd_obj.close()

        result.log.extend(engine._result.log)
        _emit("finish", status="PASS" if result.passed else "FAIL")
        return result

    # ------------------------------------------------------------------
    # Expected value checking
    # ------------------------------------------------------------------

    def _check_expected(
        self,
        result: TestRunResult,
        sc: Scenario,
        cycle_num: int,
        snap: dict,
    ) -> None:
        for exp in sc.expected:
            cycles = _expand_at(exp.at, sc.sim.cycles)
            if cycle_num not in cycles:
                continue

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
