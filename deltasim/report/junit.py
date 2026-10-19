"""JUnit XML reporter.

Produces a JUnit-compatible XML file for CI integration.

Schema
------
<testsuites>
  <testsuite name="counter" tests="N" failures="M" errors="0" time="...">
    <testcase name="count@cycle11" classname="counter" time="0">
      <!-- PASS: no child elements -->
    </testcase>
    <testcase name="count@cycle12" classname="counter" time="0">
      <failure message="expected 2, got 1">
        count @ cycle 12: expected=2, actual=1
      </failure>
    </testcase>
  </testsuite>
</testsuites>
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List

from deltasim.tb.runner import CheckResult, TestRunResult


def write_junit(result: TestRunResult, path: str) -> None:
    """Write *result* to a JUnit XML file at *path*."""
    root = ET.Element("testsuites")
    _build_suite(root, result)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n')
        tree.write(f, encoding="unicode")
        f.write("\n")


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
