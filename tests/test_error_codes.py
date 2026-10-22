"""tests/test_error_codes.py — エラーコード退行防止テスト

docs/error_codes.md の全専用エラーコードを実際に発生させ、
`deltasim test --json` (CLI) および Python API でそのコードが返ることを確認する。
E_INTERNAL に化けていないことを特にチェック。

各テストは期待コードを1つに絞って assert する。
`any(...)` や `or status == "ERROR"` は禁止。

§18.9 追加の E_INCLUDE_LOOP は test_preproc.py に分離。
LLM 系 (E_LLM_*) は LLM 未設定環境でしか発生しないため test_llm.py に分離。

原因調査メモ:
  b53531d..ea48e9d の全コミットで initial-block 内 while ループが E_INTERNAL になる。
  _InitialScheduler.add() → _schedule_runner() が時刻 0 で即座に実行され、
  LoopLimitError が try-except LoopLimitError ブロックの外に伝播する。
  この構造は §18.1 (0fd2e25) で _InitialScheduler が導入された時点から存在していた。
  「b53531d では正しく E_LOOP_LIMIT」という前提は incorrect。
  always ブロック内のループは main try-except 内で実行されるため、元から正常。
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


def _cli_test(sv_p: str, spec_p: str, tmp: Path, extra_args=()) -> dict:
    """CLI で `deltasim test --json` を実行して JSON 結果 dict を返す。"""
    json_p = str(tmp / "out.json")
    subprocess.run(
        [sys.executable, "-m", "deltasim", "test",
         str(sv_p), "--spec", str(spec_p), "--json", json_p, *extra_args],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    # Windows では open() のデフォルトエンコーディングが cp932/cp1252 になるため
    # 明示的に utf-8 を指定する
    return json.loads(Path(json_p).read_text(encoding="utf-8"))


def _api_test(sv_p: str, spec_p: str, **kw) -> ds.TestResult:
    """Python API で ds.test() を実行して TestResult を返す。"""
    return ds.test([sv_p], spec_p, **kw)


def _all_codes(result) -> list:
    """TestResult または CLI JSON から全エラーコードを収集する。"""
    if isinstance(result, dict):
        codes = [e["code"] for e in result.get("errors", [])]
        for c in result.get("cases", []):
            codes += [e["code"] for e in c.get("errors", [])]
    else:
        codes = [e.code for e in result.errors]
        for c in result.cases:
            codes += [e.code for e in c.errors]
    return codes


# ---------------------------------------------------------------------------
# 共通 SV
# ---------------------------------------------------------------------------

_COUNTER_SV = """\
module ctr(input logic clk, rst_n, output logic [7:0] count);
  always_ff @(posedge clk)
    if (!rst_n) count <= 0;
    else count <= count + 1;
endmodule
"""

_COUNTER_SPEC_BASE = {
    "version": 1, "top": "ctr",
    "clock": {"signal": "clk", "period_ns": 10},
    "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
    "sim": {"cycles": 3},
}


# ===========================================================================
# 1. E_LOOP_LIMIT — 退行修正の核心
# ===========================================================================

class TestLoopLimit:
    """
    E_LOOP_LIMIT 退行修正テスト。

    initial ブロック内 while ループが E_INTERNAL を返していた退行を修正。
    _InitialScheduler.add() が try-except LoopLimitError の外で例外を投げていた。
    """

    _INIT_WHILE_SV = """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  initial begin
    i = 0;
    while (1) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
"""
    _INIT_SPEC = {
        "version": 1, "top": "m",
        "clock": {"signal": "clk", "period_ns": 10},
        "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
        "sim": {"cycles": 2},
        "cases": [{"name": "t", "expected": [{"at": 1, "check": {"q": 0}}]}],
    }
    _ALWAYS_WHILE_SV = """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  always @(posedge clk) begin
    i = 0;
    while (i < 2000000) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
"""

    def test_initial_while_cli(self, tmp_path):
        """CLI: initial 内 while → code == "E_LOOP_LIMIT"（退行修正）"""
        sv_p = _write(tmp_path, "m.sv", self._INIT_WHILE_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._INIT_SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected from CLI, got: {codes}"
        assert "E_INTERNAL" not in codes, \
            f"E_INTERNAL must NOT appear: {codes}"

    def test_initial_while_api(self, tmp_path):
        """API: initial 内 while → code == "E_LOOP_LIMIT"（退行修正）"""
        sv_p = _write(tmp_path, "m.sv", self._INIT_WHILE_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._INIT_SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected from API, got: {codes}"
        assert "E_INTERNAL" not in codes, \
            f"E_INTERNAL must NOT appear: {codes}"

    def test_always_while_cli(self, tmp_path):
        """CLI: always 内 while → code == "E_LOOP_LIMIT"（元から正常）"""
        sv_p = _write(tmp_path, "m.sv", self._ALWAYS_WHILE_SV)
        spec = dict(self._INIT_SPEC)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected (always), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_always_while_api(self, tmp_path):
        """API: always 内 while → code == "E_LOOP_LIMIT"（元から正常）"""
        sv_p = _write(tmp_path, "m.sv", self._ALWAYS_WHILE_SV)
        spec = dict(self._INIT_SPEC)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_LOOP_LIMIT" in codes
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 2. E_DELTA_LIMIT
# ===========================================================================

_COMB_LOOP_SV = """\
module m(input logic x, output logic y);
  assign y = ~y;
endmodule
"""
_COMB_LOOP_SPEC = {
    "version": 1, "top": "m",
    "clock": {"signal": "x", "period_ns": 10},
    "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
    "sim": {"cycles": 1},
    "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}],
}


class TestDeltaLimit:
    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COMB_LOOP_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(_COMB_LOOP_SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_DELTA_LIMIT" in codes, f"E_DELTA_LIMIT expected: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COMB_LOOP_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(_COMB_LOOP_SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_DELTA_LIMIT" in codes
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 3. E_NO_CHECK
# ===========================================================================

class TestNoCheck:
    _SPEC_NO_CHECK = {
        **_COUNTER_SPEC_BASE,
        "cases": [{"name": "t"}],   # no expected → E_NO_CHECK
    }

    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC_NO_CHECK, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_NO_CHECK" in codes, f"E_NO_CHECK expected: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC_NO_CHECK, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_NO_CHECK" in codes, f"E_NO_CHECK expected: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 4. E_UNKNOWN_PORT
# ===========================================================================

class TestUnknownPort:
    _SPEC = {
        **_COUNTER_SPEC_BASE,
        "cases": [{"name": "t",
                   "expected": [{"at": 3, "check": {"nonexistent": 0}}]}],
    }

    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_UNKNOWN_PORT" in codes, f"E_UNKNOWN_PORT expected: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_UNKNOWN_PORT" in codes
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 5. E_NO_CASE — filter_cases でヒットゼロ
# ===========================================================================

class TestNoCase:
    _SPEC = {
        **_COUNTER_SPEC_BASE,
        "cases": [{"name": "real_case",
                   "expected": [{"at": 3, "check": {"count": 1}}]}],
    }

    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path,
                        extra_args=["--case", "nonexistent_case"])
        codes = _all_codes(out)
        assert "E_NO_CASE" in codes, \
            f"E_NO_CASE expected (filter yielded no cases): {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC, f)
        result = _api_test(sv_p, spec_p, filter_cases=["nonexistent_case"])
        codes = _all_codes(result)
        assert "E_NO_CASE" in codes, \
            f"E_NO_CASE expected from API: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 6. E_OFFSET_RANGE — offset_ns が period_ns 以上
# ===========================================================================

class TestOffsetRange:
    _SPEC = {
        **_COUNTER_SPEC_BASE,
        "cases": [{"name": "t",
                   "expected": [{"at": 3, "offset_ns": 9999,
                                  "check": {"count": 1}}]}],
    }

    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_OFFSET_RANGE" in codes, \
            f"E_OFFSET_RANGE expected: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _COUNTER_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(self._SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_OFFSET_RANGE" in codes, \
            f"E_OFFSET_RANGE expected from API: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 7. E_AMBIGUOUS_TOP — 複数トップ候補
# ===========================================================================

class TestAmbiguousTop:
    def test_cli(self, tmp_path):
        sv_a = _write(tmp_path, "a.sv",
                      "module mod_a(input logic clk);\nendmodule\n")
        sv_b = _write(tmp_path, "b.sv",
                      "module mod_b(input logic clk);\nendmodule\n")
        result = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv_a, sv_b],
            capture_output=True, text=True, timeout=15,
        )
        # parse コマンドはエラー時に非ゼロ終了し stderr に E_AMBIGUOUS_TOP を出す
        assert result.returncode != 0, "should fail with ambiguous top"
        assert "E_AMBIGUOUS_TOP" in result.stderr, \
            f"E_AMBIGUOUS_TOP expected in stderr: {result.stderr!r}"

    def test_api(self, tmp_path):
        sv_a = _write(tmp_path, "a.sv",
                      "module mod_a(input logic clk);\nendmodule\n")
        sv_b = _write(tmp_path, "b.sv",
                      "module mod_b(input logic clk);\nendmodule\n")
        di = ds.load([sv_a, sv_b])
        codes = [e.code for e in di.errors]
        assert "E_AMBIGUOUS_TOP" in codes, \
            f"E_AMBIGUOUS_TOP expected: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 8. E_CONSTRAINT — 実現不可能な制約
# ===========================================================================

_CONSTRAINT_SV = """\
module m(input logic clk, rst_n, input logic [7:0] a, output logic [7:0] q);
  always_ff @(posedge clk) if (!rst_n) q <= 0; else q <= a;
endmodule
"""
_IMPOSSIBLE_SPEC = {
    "version": 1, "top": "m",
    "clock": {"signal": "clk", "period_ns": 10},
    "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
    "sim": {"cycles": 3},
    "constraints": ["a > 200", "a < 50"],  # impossible
    "cases": [{"name": "t",
               "stimulus": [{"at": [1, 3], "random": {"a": {"range": [0, 255]}}}],
               "expected": [{"at": 3, "check": {"q": "8'b????????"}}]}],
}


class TestConstraint:
    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _CONSTRAINT_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(_IMPOSSIBLE_SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_CONSTRAINT" in codes, f"E_CONSTRAINT expected: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _CONSTRAINT_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(_IMPOSSIBLE_SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_CONSTRAINT" in codes
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 9. E_CONSTRAINT_EXPR — 制約式の構文エラー
#    「a === (」はトークナイズ可能だが eval 時に ExprError → E_CONSTRAINT_EXPR
# ===========================================================================

_CEXPR_SPEC = {
    "version": 1, "top": "m",
    "clock": {"signal": "clk", "period_ns": 10},
    "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
    "sim": {"cycles": 3},
    "constraints": ["a === ("],   # unmatched paren → ExprError at eval time
    "cases": [{"name": "t",
               "stimulus": [{"at": [1, 3], "random": {"a": {"range": [0, 10]}}}],
               "expected": [{"at": 3, "check": {"q": "8'b????????"}}]}],
}


class TestConstraintExpr:
    def test_cli(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _CONSTRAINT_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(_CEXPR_SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_CONSTRAINT_EXPR" in codes, \
            f"E_CONSTRAINT_EXPR expected: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        sv_p = _write(tmp_path, "m.sv", _CONSTRAINT_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(_CEXPR_SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_CONSTRAINT_EXPR" in codes, \
            f"E_CONSTRAINT_EXPR expected from API: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 10. E001 — ファイル未発見
# ===========================================================================

class TestE001:
    def test_missing_file_cli(self, tmp_path):
        """存在しない SV ファイルが E001 を返す（CLI）"""
        result = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse",
             str(tmp_path / "ghost.sv")],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0
        assert "E001" in result.stderr, \
            f"E001 expected in stderr: {result.stderr!r}"

    def test_missing_file_api(self, tmp_path):
        """存在しない SV ファイルが E001 を返す（API）"""
        di = ds.load([str(tmp_path / "ghost.sv")])
        assert di.status == "ERROR"
        codes = [e.code for e in di.errors]
        assert "E001" in codes, f"E001 expected: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 11. E002 — モジュールが見つからない
# ===========================================================================

class TestE002:
    def test_wrong_top_api(self, tmp_path):
        """`--top` に存在しないモジュール名を指定すると E002（API）"""
        sv_p = _write(tmp_path, "m.sv",
                      "module real_mod(input logic clk);\nendmodule\n")
        di = ds.load([sv_p], top="nonexistent_module")
        assert di.status == "ERROR"
        codes = [e.code for e in di.errors]
        assert "E002" in codes, f"E002 expected: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# パラメータ化テスト: 全コードを CLI + API 両方でチェック
# ===========================================================================

_PARAM_CASES = [
    pytest.param(
        "E_LOOP_LIMIT_initial",
        """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  initial begin
    i = 0;
    while (1) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
""",
        {"version": 1, "top": "m",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 2},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"q": 0}}]}]},
        "E_LOOP_LIMIT",
        id="E_LOOP_LIMIT_initial",
    ),
    pytest.param(
        "E_LOOP_LIMIT_always",
        """\
module m(input logic clk, output logic [7:0] q);
  integer i;
  always @(posedge clk) begin
    i = 0;
    while (i < 2000000) begin i = i + 1; end
    q = i[7:0];
  end
endmodule
""",
        {"version": 1, "top": "m",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 2},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"q": 0}}]}]},
        "E_LOOP_LIMIT",
        id="E_LOOP_LIMIT_always",
    ),
    pytest.param(
        "E_DELTA_LIMIT",
        "module m(input logic x, output logic y);\n  assign y = ~y;\nendmodule\n",
        {"version": 1, "top": "m",
         "clock": {"signal": "x", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 1},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}]},
        "E_DELTA_LIMIT",
        id="E_DELTA_LIMIT",
    ),
    pytest.param(
        "E_NO_CHECK",
        _COUNTER_SV,
        {**_COUNTER_SPEC_BASE, "cases": [{"name": "t"}]},
        "E_NO_CHECK",
        id="E_NO_CHECK",
    ),
    pytest.param(
        "E_UNKNOWN_PORT",
        _COUNTER_SV,
        {**_COUNTER_SPEC_BASE,
         "cases": [{"name": "t",
                    "expected": [{"at": 3, "check": {"no_such": 0}}]}]},
        "E_UNKNOWN_PORT",
        id="E_UNKNOWN_PORT",
    ),
    pytest.param(
        "E_NO_CASE",
        _COUNTER_SV,
        {**_COUNTER_SPEC_BASE,
         "cases": [{"name": "real",
                    "expected": [{"at": 3, "check": {"count": 1}}]}]},
        "E_NO_CASE",
        id="E_NO_CASE",
    ),
    pytest.param(
        "E_OFFSET_RANGE",
        _COUNTER_SV,
        {**_COUNTER_SPEC_BASE,
         "cases": [{"name": "t",
                    "expected": [{"at": 3, "offset_ns": 9999,
                                  "check": {"count": 1}}]}]},
        "E_OFFSET_RANGE",
        id="E_OFFSET_RANGE",
    ),
    pytest.param(
        "E_CONSTRAINT",
        _CONSTRAINT_SV,
        _IMPOSSIBLE_SPEC,
        "E_CONSTRAINT",
        id="E_CONSTRAINT",
    ),
    pytest.param(
        "E_CONSTRAINT_EXPR",
        _CONSTRAINT_SV,
        _CEXPR_SPEC,
        "E_CONSTRAINT_EXPR",
        id="E_CONSTRAINT_EXPR",
    ),
]


@pytest.mark.parametrize("name,sv_code,spec_dict,expected_code", _PARAM_CASES)
def test_error_code_cli(name, sv_code, spec_dict, expected_code, tmp_path):
    """CLI: 各エラーコードが E_INTERNAL に化けていないことを確認"""
    sv_p = str(tmp_path / "m.sv")
    Path(sv_p).write_text(sv_code, encoding="utf-8")
    spec_p = str(tmp_path / "spec.yaml")

    extra = {}
    if expected_code == "E_NO_CASE":
        extra["extra_args"] = ["--case", "nonexistent_case"]

    with open(spec_p, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True)

    out = _cli_test(sv_p, spec_p, tmp_path,
                    extra_args=extra.get("extra_args", ()))
    codes = _all_codes(out)

    assert "E_INTERNAL" not in codes, \
        f"{name}: E_INTERNAL must NOT appear, got {codes}"
    assert expected_code in codes, \
        f"{name}: expected {expected_code!r}, got {codes}"


@pytest.mark.parametrize("name,sv_code,spec_dict,expected_code", _PARAM_CASES)
def test_error_code_api(name, sv_code, spec_dict, expected_code, tmp_path):
    """API: 各エラーコードが E_INTERNAL に化けていないことを確認"""
    sv_p = str(tmp_path / "m.sv")
    Path(sv_p).write_text(sv_code, encoding="utf-8")
    spec_p = str(tmp_path / "spec.yaml")
    with open(spec_p, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True)

    kw = {}
    if expected_code == "E_NO_CASE":
        kw["filter_cases"] = ["nonexistent_case"]

    result = _api_test(sv_p, spec_p, **kw)
    codes = _all_codes(result)

    assert "E_INTERNAL" not in codes, \
        f"{name}: E_INTERNAL must NOT appear (API), got {codes}"
    assert expected_code in codes, \
        f"{name}: expected {expected_code!r} from API, got {codes}"
