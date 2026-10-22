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
import re
import random as _random_mod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import yaml

# ---------------------------------------------------------------------------
# §18.7.1: YAML set/check 文字列バリデーション
# ---------------------------------------------------------------------------

# 有効な SV リテラル形式: sized ('4'b1x0z'), unsized ('bx), fill ('x)
_SV_LITERAL_RE = re.compile(
    r"^[0-9]*'[sS]?[bBoOdDhH][0-9a-fA-FxXzZ?_]+$"
    r"|^'[01xXzZ]$",
)


def _coerce_yaml_sv_value(v, path: str):
    """YAML の set/check 値を検証して int または有効な SV リテラル文字列を返す。

    §18.7.1: 文字列の場合は基数つき形式（例: "4'b1x0z"、"'bx"）のみ許可。
    裸の "x"/"z" 等は ScenarioError。
    """
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        sv = v.strip()
        if _SV_LITERAL_RE.match(sv):
            return sv
        raise ScenarioError(
            f"{path} の値 {v!r} は不正です。"
            "整数または基数つきリテラル（\"4'b1x0z\"、\"'bx\"、\"'z\" 等）を使ってください。"
        )
    return int(v)


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
    set_values: Optional[Dict[str, object]] = None  # int or str ('x','z','4\'b...')
    random_spec: Optional[Dict[str, object]] = None
    offset_ns: int = 0          # §15.2: サイクル途中のオフセット (ns)


@dataclass
class ExpectedEntry:
    at: Union[int, Tuple[int, int]]
    check: Optional[Dict[str, object]] = None  # int or str (x/z/? pattern)
    expr: Optional[str] = None
    offset_ns: int = 0          # §15.2: サイクル途中のオフセット (ns)


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
    # §18.6: 乱数刺激の制約式リスト（SV 式サブセット）
    constraints: List[str] = field(default_factory=list)
    # §18.6: リジェクションサンプリング上限（None はデフォルト値を使用）
    max_constraint_attempts: Optional[int] = None


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
    _warnings: list = field(default_factory=list, compare=False)  # §15.12 WarningInfo
    # §18.6: top-level constraints (applies to all cases)
    constraints: List[str] = field(default_factory=list)
    max_constraint_attempts: Optional[int] = None


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

class ScenarioError(Exception):
    pass


def load_scenario(path: str) -> Scenario:
    """Load and validate a YAML scenario file.

    §13.2 backward compat: if no ``cases:`` key, the whole file is treated as
    a single case whose name = the file's stem (e.g. ``counter_spec``).
    §15.13: W_UNKNOWN_KEY is emitted for unknown keys at all YAML layers.
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

    # §15.13: 実際に使われたキー名を parent_path に反映
    _top_inp_key  = "inputs"  if "inputs"  in data else "stimulus"
    _top_exp_key  = "expected" if "expected" in data else "expect"
    s.inputs = _parse_inputs(
        data.get("inputs") or data.get("stimulus") or [],
        s._warnings, parent_path=_top_inp_key,
    )
    s.expected = _parse_expected(
        data.get("expected") or data.get("expect") or [],
        s._warnings, parent_path=_top_exp_key,
    )

    if data.get("model"):
        m = data["model"]
        s.model = ModelSpec(
            file=str(m["file"]),
            class_name=str(m.get("class", m.get("class_name", "Model"))),
            params=dict(m.get("params") or {}),
        )

    # §18.6: top-level constraints
    if data.get("constraints"):
        s.constraints = [str(c) for c in data["constraints"]]
    if data.get("max_constraint_attempts") is not None:
        mca = int(data["max_constraint_attempts"])
        if mca < 1:
            raise ScenarioError(f"max_constraint_attempts は 1 以上の整数が必要です: {mca}")
        s.max_constraint_attempts = mca

    # §13.2: Parse cases: list
    raw_cases = data.get("cases")
    if raw_cases is not None:
        seen_names: set = set()
        for i_rc, rc in enumerate(raw_cases):
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
            # §15.13: 実際に使われたキー名を parent_path に反映
            _inp_key = "inputs" if "inputs" in rc else "stimulus"
            _exp_key = "expected" if "expected" in rc else "expect"
            inp_raw = rc.get("inputs") or rc.get("stimulus") or []
            exp_raw = rc.get("expected") or rc.get("expect") or []
            case.inputs = _parse_inputs(
                inp_raw, s._warnings,
                parent_path=f"cases[{i_rc}].{_inp_key}",
            )
            case.expected = _parse_expected(
                exp_raw, s._warnings,
                parent_path=f"cases[{i_rc}].{_exp_key}",
            )

            if "model" in rc:
                m = rc["model"]
                case.model = ModelSpec(
                    file=str(m["file"]),
                    class_name=str(m.get("class", m.get("class_name", "Model"))),
                    params=dict(m.get("params") or {}),
                )
            # §18.6: case-level constraints
            if rc.get("constraints"):
                case.constraints = [str(c) for c in rc["constraints"]]
            if rc.get("max_constraint_attempts") is not None:
                mca = int(rc["max_constraint_attempts"])
                if mca < 1:
                    raise ScenarioError(f"max_constraint_attempts は 1 以上の整数が必要です: {mca}")
                case.max_constraint_attempts = mca
            s.cases.append(case)
    # else: no cases: key → legacy single-case (s.cases stays empty)

    # ----------------------------------------------------------------
    # §15.13: W_UNKNOWN_KEY — check all YAML layers AFTER parsing
    # ----------------------------------------------------------------
    # (constants defined below this function, forward-ref OK in Python)
    # トップレベルは path="" → "{key}: 不明なキーは無視されます"
    _warn_unknown(data, _KNOWN_TOP_KEYS, "", s._warnings)
    if isinstance(data.get("clock"), dict):
        _warn_unknown(data["clock"], _KNOWN_CLOCK_KEYS, "clock", s._warnings)
    if isinstance(data.get("reset"), dict):
        _warn_unknown(data["reset"], _KNOWN_RESET_KEYS, "reset", s._warnings)
    if isinstance(data.get("sim"), dict):
        _warn_unknown(data["sim"], _KNOWN_SIM_KEYS, "sim", s._warnings)
    if raw_cases is not None:
        for i_rc, rc in enumerate(raw_cases):
            _warn_unknown(rc, _KNOWN_CASE_KEYS, f"cases[{i_rc}]", s._warnings)
            if isinstance(rc.get("clock"), dict):
                _warn_unknown(rc["clock"], _KNOWN_CLOCK_KEYS,
                              f"cases[{i_rc}].clock", s._warnings)
            if isinstance(rc.get("reset"), dict):
                _warn_unknown(rc["reset"], _KNOWN_RESET_KEYS,
                              f"cases[{i_rc}].reset", s._warnings)

    return s


# ---------------------------------------------------------------------------
# §15.13: 既知キー定義（全階層）
# ---------------------------------------------------------------------------

# stimulus/expect アイテムの既知キー（§15.12 から継承）
_KNOWN_INPUT_KEYS = frozenset({"at", "set", "random", "offset_ns"})
_KNOWN_EXPECT_KEYS = frozenset({"at", "check", "expr", "offset_ns"})

# トップレベルの既知キー
_KNOWN_TOP_KEYS = frozenset({
    "version", "top",
    "clock", "reset", "sim",
    "inputs", "stimulus",   # aliases
    "expected", "expect",   # aliases
    "model",
    "skip", "skip_reason",
    "cases",
    "constraints", "max_constraint_attempts",  # §18.6
})

# clock/reset/sim の既知キー
_KNOWN_CLOCK_KEYS = frozenset({"signal", "period_ns"})
_KNOWN_RESET_KEYS = frozenset({"signal", "active_low", "duration_cycles"})
_KNOWN_SIM_KEYS   = frozenset({"cycles", "seed", "vcd"})

# cases[] 各ケースの直下の既知キー
_KNOWN_CASE_KEYS = frozenset({
    "name", "skip", "skip_reason",
    "seed", "cycles",
    "clock", "reset",
    "inputs", "stimulus",   # aliases
    "expected", "expect",   # aliases
    "model",
    "constraints", "max_constraint_attempts",  # §18.6
})


def _warn_unknown(data: dict, known: frozenset, path: str, warnings_out) -> None:
    """Emit W_UNKNOWN_KEY for any key in data not in known set.

    §15.13: message は「{path}.{key}: 不明なキーは無視されます」形式。
    path が空文字のときは「{key}: ...」（トップレベル）。
    """
    if warnings_out is None or not isinstance(data, dict):
        return
    from deltasim.result import WarningInfo
    for k in data:
        if k not in known:
            key_path = f"{path}.{k}" if path else k
            warnings_out.append(WarningInfo(
                message=f"{key_path}: 不明なキーは無視されます",
                code="W_UNKNOWN_KEY",
            ))


def _parse_inputs(raw: list, warnings_out=None, parent_path: str = "stimulus") -> List[InputEntry]:
    """Parse inputs list. Collects W_UNKNOWN_KEY warnings into warnings_out.

    §15.13: parent_path は呼び出し側が渡す（例: "stimulus", "cases[0].inputs"）。
    メッセージ例: "cases[0].stimulus[2].ofset_ns: 不明なキーは無視されます"
    """
    from deltasim.result import WarningInfo
    result = []
    for i, entry in enumerate(raw):
        if "at" not in entry:
            raise ScenarioError(f"inputs entry missing 'at': {entry}")
        at = _parse_at(entry["at"])
        offset = _parse_offset_ns(entry, i, "stimulus", warnings_out)
        inp = InputEntry(at=at, offset_ns=offset)
        if "set" in entry:
            # §18.7.1: 基数つきリテラル文字列のみ許可。裸の "x"/"z" は ScenarioError。
            inp.set_values = {
                k: _coerce_yaml_sv_value(v, f"stimulus/set.{k}")
                for k, v in entry["set"].items()
            }
        elif "random" in entry:
            inp.random_spec = dict(entry["random"])
        else:
            raise ScenarioError(f"inputs entry has neither 'set' nor 'random': {entry}")
        # W_UNKNOWN_KEY
        if warnings_out is not None:
            for k in entry:
                if k not in _KNOWN_INPUT_KEYS:
                    warnings_out.append(WarningInfo(
                        message=f"{parent_path}[{i}].{k}: 不明なキーは無視されます",
                        code="W_UNKNOWN_KEY",
                    ))
        result.append(inp)
    return result


def _parse_expected(raw: list, warnings_out=None, parent_path: str = "expect") -> List[ExpectedEntry]:
    """Parse expected list. Collects W_UNKNOWN_KEY warnings into warnings_out.

    §15.13: parent_path は呼び出し側が渡す（例: "expect", "cases[0].expected"）。
    """
    from deltasim.result import WarningInfo
    result = []
    for i, entry in enumerate(raw):
        if "at" not in entry:
            raise ScenarioError(f"expected entry missing 'at': {entry}")
        at = _parse_at(entry["at"])
        offset = _parse_offset_ns(entry, i, "expect", warnings_out)
        exp = ExpectedEntry(at=at, offset_ns=offset)
        if "check" in entry:
            # §18.7.1: 基数つきリテラル文字列のみ許可。裸の "x"/"z" は ScenarioError。
            exp.check = {
                k: _coerce_yaml_sv_value(v, f"expected/check.{k}")
                for k, v in entry["check"].items()
            }
        elif "expr" in entry:
            exp.expr = str(entry["expr"])
        else:
            raise ScenarioError(f"expected entry has neither 'check' nor 'expr': {entry}")
        # W_UNKNOWN_KEY
        if warnings_out is not None:
            for k in entry:
                if k not in _KNOWN_EXPECT_KEYS:
                    warnings_out.append(WarningInfo(
                        message=f"{parent_path}[{i}].{k}: 不明なキーは無視されます",
                        code="W_UNKNOWN_KEY",
                    ))
        result.append(exp)
    return result


def _parse_offset_ns(entry: dict, idx: int, kind: str, warnings_out) -> int:
    """Read offset_ns from entry dict (raw value; range not validated here)."""
    v = entry.get("offset_ns", 0)
    if v == 0 or v is None:
        return 0
    return v  # actual int/float validation done in validate_offset_ns()


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

def validate_offset_ns(scenario: "Scenario", spec_path: str = "") -> list:
    """§15.6: 全ケースの offset_ns を検証して ErrorInfo リストを返す。

    effective period_ns = case.clock.period_ns if set, else scenario.clock.period_ns
    """
    from deltasim.result import ErrorInfo
    errors = []

    def _check_items(items, case_name: str, kind: str, period: int):
        for i, entry in enumerate(items):
            off = entry.offset_ns
            # 整数かつ 0 <= off < period
            if not isinstance(off, int) or isinstance(off, bool):
                errors.append(ErrorInfo(
                    file=spec_path, severity="error", code="E_OFFSET_RANGE",
                    message=(
                        f"cases[{case_name}].{kind}[{i}]: "
                        f"offset_ns={off!r} は範囲外です"
                        f"（0 以上 {period} 未満の整数）"
                    ),
                ))
            elif not (0 <= off < period):
                errors.append(ErrorInfo(
                    file=spec_path, severity="error", code="E_OFFSET_RANGE",
                    message=(
                        f"cases[{case_name}].{kind}[{i}]: "
                        f"offset_ns={off} は範囲外です"
                        f"（0 以上 {period} 未満の整数）"
                    ),
                ))

    def _process_case(case_name: str, inputs, expected, period: int):
        _check_items(inputs, case_name, "stimulus", period)
        _check_items(expected, case_name, "expect", period)

    # top-level inputs/expected (legacy single-case)
    top_period = scenario.clock.period_ns
    if scenario.inputs or scenario.expected:
        _process_case(scenario._stem, scenario.inputs, scenario.expected, top_period)

    # cases
    for case in scenario.cases:
        eff_period = case.clock.period_ns if case.clock else top_period
        _process_case(
            case.name,
            case.inputs if case.inputs else scenario.inputs,
            case.expected if case.expected else scenario.expected,
            eff_period,
        )
    return errors


# ---------------------------------------------------------------------------
# Stimulus schedule
# ---------------------------------------------------------------------------

# §18.6: デフォルトのリジェクションサンプリング上限
DEFAULT_MAX_CONSTRAINT_ATTEMPTS = 10_000


class ConstraintError(Exception):
    """§18.6: 制約を満たす値が上限回数内に見つからなかった。"""
    code = "E_CONSTRAINT"


class ConstraintExprError(Exception):
    """§18.6: 制約式の構文エラーまたは未知の信号名。"""
    code = "E_CONSTRAINT_EXPR"


class StimulusSchedule:
    """Pre-computes the input values to apply at each (cycle, offset_ns).

    §15.2: keyed by (cycle, offset_ns) to support sub-cycle stimuli.
    §18.6: supports constraints: (rejection sampling, per-cycle scope).
    Backward-compatible: get(cycle) is equivalent to get(cycle, 0).

    §18.6 constraint scope:
    - Per (cycle, offset), collect ALL confirmed values (set + random).
    - Only evaluate constraints that mention >= 1 random signal for that cycle.
    - Re-sample all random signals for that cycle together until constraints pass.
    - Signal widths from signal_widths dict are used to mask values (SV semantics).
    """

    def __init__(
        self,
        inputs: List[InputEntry],
        total_cycles: int,
        seed: int = 0,
        constraints: Optional[List[str]] = None,
        max_constraint_attempts: Optional[int] = None,
        signal_widths: Optional[Dict[str, int]] = None,
        signal_signed: Optional[Dict[str, bool]] = None,
    ):
        self._rng = _random_mod.Random(seed)
        self._constraints = constraints or []
        self._max_attempts = (max_constraint_attempts
                              if max_constraint_attempts is not None
                              else DEFAULT_MAX_CONSTRAINT_ATTEMPTS)
        self._signal_widths = signal_widths or {}
        self._signal_signed = signal_signed or {}
        # §18.6 Fix 1: initial values for all known input ports = 0
        # This matches the engine initial state (SimEngine initialises all signals to 0).
        # Without this, constraints that reference a port not yet set would raise ExprError.
        self._initial_port_vals: Dict[str, int] = {name: 0 for name in self._signal_widths}
        # (cycle, offset_ns) -> {signal: value}
        self._schedule: Dict[tuple, Dict[str, int]] = {}
        self._build(inputs, total_cycles)

    def _build(self, inputs: List[InputEntry], total_cycles: int) -> None:
        """§18.6: 2-pass build — group by (cycle, offset), then sample with constraints.

        Fix 1: 保持値 (persistent_state) をコンテキストに含める。
          各サイクルに印加された値は次のサイクルに引き継がれ、制約評価で利用可能。

        Fix 2: sorted(all_keys) で RNG 消費順を (cycle, offset) 昇順に固定。
          同一キー内の乱数信号のサンプリング順は random_spec の記述順（dict 順）で固定。
        """
        # Pass 1: collect fixed and random specs per (cycle, offset)
        per_key_fixed: Dict[tuple, Dict[str, int]] = {}
        per_key_random: Dict[tuple, Dict[str, object]] = {}

        for entry in inputs:
            cycles = sorted(_expand_at(entry.at, total_cycles))
            offset = entry.offset_ns
            for cycle in cycles:
                key = (cycle, offset)
                if entry.set_values:
                    per_key_fixed.setdefault(key, {}).update(entry.set_values)
                elif entry.random_spec:
                    per_key_random.setdefault(key, {}).update(entry.random_spec)

        # Pass 2: process keys in SORTED order (Fix 2: deterministic RNG order)
        # Maintain persistent_state: last applied value for each input signal
        # so that constraints can reference signals set in previous cycles (Fix 1)
        # §18.6 Fix 1: start from known initial values (all input ports = 0)
        persistent_state: Dict[str, int] = dict(self._initial_port_vals)

        all_keys = set(per_key_fixed) | set(per_key_random)
        for key in sorted(all_keys):  # Fix 2: sorted → deterministic RNG order
            cycle, _ = key
            fixed_vals = per_key_fixed.get(key, {})
            random_specs = per_key_random.get(key, {})
            if not random_specs:
                combined = dict(fixed_vals)
            else:
                # Fix 1: Build base context = persistent_state + fixed_vals of this key
                base_ctx = dict(persistent_state)
                base_ctx.update(fixed_vals)
                sampled = self._sample_cycle_with_constraints(
                    random_specs, base_ctx, cycle
                )
                combined = dict(fixed_vals)
                combined.update(sampled)
            # Update persistent_state with confirmed values
            persistent_state.update(combined)
            self._schedule[key] = combined

    def _sample_cycle_with_constraints(
        self,
        random_specs: Dict[str, object],
        base_ctx: Dict[str, int],
        cycle: int,
    ) -> Dict[str, int]:
        """§18.6: サイクル単位でリジェクションサンプリングを行う（Fix 1 保持値込み）。

        - base_ctx: 保持値 + このサイクルの固定値（persistent_state + fixed_vals）
        - base_ctx + 乱数候補 (candidate) でコンテキストを作る。
        - 「その乱数信号名を少なくとも1つ含む制約」だけを評価する。
        - 乱数信号を含まない制約はそのサイクルでは対象外。
        """
        from deltasim.interp.expr import eval_bool, ExprError
        import re

        random_signal_names = set(random_specs.keys())

        if not self._constraints:
            return {sig: self._sample(spec) for sig, spec in random_specs.items()}

        # Determine active constraints (mention at least one random signal)
        def _mentions(cstr: str, sigs: set) -> bool:
            return any(bool(re.search(r'\b' + re.escape(s) + r'\b', cstr)) for s in sigs)

        active = [c for c in self._constraints if _mentions(c, random_signal_names)]
        if not active:
            # No constraint applies to this cycle's random signals
            return {sig: self._sample(spec) for sig, spec in random_specs.items()}

        for attempt in range(1, self._max_attempts + 1):
            candidate = {sig: self._sample(spec) for sig, spec in random_specs.items()}
            # Build eval context: base_ctx (persistent + fixed) + candidate, with width masking
            ctx = dict(base_ctx)
            ctx.update(candidate)
            ctx = self._mask_context(ctx)

            all_ok = True
            for cstr in active:
                try:
                    ok = eval_bool(cstr, ctx)
                except ExprError as exc:
                    raise ConstraintExprError(
                        f"制約式 '{cstr}' の評価に失敗しました: {exc}"
                    ) from exc
                if not ok:
                    all_ok = False
                    break
            if all_ok:
                return candidate

        raise ConstraintError(
            f"E_CONSTRAINT: サイクル {cycle} で制約 {active} を満たす値が "
            f"{self._max_attempts} 回以内に見つかりませんでした。"
        )

    def _mask_context(self, ctx: Dict[str, int]) -> Dict[str, int]:
        """§18.6: 信号幅に応じてビットマスクを適用する（SV semantics）。"""
        if not self._signal_widths:
            return ctx
        result = {}
        for sig, val in ctx.items():
            w = self._signal_widths.get(sig)
            if w and w > 0:
                masked = val & ((1 << w) - 1)
                if self._signal_signed.get(sig) and (masked >> (w - 1)):
                    masked -= (1 << w)
                result[sig] = masked
            else:
                result[sig] = val
        return result

    def get(self, cycle: int, offset_ns: int = 0) -> Dict[str, int]:
        """Input values to apply at (cycle, offset_ns). offset_ns=0 → posedge."""
        return self._schedule.get((cycle, offset_ns), {})

    def offsets_for_cycle(self, cycle: int) -> set:
        """All unique offset_ns values for a given cycle."""
        return {off for (c, off) in self._schedule if c == cycle}

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
