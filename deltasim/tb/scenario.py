"""YAML scenario specification: data model, loader, and stimulus schedule.

§13.2 Multi-case format (new):
-------------------------------
version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 10}
sim:   {cycles: 30, seed: 42}

cases:
  - name: basic
    expected:
      - at: 11
        check: {count: 1}
  - name: overflow
    sim: {cycles: 260}
    expected:
      - at: 258
        check: {count: 255}
  - name: skip_example
    skip: true
    skip_reason: "CI対象外"

Legacy single-case format (backward compat, no cases: key):
------------------------------------------------------------
version: 1
top: counter
inputs:   [...]
expected: [...]
→ treated as: cases: [{name: <filename_stem>}] with top-level inputs/expected
"""

from __future__ import annotations

import fnmatch
import os
import random as _random_mod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import yaml


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ClockSpec:
    signal: Optional[str] = None
    period_ns: int = 10


@dataclass
class ResetSpec:
    signal: Optional[str] = None
    active_low: bool = True
    duration_cycles: int = 10


@dataclass
class SimSpec:
    cycles: int = 100
    seed: int = 0
    vcd: Optional[str] = None


@dataclass
class InputEntry:
    at: Union[int, Tuple[int, int], str]
    set_values: Optional[Dict[str, int]] = None
    random_spec: Optional[Dict[str, object]] = None


@dataclass
class ExpectedEntry:
    at: Union[int, Tuple[int, int]]
    check: Optional[Dict[str, int]] = None
    expr: Optional[str] = None


@dataclass
class ModelSpec:
    file: str
    class_name: str
    params: Dict[str, object] = field(default_factory=dict)


@dataclass
class CaseSpec:
    """§13.2: 1 テストケースの仕様。ケース側の値でトップレベルを上書きできる。"""
    name: str = ""
    skip: bool = False
    skip_reason: str = ""
    seed: Optional[int] = None         # overrides sim.seed if set
    clock: Optional[ClockSpec] = None  # overrides top-level clock
    reset: Optional[ResetSpec] = None  # overrides top-level reset
    cycles: Optional[int] = None       # overrides sim.cycles if set
    inputs: List[InputEntry] = field(default_factory=list)
    expected: List[ExpectedEntry] = field(default_factory=list)
    model: Optional[ModelSpec] = None  # overrides top-level model


@dataclass
class Scenario:
    version: int = 1
    top: Optional[str] = None
    clock: ClockSpec = field(default_factory=ClockSpec)
    reset: ResetSpec = field(default_factory=ResetSpec)
    sim: SimSpec = field(default_factory=SimSpec)
    inputs: List[InputEntry] = field(default_factory=list)
    expected: List[ExpectedEntry] = field(default_factory=list)
    model: Optional[ModelSpec] = None
    # §12.1: top-level SKIP (applies when no cases: key — legacy single-case)
    skip: bool = False
    skip_reason: str = ""
    # §13.2: named test cases (empty = legacy single-case mode)
    cases: List[CaseSpec] = field(default_factory=list)
    _base_dir: str = field(default="", compare=False)
    _stem: str = field(default="scenario", compare=False)   # filename stem


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

class ScenarioError(Exception):
    pass


def load_scenario(path: str) -> Scenario:
    """Load and validate a YAML scenario file.

    §13.2 backward compat: if no ``cases:`` key, the whole file is treated as
    a single case whose name = the file's stem (e.g. ``counter_spec``).
    """
    from pathlib import Path as _Path
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    base_dir = os.path.dirname(os.path.abspath(path))
    stem = _Path(path).stem
    s = Scenario(_base_dir=base_dir, _stem=stem)
    s.version = int(data.get("version", 1))
    s.top = data.get("top")

    # §12.1: top-level skip (applies to legacy single-case only)
    s.skip = bool(data.get("skip", False))
    s.skip_reason = str(data.get("skip_reason", ""))

    ck = data.get("clock") or {}
    s.clock = ClockSpec(
        signal=ck.get("signal"),
        period_ns=int(ck.get("period_ns", 10)),
    )

    rst = data.get("reset") or {}
    s.reset = ResetSpec(
        signal=rst.get("signal"),
        active_low=bool(rst.get("active_low", True)),
        duration_cycles=int(rst.get("duration_cycles", 10)),
    )

    sim = data.get("sim") or {}
    s.sim = SimSpec(
        cycles=int(sim.get("cycles", 100)),
        seed=int(sim.get("seed", 0)),
        vcd=sim.get("vcd"),
    )

    s.inputs = _parse_inputs(data.get("inputs") or [])
    s.expected = _parse_expected(data.get("expected") or [])

    if data.get("model"):
        m = data["model"]
        s.model = ModelSpec(
            file=str(m["file"]),
            class_name=str(m.get("class", m.get("class_name", "Model"))),
            params=dict(m.get("params") or {}),
        )

    # §13.2: Parse cases: list
    raw_cases = data.get("cases")
    if raw_cases is not None:
        seen_names: set = set()
        for rc in raw_cases:
            cname = rc.get("name")
            if not cname:
                raise ScenarioError(f"cases entry missing required 'name': {rc}")
            if cname in seen_names:
                raise ScenarioError(
                    f"cases[].name must be unique: duplicate '{cname}'",
                )
            seen_names.add(cname)

            case = CaseSpec(name=cname)
            case.skip = bool(rc.get("skip", False))
            case.skip_reason = str(rc.get("skip_reason", ""))

            # Optional seed / cycles / clock / reset overrides
            if "seed" in rc:
                case.seed = int(rc["seed"])
            if "cycles" in rc:
                case.cycles = int(rc["cycles"])
            if "clock" in rc:
                ck2 = rc["clock"]
                case.clock = ClockSpec(
                    signal=ck2.get("signal"),
                    period_ns=int(ck2.get("period_ns", 10)),
                )
            if "reset" in rc:
                rst2 = rc["reset"]
                case.reset = ResetSpec(
                    signal=rst2.get("signal"),
                    active_low=bool(rst2.get("active_low", True)),
                    duration_cycles=int(rst2.get("duration_cycles", 10)),
                )
            # Stimulus/expect — support both "inputs"/"expected" and "stimulus"/"expect"
            inp_raw = rc.get("inputs") or rc.get("stimulus") or []
            exp_raw = rc.get("expected") or rc.get("expect") or []
            case.inputs = _parse_inputs(inp_raw)
            case.expected = _parse_expected(exp_raw)

            if "model" in rc:
                m = rc["model"]
                case.model = ModelSpec(
                    file=str(m["file"]),
                    class_name=str(m.get("class", m.get("class_name", "Model"))),
                    params=dict(m.get("params") or {}),
                )
            s.cases.append(case)
    # else: no cases: key → legacy single-case (s.cases stays empty)

    return s


def _parse_inputs(raw: list) -> List[InputEntry]:
    result = []
    for entry in raw:
        if "at" not in entry:
            raise ScenarioError(f"inputs entry missing 'at': {entry}")
        at = _parse_at(entry["at"])
        inp = InputEntry(at=at)
        if "set" in entry:
            inp.set_values = {k: int(v) for k, v in entry["set"].items()}
        elif "random" in entry:
            inp.random_spec = dict(entry["random"])
        else:
            raise ScenarioError(f"inputs entry has neither 'set' nor 'random': {entry}")
        result.append(inp)
    return result


def _parse_expected(raw: list) -> List[ExpectedEntry]:
    result = []
    for entry in raw:
        if "at" not in entry:
            raise ScenarioError(f"expected entry missing 'at': {entry}")
        at = _parse_at(entry["at"])
        exp = ExpectedEntry(at=at)
        if "check" in entry:
            exp.check = {k: int(v) for k, v in entry["check"].items()}
        elif "expr" in entry:
            exp.expr = str(entry["expr"])
        else:
            raise ScenarioError(f"expected entry has neither 'check' nor 'expr': {entry}")
        result.append(exp)
    return result


def _parse_at(val) -> Union[int, Tuple[int, int], str]:
    if val == "*":
        return "*"
    if isinstance(val, list):
        if len(val) != 2:
            raise ScenarioError(f"'at' list must have exactly 2 elements: {val}")
        return (int(val[0]), int(val[1]))
    return int(val)


# ---------------------------------------------------------------------------
# Stimulus schedule
# ---------------------------------------------------------------------------

class StimulusSchedule:
    """Pre-computes the input values to apply at each cycle (1-indexed)."""

    def __init__(
        self,
        inputs: List[InputEntry],
        total_cycles: int,
        seed: int = 0,
    ):
        self._rng = _random_mod.Random(seed)
        self._schedule: Dict[int, Dict[str, int]] = {}
        self._build(inputs, total_cycles)

    def _build(self, inputs: List[InputEntry], total_cycles: int) -> None:
        for entry in inputs:
            cycles = sorted(_expand_at(entry.at, total_cycles))
            for cycle in cycles:
                vals: Dict[str, int] = {}
                if entry.set_values:
                    vals.update(entry.set_values)
                elif entry.random_spec:
                    for sig, spec in entry.random_spec.items():
                        vals[sig] = self._sample(spec)
                self._schedule.setdefault(cycle, {}).update(vals)

    def get(self, cycle: int) -> Dict[str, int]:
        """Input values to apply before posedge of *cycle* (1-indexed)."""
        return self._schedule.get(cycle, {})

    def _sample(self, spec) -> int:
        if isinstance(spec, (int, float)):
            return int(spec)
        if isinstance(spec, dict):
            if "range" in spec:
                lo, hi = int(spec["range"][0]), int(spec["range"][1])
                return self._rng.randint(lo, hi)
            if "dist" in spec:
                return self._sample_dist(spec["dist"])
        return 0

    def _sample_dist(self, dist: list) -> int:
        total = sum(float(d.get("weight", 1)) for d in dist)
        r = self._rng.uniform(0, total)
        cumulative = 0.0
        for d in dist:
            cumulative += float(d.get("weight", 1))
            if r <= cumulative:
                if "value" in d:
                    return int(d["value"])
                if "range" in d:
                    return self._rng.randint(int(d["range"][0]), int(d["range"][1]))
        return 0


def _expand_at(at: Union[int, Tuple[int, int], str], total: int) -> List[int]:
    """Expand an 'at' spec to a list of 1-indexed cycle numbers."""
    if at == "*":
        return list(range(1, total + 1))
    if isinstance(at, tuple):
        return list(range(at[0], at[1] + 1))
    return [int(at)]
