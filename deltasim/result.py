"""deltasim result types — M5 API contract.

All public types in this module correspond exactly to the interface contract
in docs/api.md.  Do not add fields here without updating docs/api.md.

Value encoding (§2.7)
---------------------
  1-bit : "0" | "1" | "x" | "z"
  N-bit : "0101xz10"  (MSB first, length = N)

status codes  : "PASS" | "FAIL" | "ERROR" | "SKIP"
severity      : "error" | "warning"
phase         : "parse" | "elab" | "sim" | "check"
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "2"

# ---------------------------------------------------------------------------
# Z-value sentinel  (§12.4: x と z を区別、小文字統一)
# ---------------------------------------------------------------------------

class _ZValue:
    """Sentinel for high-impedance (Z) — distinct from None (X)."""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Z"


Z_VALUE = _ZValue()   # singleton sentinel for Z


def _is_xz(v) -> bool:
    """Return True if v is X (None) or Z (_ZValue)."""
    return v is None or isinstance(v, _ZValue)


# ---------------------------------------------------------------------------
# Value encoding helpers (§2.7)
# ---------------------------------------------------------------------------

def val_to_4state(val: Optional[int], width: int) -> str:
    """Convert a simulation value to a 4-state bit string (§12.4 小文字統一).

    * None (X) → "x" / "xxx..."
    * Z_VALUE  → "z" / "zzz..."
    * int      → "0" / "1" / "0101..."
    """
    if width <= 0:
        width = 1
    if val is None:
        return "x" if width == 1 else "x" * width
    if isinstance(val, _ZValue):
        return "z" if width == 1 else "z" * width
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
# Structured error (§2.3)
# ---------------------------------------------------------------------------

@dataclass
class ErrorInfo:
    file: str = ""
    line: int = 0
    column: int = 0
    severity: str = "error"   # "error" | "warning"
    message: str = ""
    code: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Check result (one expected-value check, §2.2)
# ---------------------------------------------------------------------------

@dataclass
class CheckInfo:
    """§12.5: elapsed_ms はケース（シナリオ）単位で TestResult.elapsed_ms に入る。
    CheckInfo はチェック個別の合否を表す。
    §15.7: offset_ns フィールドを追加。time_ns は at:N + offset_ns の実際の確認時刻。"""
    name: str = ""
    status: str = "PASS"        # PASS | FAIL | ERROR | SKIP
    signal: str = ""
    cycle: int = 0
    time_ns: int = 0
    offset_ns: int = 0          # §15.7: 追加フィールド。省略時(offset_ns=0)は既存と同じ
    expected: str = ""          # 4-state bit string
    actual: str = ""            # 4-state bit string
    source: str = "spec"        # "spec" | "model"
    seed: int = 0
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Warning (§15.12)
# ---------------------------------------------------------------------------

@dataclass
class WarningInfo:
    """§15.12: 未知キーなど非致命的な問題の警告。"""
    message: str = ""
    code: str = ""             # e.g. "W_UNKNOWN_KEY"
    file: str = ""
    line: int = 0
    column: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Signal change entry (§2.6)
# ---------------------------------------------------------------------------

@dataclass
class SignalChange:
    time_ns: int = 0
    value: str = "x"            # 4-state bit string


# ---------------------------------------------------------------------------
# Design info (return type of load(), §2.1)
# ---------------------------------------------------------------------------

@dataclass
class PortInfo:
    name: str
    direction: str              # "input" | "output" | "inout"
    width: int
    signed: bool = False


@dataclass
class SignalSpec:
    name: str
    width: int
    direction: Optional[str] = None
    signed: bool = False


@dataclass
class HierarchyNode:
    """One node in the module hierarchy tree."""
    name: str
    module: str
    signals: List[str] = field(default_factory=list)
    children: List["HierarchyNode"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "module": self.module,
            "signals": self.signals,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class DesignInfo:
    """Return value of load() — represents an elaborated design (§2.1)."""
    schema_version: str = SCHEMA_VERSION
    status: str = "PASS"
    module: str = ""
    ports: List[PortInfo] = field(default_factory=list)
    params: Dict[str, int] = field(default_factory=dict)
    signals: List[SignalSpec] = field(default_factory=list)
    hierarchy: Optional[HierarchyNode] = None
    errors: List[ErrorInfo] = field(default_factory=list)
    # Private: internal ElabDesign (not serialised)
    _design: Any = field(default=None, repr=False, compare=False)
    _catalog: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "module": self.module,
            "ports": [asdict(p) for p in self.ports],
            "params": self.params,
            "signals": [asdict(s) for s in self.signals],
            "hierarchy": self.hierarchy.to_dict() if self.hierarchy else None,
            "errors": [e.to_dict() for e in self.errors],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class CaseResult:
    """1 テストケース（シナリオ cases[] の要素）の実行結果 (§13.2)。

    §14.3: message フィールド
      SKIP    : skip_reason（無ければ "skipped"）
      キャンセル: "cancelled"
      ERROR   : エラー要約
      PASS/FAIL: §14 では未定義 → 空文字列（docs に明記済み）
    """
    name: str = ""
    status: str = "PASS"           # PASS | FAIL | ERROR | SKIP
    seed: int = 0
    elapsed_ms: float = 0.0        # §13.1: このケースの実時間
    message: str = ""              # §14.3: 状態を表すメッセージ
    checks: List[CheckInfo] = field(default_factory=list)
    log: List[str] = field(default_factory=list)
    errors: List[ErrorInfo] = field(default_factory=list)
    warnings: List[WarningInfo] = field(default_factory=list)  # §15.12
    cancelled: bool = False
    truncated_at: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "status": self.status,
            "seed": self.seed,
            "elapsed_ms": self.elapsed_ms,
            "message": self.message,
            "cancelled": self.cancelled,
            "truncated_at": self.truncated_at,
            "checks": [c.to_dict() for c in self.checks],
            "log": self.log,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],   # §15.13: always []
        }
        return d


# ---------------------------------------------------------------------------
# Main test result (§2.2)
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    """Return value of test() / `deltasim test --json` (§2.2, schema v2).

    §13.1: elapsed_ms は全体の実時間（summary.elapsed_ms と同値）。後方互換で残すが非推奨。
           ケースごとの実時間は cases[].elapsed_ms を参照。
    §13.2: cases[] に各テストケースの結果を格納。
    """
    schema_version: str = SCHEMA_VERSION
    module: str = ""
    status: str = "PASS"        # PASS | FAIL | ERROR | SKIP
    seed: int = 0
    elapsed_ms: float = 0.0     # §13.1: 全体実時間 (≡ summary.elapsed_ms, 非推奨)
    log: List[str] = field(default_factory=list)
    checks: List[CheckInfo] = field(default_factory=list)   # 全ケース集計 (compat)
    cases: List[CaseResult] = field(default_factory=list)   # §13.2 per-case details
    errors: List[ErrorInfo] = field(default_factory=list)
    warnings: List[WarningInfo] = field(default_factory=list)  # §15.12
    cancelled: bool = False
    truncated_at: Optional[int] = None

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
        from deltasim import __version__ as _ver
        d = {
            "schema_version": self.schema_version,
            "deltasim_version": _ver,           # §0.2.0: ツール版（schema_version は "2" のまま）
            "module": self.module,
            "status": self.status,
            "seed": self.seed,
            "elapsed_ms": self.elapsed_ms,
            "summary": {"elapsed_ms": self.elapsed_ms},
            "cancelled": self.cancelled,
            "truncated_at": self.truncated_at,
            "log": self.log,
            "cases": [c.to_dict() for c in self.cases],
            "checks": [c.to_dict() for c in self.checks],
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],   # §15.13: always []
        }
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


@dataclass
class LoadResult:
    """Return value of load() when called with just files (legacy compat)."""
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
    elapsed_ms: float = 0.0
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
            "elapsed_ms": self.elapsed_ms,
            "vcd_path": self.vcd_path,
            "log": self.log,
            "errors": [e.to_dict() for e in self.errors],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Simulator snapshot (§2.5)
# ---------------------------------------------------------------------------

@dataclass
class SimSnapshot:
    """State snapshot returned by Simulator.step() / run_until()."""
    schema_version: str = SCHEMA_VERSION
    time_ns: int = 0
    signals: Dict[str, Optional[int]] = field(default_factory=dict)
    # Signals that changed since the last snapshot
    changes: List[Tuple[str, Optional[int]]] = field(default_factory=list)
    # Breakpoint ID that fired, or None
    breakpoint_hit: Optional[int] = None

    def get(self, signal: str) -> Optional[int]:
        return self.signals.get(signal)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "time_ns": self.time_ns,
            "signals": {k: v for k, v in self.signals.items()},
            "changes": [[k, v] for k, v in self.changes],
            "breakpoint_hit": self.breakpoint_hit,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


# ---------------------------------------------------------------------------
# Signal change DB (§2.6)
# ---------------------------------------------------------------------------

class SignalChangeDB:
    """In-memory store of value-change events keyed by signal name (§2.6).

    §12.3: 既定で記録対象はトップのポートと仕様参照シグナルのみ。
    上限 512 MB (既定)、超えたら記録停止 + truncated_at を設定。
    """

    DEFAULT_MAX_BYTES = 512 * 1024 * 1024  # 512 MB

    def __init__(
        self,
        max_bytes: int = DEFAULT_MAX_BYTES,
        record_set: "Optional[set]" = None,  # None = record all
    ):
        self._db: Dict[str, List[SignalChange]] = {}
        self._max_bytes = max_bytes
        self._used_bytes = 0
        self._truncated_at: Optional[int] = None
        self._active = True
        self._record_set = record_set  # None = all signals

    @property
    def truncated_at(self) -> Optional[int]:
        """Simulation time (ns) when recording was stopped due to memory limit."""
        return self._truncated_at

    def record(self, time_ns: int, signal: str, val, width: int) -> None:
        if not self._active:
            return
        # Filter by record set (§12.3)
        if self._record_set is not None and signal not in self._record_set:
            return

        value_str = val_to_4state(val, width)
        entry = SignalChange(time_ns=time_ns, value=value_str)

        lst = self._db.setdefault(signal, [])
        if lst and lst[-1].value == value_str:
            return  # skip duplicate values

        # Check memory budget (~16 bytes per entry rough estimate)
        est = 8 + len(value_str) + 8  # time_ns(8) + chars + overhead(8)
        if self._used_bytes + est > self._max_bytes:
            self._truncated_at = time_ns
            self._active = False
            return

        lst.append(entry)
        self._used_bytes += est

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
    type: str                           # start | progress | case_done | finish
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)
    module: Optional[str] = None
    status: Optional[str] = None
    elapsed_ms: Optional[float] = None
    cancelled: Optional[bool] = None
    phase: Optional[str] = None         # parse | elab | sim | check
    cycle: Optional[int] = None
    total_cycles: Optional[int] = None
    time_ns: Optional[int] = None
    name: Optional[str] = None
    signal: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: Optional[str] = None      # §14.3: case_done に message を追加

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"type": self.type, "timestamp_ms": self.timestamp_ms}
        for k in ("module", "status", "elapsed_ms", "cancelled",
                   "phase", "cycle", "total_cycles", "time_ns",
                   "name", "signal", "expected", "actual", "message"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cancel token
# ---------------------------------------------------------------------------

class CancelToken:
    """Lightweight cancel token (§2.4)."""

    def __init__(self):
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        if self._cancelled:
            raise SimulationCancelled()


class SimulationCancelled(Exception):
    """Raised when a CancelToken fires during simulation."""
