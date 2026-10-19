"""YAML scenario specification: data model, loader, and stimulus schedule.

Spec format (version 1)
-----------------------
version: 1
top: counter

clock:
  signal: clk        # auto-detected if omitted
  period_ns: 10      # default: 10 (100 MHz)

reset:
  signal: rst_n      # auto-detected if omitted
  active_low: true   # default: true
  duration_cycles: 10

sim:
  cycles: 30
  seed: 42
  vcd: null          # path for VCD output (optional)

inputs:
  - at: 15
    set: {d: 1}
  - at: [11, 30]          # inclusive range
    random:
      d: {range: [0, 1]}
  - at: [11, 30]
    random:
      data:
        dist:
          - {value: 0, weight: 10}
          - {range: [1, 255], weight: 1}

expected:
  - at: 11
    check: {count: 1}
  - at: [11, 30]
    expr: "count == _cycle - 10"   # _cycle = current cycle (1-indexed)

model:
  file: counter_model.py   # relative to spec file
  class: CounterModel
  params: {WIDTH: 8}
"""

from __future__ import annotations

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
class Scenario:
    version: int = 1
    top: Optional[str] = None
    clock: ClockSpec = field(default_factory=ClockSpec)
    reset: ResetSpec = field(default_factory=ResetSpec)
    sim: SimSpec = field(default_factory=SimSpec)
    inputs: List[InputEntry] = field(default_factory=list)
    expected: List[ExpectedEntry] = field(default_factory=list)
    model: Optional[ModelSpec] = None
    # §12.1: SKIP
    skip: bool = False
    skip_reason: str = ""
    _base_dir: str = field(default="", compare=False)


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

class ScenarioError(Exception):
    pass


def load_scenario(path: str) -> Scenario:
    """Load and validate a YAML scenario file."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    base_dir = os.path.dirname(os.path.abspath(path))
    s = Scenario(_base_dir=base_dir)
    s.version = int(data.get("version", 1))
    s.top = data.get("top")

    # §12.1: skip
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

    for entry in data.get("inputs") or []:
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
        s.inputs.append(inp)

    for entry in data.get("expected") or []:
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
        s.expected.append(exp)

    if data.get("model"):
        m = data["model"]
        s.model = ModelSpec(
            file=str(m["file"]),
            class_name=str(m.get("class", m.get("class_name", "Model"))),
            params=dict(m.get("params") or {}),
        )

    return s


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
