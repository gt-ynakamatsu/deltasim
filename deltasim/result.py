"""deltasim result types — M5 API contract.

All public types in this module correspond exactly to the interface contract
in docs/api.md (§2 of deltasim GUI spec v0.1).  Do not add fields here
without updating docs/api.md and getting sign-off.

Value encoding (§2.7)
---------------------
Signal values are represented as 4-state bit strings:
  1-bit :  "0" | "1" | "x" | "z"
  N-bit :  "0101xz10" (MSB first, length = N)

status codes: "PASS" | "FAIL" | "ERROR" | "SKIP"
severity    : "error" | "warning"
phase       : "parse" | "elab" | "sim" | "check"
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# Value encoding helpers
# ---------------------------------------------------------------------------

def val_to_4state(val: Optional[int], width: int) -> str:
    """Convert a simulation value (None = X) to a 4-state bit string."""
    if width <= 0:
        width = 1
    if val is None:
        return "x" if width == 1 else "x" * width
    if width == 1:
        return str(val & 1)
    return bin(val & ((1 << width) - 1))[2:].zfill(width)


def val_from_4state(s: str) -> Optional[int]:
    """Parse a 4-state bit string back to an int (None if any bit is X/Z)."""
    s = s.lower()
    if any(c in s for c in "xz"):
        return None
    return int(s, 2)


# ---------------------------------------------------------------------------
# Structured error
# ---------------------------------------------------------------------------

@dataclass
class ErrorInfo:
    """Structured parse / elaboration error (§2.3)."""
    file: str = ""
    line: int = 0
    column: int = 0
    severity: str = "error"   # "error" | "warning"
    message: str = ""
    code: str = ""            # e.g. "E001", "W002"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Check result (one expected-value check)
# ---------------------------------------------------------------------------

@dataclass
class CheckInfo:
    """Result of one expected-value check (§2.2)."""
    name: str = ""
    status: str = "PASS"       # PASS | FAIL | ERROR | SKIP
    signal: str = ""
    cycle: int = 0
    time_ns: int = 0
    expected: str = ""         # 4-state bit string
    actual: str = ""           # 4-state bit string
    source: str = "spec"       # "spec" | "model"
    seed: int = 0
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Signal change entry
# ---------------------------------------------------------------------------

@dataclass
class SignalChange:
    """One value-change event on a signal (§2.6)."""
    time_ns: int = 0
    value: str = "x"   # 4-state bit string


# ---------------------------------------------------------------------------
# Main result types
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    """Return value of test() / output of `deltasim test --json` (§2.2)."""
    schema_version: str = SCHEMA_VERSION
    module: str = ""
    status: str = "PASS"         # PASS | FAIL | ERROR | SKIP
    seed: int = 0
    elapsed_ms: float = 0.0
    log: List[str] = field(default_factory=list)
    checks: List[CheckInfo] = field(default_factory=list)
    errors: List[ErrorInfo] = field(default_factory=list)
    cancelled: bool = False

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    @property
    def n_pass(self) -> int:
        return sum(1 for c in self.checks if c.status == "PASS")

    @property
    def n_fail(self) -> int:
        return sum(1 for c in self.checks if c.status == "FAIL")

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "schema_version": self.schema_version,
            "module": self.module,
            "status": self.status,
            "seed": self.seed,
            "elapsed_ms": self.elapsed_ms,
            "cancelled": self.cancelled,
            "log": self.log,
            "checks": [c.to_dict() for c in self.checks],
            "errors": [e.to_dict() for e in self.errors],
        }
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_run_result(cls, run_result, module_name: str, seed: int = 0) -> "TestResult":
        """Convert a legacy RunResult / TestRunResult to TestResult."""
        from deltasim.tb.runner import TestRunResult, CheckResult
        if not isinstance(run_result, TestRunResult):
            return cls(module=module_name, seed=seed)

        checks = []
        status = "PASS"
        for cr in run_result.checks:
            cs = "PASS" if cr.passed else "FAIL"
            if cs == "FAIL":
                status = "FAIL"
            checks.append(CheckInfo(
                name=f"{cr.signal}@cycle{cr.cycle}[{cr.source}]",
                status=cs,
                signal=cr.signal,
                cycle=cr.cycle,
                time_ns=cr.cycle * 10,  # approximate
                expected=str(cr.expected),
                actual=str(cr.actual),
                source=cr.source,
                seed=seed,
                message=None if cr.passed else f"expected {cr.expected!r}, got {cr.actual!r}",
            ))
        return cls(
            module=module_name,
            status=status,
            seed=seed,
            log=run_result.log,
            checks=checks,
        )


@dataclass
class LoadResult:
    """Return value of load() (§2.1)."""
    schema_version: str = SCHEMA_VERSION
    status: str = "PASS"
    module: str = ""
    ports: List[dict] = field(default_factory=list)
    params: Dict[str, int] = field(default_factory=dict)
    signals: List[dict] = field(default_factory=list)
    errors: List[ErrorInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "module": self.module,
            "ports": self.ports,
            "params": self.params,
            "signals": self.signals,
            "errors": [e.to_dict() for e in self.errors],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class RunResult:
    """Return value of run() (§2.1)."""
    schema_version: str = SCHEMA_VERSION
    module: str = ""
    status: str = "PASS"
    cycles: int = 0
    time_ns: int = 0
    log: List[str] = field(default_factory=list)
    vcd_path: Optional[str] = None
    errors: List[ErrorInfo] = field(default_factory=list)
    cancelled: bool = False
    # Signal-change DB (available only when keep_changes=True)
    _change_db: Any = field(default=None, repr=False, compare=False)

    def get_signal_changes(
        self, signal: str, start_ns: int = 0, end_ns: Optional[int] = None
    ) -> List[SignalChange]:
        """Return value-change list for *signal* within the time range."""
        if self._change_db is None:
            return []
        return self._change_db.get(signal, start_ns, end_ns)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "module": self.module,
            "status": self.status,
            "cycles": self.cycles,
            "time_ns": self.time_ns,
            "cancelled": self.cancelled,
            "vcd_path": self.vcd_path,
            "log": self.log,
            "errors": [e.to_dict() for e in self.errors],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Signal change DB
# ---------------------------------------------------------------------------

class SignalChangeDB:
    """In-memory store of value-change events keyed by signal name (§2.6)."""

    def __init__(self):
        self._db: Dict[str, List[SignalChange]] = {}

    def record(self, time_ns: int, signal: str, val: Optional[int], width: int) -> None:
        entry = SignalChange(
            time_ns=time_ns,
            value=val_to_4state(val, width),
        )
        self._db.setdefault(signal, []).append(entry)

    def get(
        self, signal: str, start_ns: int = 0, end_ns: Optional[int] = None
    ) -> List[SignalChange]:
        changes = self._db.get(signal, [])
        if start_ns == 0 and end_ns is None:
            return list(changes)
        return [c for c in changes
                if c.time_ns >= start_ns and (end_ns is None or c.time_ns <= end_ns)]

    def signals(self) -> List[str]:
        return list(self._db.keys())


# ---------------------------------------------------------------------------
# Progress events (§2.4)
# ---------------------------------------------------------------------------

@dataclass
class ProgressEvent:
    """One progress event emitted during simulation (§2.4)."""
    type: str                        # start | progress | case_done | finish
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)
    # start / finish
    module: Optional[str] = None
    status: Optional[str] = None
    elapsed_ms: Optional[float] = None
    cancelled: Optional[bool] = None
    # progress
    phase: Optional[str] = None     # parse | elab | sim | check
    cycle: Optional[int] = None
    total_cycles: Optional[int] = None
    time_ns: Optional[int] = None
    # case_done
    name: Optional[str] = None
    signal: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"type": self.type, "timestamp_ms": self.timestamp_ms}
        for k in ("module", "status", "elapsed_ms", "cancelled",
                   "phase", "cycle", "total_cycles", "time_ns",
                   "name", "signal", "expected", "actual"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class CancelToken:
    """Lightweight cancel token.  Pass to API functions to interrupt them."""

    def __init__(self):
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        """Raise SimulationCancelled if this token has been cancelled."""
        if self._cancelled:
            raise SimulationCancelled()


class SimulationCancelled(Exception):
    """Raised when a CancelToken fires during simulation."""
