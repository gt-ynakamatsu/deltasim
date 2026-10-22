"""Terminal reporter: human-readable test results."""

from __future__ import annotations
from typing import Union

from deltasim.tb.runner import TestRunResult, CheckResult
from deltasim.result import TestResult

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"
_PASS_PLAIN = "PASS"
_FAIL_PLAIN = "FAIL"


def print_results(
    result: TestRunResult,
    verbose: bool = False,
    color: bool = True,
) -> None:
    """Print a summary (and optionally all checks) to stdout (legacy API)."""
    P = _PASS if color else _PASS_PLAIN
    F = _FAIL if color else _FAIL_PLAIN

    print(f"\n{'='*60}")
    print(f"  deltasim test — module: {result.module_name}")
    print(f"{'='*60}")

    if verbose or not result.passed:
        for cr in result.checks:
            status = P if cr.passed else F
            src = f"[{cr.source}]" if cr.source != "spec" else ""
            print(
                f"  {status} cycle={cr.cycle:4d}  {cr.signal}{src}: "
                f"expected={cr.expected!r}  actual={cr.actual}"
            )

    total = len(result.checks)
    print(f"{'='*60}")
    if total == 0:
        print("  (no expected-value checks defined)")
    else:
        print(
            f"  {result.n_pass}/{total} PASS  "
            f"{result.n_fail}/{total} FAIL"
        )
        print(f"  Overall: {P if result.passed else F}")
    print(f"{'='*60}\n")

    if result.log:
        print("--- Simulation log ---")
        for line in result.log:
            print(f"  {line}")
        print()


def print_results_v2(
    result: "TestResult",
    verbose: bool = False,
    color: bool = True,
) -> None:
    """Print a summary from a TestResult (M5+ API). §13.2 multi-case aware."""
    P = _PASS if color else _PASS_PLAIN
    F = _FAIL if color else _FAIL_PLAIN
    E = ("\033[33mERROR\033[0m" if color else "ERROR")
    STATUS_CLR = {"PASS": P, "FAIL": F, "ERROR": E, "SKIP": "SKIP"}

    print(f"\n{'='*60}")
    print(f"  deltasim test — module: {result.module}")
    print(f"{'='*60}")

    if result.errors:
        for err in result.errors:
            sev = err.severity.upper()
            loc = f"{err.file}:{err.line}" if err.file else ""
            print(f"  [{sev}] {err.code}: {loc} {err.message}")

    # §15.12: top-level warnings (W_UNKNOWN_KEY 等)
    for w in getattr(result, "warnings", []):
        try:
            print(f"  [WARNING] {w.code}: {w.message}")
        except UnicodeEncodeError:
            # Windows で日本語メッセージが端末エンコーディングと合わない場合の保護
            safe_msg = w.message.encode("ascii", errors="replace").decode("ascii")
            print(f"  [WARNING] {w.code}: {safe_msg}")

    # Print per-case details if multi-case, or flat if single-case
    if result.cases:
        for cr in result.cases:
            case_st = STATUS_CLR.get(cr.status, cr.status)
            print(f"  [{case_st}] case: {cr.name}  seed={cr.seed}  elapsed={cr.elapsed_ms:.0f}ms")
            # §15.12: W_UNKNOWN_KEY warnings
            for w in getattr(cr, "warnings", []):
                print(f"  [WARNING] {w.code}: {w.message}", flush=True)
            if verbose or cr.status != "PASS":
                for ci in cr.checks:
                    st = STATUS_CLR.get(ci.status, ci.status)
                    src = f"[{ci.source}]" if ci.source != "spec" else ""
                    msg = f"  → {ci.message}" if ci.message and ci.status != "PASS" else ""
                    # §15.7.6: offset_ns>0 → "cycle=4 +3ns (38ns)"
                    off = getattr(ci, "offset_ns", 0)
                    cycle_str = (f"cycle={ci.cycle:4d} +{off}ns ({ci.time_ns}ns)"
                                 if off else f"cycle={ci.cycle:4d} ({ci.time_ns}ns)")
                    print(
                        f"    {st} {cycle_str}  {ci.signal}{src}: "
                        f"expected={ci.expected!r}  actual={ci.actual!r}{msg}"
                    )
    else:
        # Legacy flat display (no cases[])
        if verbose or result.status != "PASS":
            for ci in result.checks:
                st = STATUS_CLR.get(ci.status, ci.status)
                src = f"[{ci.source}]" if ci.source != "spec" else ""
                msg = f"  → {ci.message}" if ci.message and ci.status != "PASS" else ""
                off = getattr(ci, "offset_ns", 0)
                cycle_str = (f"cycle={ci.cycle:4d} +{off}ns ({ci.time_ns}ns)"
                             if off else f"cycle={ci.cycle:4d} ({ci.time_ns}ns)")
                print(
                    f"  {st} {cycle_str}  {ci.signal}{src}: "
                    f"expected={ci.expected!r}  actual={ci.actual!r}{msg}"
                )

    total_checks = len(result.checks)
    n_pass = result.n_pass
    n_fail = result.n_fail
    print(f"{'='*60}")
    if total_checks == 0 and not result.errors:
        n_cases = len(result.cases)
        print(f"  (チェックなし" + (f"、{n_cases} ケース" if n_cases else "") + ")")
    else:
        print(
            f"  {n_pass}/{total_checks} PASS  {n_fail}/{total_checks} FAIL"
            f"  elapsed={result.elapsed_ms:.0f}ms"
        )
        print(f"  Overall: {STATUS_CLR.get(result.status, result.status)}")
    if result.cancelled:
        print("  [キャンセルされました]")
    print(f"{'='*60}\n")

    if result.log:
        print("--- Simulation log ---")
        for line in result.log:
            print(f"  {line}")
        print()
