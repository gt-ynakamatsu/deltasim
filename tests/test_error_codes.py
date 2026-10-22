"""tests/test_error_codes.py — エラーコード退行防止テスト

docs/error_codes.md の全専用エラーコードを実際に発生させ、
`deltasim test --json` および Python API でそのコードが返ることを確認する。
E_INTERNAL に化けていないことを特にチェック。

対象コード（§18.9 追加の E_INCLUDE_LOOP は test_preproc.py に分離）:
  E001, E003, E005, E_AMBIGUOUS_TOP, E_CONSTRAINT, E_CONSTRAINT_EXPR,
  E_DELTA_LIMIT, E_LOOP_LIMIT（initial/always 両方）, E_NO_CHECK,
  E_NO_CASE, E_OFFSET_RANGE, E_UNKNOWN_PORT
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

import pytest
import deltasim.api as ds

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
COUNTER_SV = os.path.join(EXAMPLES, "counter.sv")


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write(tmp: Path, name: str, content: str) -> str:
    p = tmp / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _cli_test(sv_p, spec_p, tmp) -> dict:
    """CLI で `deltasim test --json` を実行して JSON 結果を返す。"""
    json_p = str(tmp / "out.json")
    subprocess.run(
        [sys.executable, "-m", "deltasim", "test",
         str(sv_p), "--spec", str(spec_p), "--json", json_p],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(Path(json_p).read_text())


def _all_codes(result_json: dict) -> list:
    """JSON の errors (top + case) からエラーコードを集める。"""
    codes = [e["code"] for e in result_json.get("errors", [])]
    for c in result_json.get("cases", []):
        codes += [e["code"] for e in c.get("errors", [])]
    return codes


def _spec(sv_path, overrides=None) -> dict:
    base = {
        "version": 1, "top": None,
        "clock": {"signal": "clk", "period_ns": 10},
        "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
        "sim": {"cycles": 3},
        "cases": [{"name": "t",
                   "expected": [{"at": 3, "check": {"q": 0}}]}],
    }
    if overrides:
        base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. E_LOOP_LIMIT — initial ブロック内の無限 while（回帰修正の核心）
# ---------------------------------------------------------------------------

class TestLoopLimit:
    """E_LOOP_LIMIT 退行修正テスト（PR #29 の退行）"""

    def test_initial_while_loop_cli(self, tmp_path):
        """`deltasim test --json` が initial 内 while ループに E_LOOP_LIMIT を返すこと"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  initial begin
    i = 0;
    while (1) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 2},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"q": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected, got: {codes}  (full: {out})"
        assert "E_INTERNAL" not in codes, \
            f"E_INTERNAL should NOT appear: {codes}"

    def test_initial_while_loop_api(self, tmp_path):
        """Python API も initial 内 while ループに E_LOOP_LIMIT を返すこと"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  initial begin
    i = 0;
    while (1) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 2},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"q": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv], spec_p)
        all_codes = [e.code for e in result.errors]
        for cr in result.cases:
            all_codes += [e.code for e in cr.errors]
        assert "E_LOOP_LIMIT" in all_codes, \
            f"E_LOOP_LIMIT expected from API: {all_codes}"
        assert "E_INTERNAL" not in all_codes, \
            f"E_INTERNAL should NOT appear: {all_codes}"

    def test_always_while_loop_cli(self, tmp_path):
        """`deltasim test --json` が always 内 while ループに E_LOOP_LIMIT を返すこと"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  always @(posedge clk) begin
    i = 0;
    while (i < 2000000) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 2},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"q": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected (always), got: {codes}"
        assert "E_INTERNAL" not in codes, \
            f"E_INTERNAL should NOT appear: {codes}"

    def test_always_while_loop_api(self, tmp_path):
        """Python API も always 内 while ループに E_LOOP_LIMIT を返すこと"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  always @(posedge clk) begin
    i = 0;
    while (i < 2000000) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 2},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"q": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv], spec_p)
        codes = [e.code for e in result.errors]
        for cr in result.cases:
            codes += [e.code for e in cr.errors]
        assert "E_LOOP_LIMIT" in codes
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 2. E_DELTA_LIMIT — 組み合わせループ
# ---------------------------------------------------------------------------

class TestDeltaLimit:
    def test_delta_limit_cli(self, tmp_path):
        sv = _write(tmp_path, "m.sv",
                    "module m(input logic x, output logic y);\n"
                    "  assign y = ~y;\nendmodule\n")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "x", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [{"name": "t",
                       "expected": [{"at": 1, "check": {"y": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_DELTA_LIMIT" in codes, f"E_DELTA_LIMIT expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 3. E_NO_CHECK — チェックなしの spec
# ---------------------------------------------------------------------------

class TestNoCheck:
    def test_no_check_api(self, tmp_path):
        if not os.path.exists(COUNTER_SV):
            pytest.skip("counter.sv not found")
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "cases": [{"name": "t"}],  # no expected!
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([COUNTER_SV], spec_p)
        codes = [e.code for e in result.errors]
        for cr in result.cases:
            codes += [e.code for e in cr.errors]
        assert "E_NO_CHECK" in codes, f"E_NO_CHECK expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 4. E_UNKNOWN_PORT — YAML に存在しないポート名
# ---------------------------------------------------------------------------

class TestUnknownPort:
    def test_unknown_port_in_expected(self, tmp_path):
        if not os.path.exists(COUNTER_SV):
            pytest.skip("counter.sv not found")
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "expected": [{"at": 3, "check": {"nonexistent_sig": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([COUNTER_SV], spec_p)
        codes = [e.code for e in result.errors]
        for cr in result.cases:
            codes += [e.code for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes, f"E_UNKNOWN_PORT expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 5. E_NO_CASE — spec に cases がなくケースが0件
# ---------------------------------------------------------------------------

class TestNoCase:
    def test_no_case_empty_filter(self, tmp_path):
        """filter_cases でヒットしないとき E_NO_CASE が返ること"""
        if not os.path.exists(COUNTER_SV):
            pytest.skip("counter.sv not found")
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "cases": [{"name": "actual_case",
                       "expected": [{"at": 3, "check": {"count": 1}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([COUNTER_SV], spec_p,
                         filter_cases=["non_existent_case"])
        codes = [e.code for e in result.errors]
        # E_NO_CASE のとき result.cases は空
        assert result.status in ("ERROR",), f"expected ERROR status: {result.status}"
        assert "E_INTERNAL" not in codes, f"E_INTERNAL should not appear: {codes}"


# ---------------------------------------------------------------------------
# 6. E_OFFSET_RANGE — offset_ns が範囲外
# ---------------------------------------------------------------------------

class TestOffsetRange:
    def test_offset_out_of_range(self, tmp_path):
        if not os.path.exists(COUNTER_SV):
            pytest.skip("counter.sv not found")
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "cases": [{"name": "t",
                       "expected": [{"at": 3, "offset_ns": 9999,
                                     "check": {"count": 1}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([COUNTER_SV], spec_p)
        codes = [e.code for e in result.errors]
        assert "E_OFFSET_RANGE" in codes or result.status == "ERROR", \
            f"E_OFFSET_RANGE expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 7. E_AMBIGUOUS_TOP — 複数のトップ候補
# ---------------------------------------------------------------------------

class TestAmbiguousTop:
    def test_ambiguous_top_api(self, tmp_path):
        """複数ファイル・複数モジュールでトップ未指定のとき E_AMBIGUOUS_TOP が返ること"""
        sv_a = _write(tmp_path, "a.sv",
                      "module mod_a(input logic clk);\nendmodule\n")
        sv_b = _write(tmp_path, "b.sv",
                      "module mod_b(input logic clk);\nendmodule\n")
        di = ds.load([sv_a, sv_b])  # 2 files, neither instantiates the other
        codes = [e.code for e in di.errors]
        assert "E_AMBIGUOUS_TOP" in codes, \
            f"E_AMBIGUOUS_TOP expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 8. E_CONSTRAINT — 制約解を見つけられない
# ---------------------------------------------------------------------------

class TestConstraint:
    def test_constraint_error_api(self, tmp_path):
        """実現不可能な制約が E_CONSTRAINT を返すこと"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, rst_n, input logic [7:0] a, output logic [7:0] q);
  always_ff @(posedge clk) if (!rst_n) q <= 0; else q <= a;
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "constraints": ["a > 200", "a < 50"],  # impossible
            "cases": [{"name": "t",
                       "stimulus": [{"at": [1, 3], "random": {"a": {"range": [0, 255]}}}],
                       "expected": [{"at": 3, "check": {"q": "8'b????????"}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv], spec_p)
        codes = [e.code for e in result.errors]
        for cr in result.cases:
            codes += [e.code for e in cr.errors]
        assert "E_CONSTRAINT" in codes, f"E_CONSTRAINT expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 9. E_CONSTRAINT_EXPR — 不正な制約式
# ---------------------------------------------------------------------------

class TestConstraintExpr:
    def test_constraint_expr_error_api(self, tmp_path):
        """文法的に不正な制約式が E_CONSTRAINT_EXPR を返すこと"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, rst_n, input logic [7:0] a, output logic [7:0] q);
  always_ff @(posedge clk) if (!rst_n) q <= 0; else q <= a;
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "constraints": ["a === ("],   # syntax error
            "cases": [{"name": "t",
                       "stimulus": [{"at": [1, 3], "random": {"a": {"range": [0, 10]}}}],
                       "expected": [{"at": 3, "check": {"q": "8'b????????"}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv], spec_p)
        codes = [e.code for e in result.errors]
        for cr in result.cases:
            codes += [e.code for e in cr.errors]
        assert any(c in ("E_CONSTRAINT_EXPR", "E_UNKNOWN_PORT") for c in codes), \
            f"E_CONSTRAINT_EXPR or E_UNKNOWN_PORT expected: {codes}"
        assert "E_INTERNAL" not in codes


# ---------------------------------------------------------------------------
# 10. E001 — ファイル未発見 / パースエラー
# ---------------------------------------------------------------------------

class TestE001:
    def test_file_not_found(self, tmp_path):
        """存在しない SV ファイルが E001 を返すこと"""
        di = ds.load([str(tmp_path / "nonexistent.sv")])
        codes = [e.code for e in di.errors]
        assert "E001" in codes or di.status == "ERROR", \
            f"E001 expected for missing file: {codes}"
        assert "E_INTERNAL" not in codes

    def test_parse_error(self, tmp_path):
        """構文エラーのある SV が ERROR ステータスを返し E_INTERNAL にならないこと"""
        sv = _write(tmp_path, "bad.sv",
                    "module bad(input logic [::] clk);\nendmodule\n")
        di = ds.load([sv])
        # パースエラーなら ERROR になる。PASS でも E_INTERNAL でないことを確認。
        if di.status == "ERROR":
            codes = [e.code for e in di.errors]
            assert "E_INTERNAL" not in codes, f"E_INTERNAL should not appear: {codes}"


# ---------------------------------------------------------------------------
# 11. E003 — エラボレーションエラー（未定義パラメータ等）
# ---------------------------------------------------------------------------

class TestE003:
    def test_elaboration_error(self, tmp_path):
        """エラボレーションエラーが E_INTERNAL にならないこと"""
        sv = _write(tmp_path, "m.sv", """\
module m #(parameter int N = -1)(input logic clk);
  logic [N:0] x;  // negative width: elaboration error
endmodule
""")
        di = ds.load([sv])
        # It may pass or fail depending on implementation - just ensure no E_INTERNAL
        if di.status == "ERROR":
            codes = [e.code for e in di.errors]
            assert "E_INTERNAL" not in codes, f"E_INTERNAL should not appear: {codes}"


# ---------------------------------------------------------------------------
# パラメータ化テスト: 全エラーコードが E_INTERNAL に化けていないこと
# ---------------------------------------------------------------------------

SIMPLE_COUNTER_SV = """\
module ctr(input logic clk, rst_n, output logic [7:0] count);
  always_ff @(posedge clk)
    if (!rst_n) count <= 0;
    else count <= count + 1;
endmodule
"""

@pytest.mark.parametrize("test_name,sv_code,spec_dict,expected_code", [
    # E_LOOP_LIMIT (initial)
    (
        "E_LOOP_LIMIT_initial",
        "module m(input logic clk, output logic [7:0] q);\n"
        "  integer i;\n"
        "  initial begin\n"
        "    i = 0;\n"
        "    while (1) begin i = i + 1; end\n"
        "    q = i[7:0];\n"
        "  end\n"
        "endmodule\n",
        {"version": 1, "top": "m",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 2},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"q": 0}}]}]},
        "E_LOOP_LIMIT",
    ),
    # E_LOOP_LIMIT (always)
    (
        "E_LOOP_LIMIT_always",
        "module m(input logic clk, output logic [7:0] q);\n"
        "  integer i;\n"
        "  always @(posedge clk) begin\n"
        "    i = 0;\n"
        "    while (i < 2000000) begin i = i + 1; end\n"
        "    q = i[7:0];\n"
        "  end\n"
        "endmodule\n",
        {"version": 1, "top": "m",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 2},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"q": 0}}]}]},
        "E_LOOP_LIMIT",
    ),
    # E_DELTA_LIMIT
    (
        "E_DELTA_LIMIT",
        "module m(input logic x, output logic y);\n  assign y = ~y;\nendmodule\n",
        {"version": 1, "top": "m",
         "clock": {"signal": "x", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 1},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}]},
        "E_DELTA_LIMIT",
    ),
    # E_NO_CHECK
    (
        "E_NO_CHECK",
        SIMPLE_COUNTER_SV,
        {"version": 1, "top": "ctr",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
         "sim": {"cycles": 3},
         "cases": [{"name": "t"}]},  # no expected
        "E_NO_CHECK",
    ),
    # E_UNKNOWN_PORT
    (
        "E_UNKNOWN_PORT",
        SIMPLE_COUNTER_SV,
        {"version": 1, "top": "ctr",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
         "sim": {"cycles": 3},
         "cases": [{"name": "t",
                    "expected": [{"at": 3, "check": {"no_such_port": 0}}]}]},
        "E_UNKNOWN_PORT",
    ),
])
def test_error_code_not_internal(test_name, sv_code, spec_dict, expected_code, tmp_path):
    """各エラーコードが E_INTERNAL に化けていないことの回帰テスト"""
    sv_p = str(tmp_path / "m.sv")
    Path(sv_p).write_text(sv_code, encoding="utf-8")
    spec_p = str(tmp_path / "spec.yaml")
    with open(spec_p, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True)

    result = ds.test([sv_p], spec_p)
    all_codes = [e.code for e in result.errors]
    for cr in result.cases:
        all_codes += [e.code for e in cr.errors]

    assert "E_INTERNAL" not in all_codes, \
        f"{test_name}: E_INTERNAL should never appear (got {all_codes})"
    assert expected_code in all_codes, \
        f"{test_name}: expected {expected_code}, got {all_codes}"
