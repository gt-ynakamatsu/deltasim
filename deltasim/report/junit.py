"""JUnit XML reporter — supports both TestRunResult (legacy) and TestResult (M5)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Union

from deltasim.tb.runner import TestRunResult, CheckResult


def write_junit(result: TestRunResult, path: str) -> None:
    """Write *result* (legacy TestRunResult) to a JUnit XML file at *path*."""
    root = ET.Element("testsuites")
    _build_suite(root, result)
    _write_tree(root, path)


def write_junit_v2(result, path: str) -> None:
    """Write a TestResult (M5 API) to a JUnit XML file at *path*."""
    from deltasim.result import TestResult, CheckInfo
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", result.module)
    suite.set("tests", str(len(result.checks)))
    suite.set("failures", str(result.n_fail))
    suite.set("errors", "0")

    for ci in result.checks:
        tc = ET.SubElement(suite, "testcase")
        tc.set("name", ci.name)
        tc.set("classname", result.module)
        tc.set("time", f"{ci.elapsed_ms / 1000:.4f}")
        if ci.status != "PASS":
            failure = ET.SubElement(tc, "failure")
            failure.set("message", ci.message or f"expected {ci.expected!r}, got {ci.actual!r}")
            failure.text = (
                f"{ci.signal} @ cycle {ci.cycle}  time={ci.time_ns}ns\n"
                f"  expected : {ci.expected}\n"
                f"  actual   : {ci.actual}"
            )

    _write_tree(root, path)


def _build_suite(root: ET.Element, result: TestRunResult) -> ET.Element:
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", result.module_name)
    suite.set("tests", str(len(result.checks)))
    suite.set("failures", str(result.n_fail))
    suite.set("errors", "0")

    for cr in result.checks:
        _add_testcase(suite, cr, result.module_name)

    return suite


def _add_testcase(suite: ET.Element, cr: CheckResult, classname: str) -> None:
    tc = ET.SubElement(suite, "testcase")
    sig = cr.signal.replace("<", "").replace(">", "")
    tc.set("name", f"{sig}@cycle{cr.cycle}[{cr.source}]")
    tc.set("classname", classname)
    tc.set("time", "0")

    if not cr.passed:
        msg = f"expected {cr.expected!r}, got {cr.actual!r}"
        failure = ET.SubElement(tc, "failure")
        failure.set("message", msg)
        failure.text = (
            f"{cr.signal} @ cycle {cr.cycle}: "
            f"expected={cr.expected!r}, actual={cr.actual!r}"
        )


def _write_tree(root: ET.Element, path: str) -> None:
    from pathlib import Path
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(f, encoding="unicode")
        f.write("\n")
