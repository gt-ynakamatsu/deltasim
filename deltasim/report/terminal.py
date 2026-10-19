"""Terminal reporter: human-readable test results."""

from __future__ import annotations

from typing import Optional

from deltasim.tb.runner import TestRunResult, CheckResult

_PASS = "\033[32mPASS\033[0m"
_FAIL = "\033[31mFAIL\033[0m"
_PASS_PLAIN = "PASS"
_FAIL_PLAIN = "FAIL"


def print_results(
    result: TestRunResult,
    verbose: bool = False,
    color: bool = True,
) -> None:
    """Print a summary (and optionally all checks) to stdout."""
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
