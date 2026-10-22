"""tests/test_error_codes.py — エラーコード退行防止テスト

docs/error_codes.md の全専用エラーコードを実際に発生させ、
`deltasim test --json` (CLI) および Python API でそのコードが返ることを確認する。
E_INTERNAL に化けていないことを特にチェック。

各テストは期待コードを1つに絞って assert する。
`any(...)` や `or status == "ERROR"` は禁止。

E_INCLUDE_LOOP (§18.9) もここで統合テスト（CLI + API）。
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


def _cli_test(sv_p: str, spec_p: str, tmp: Path, extra_args=(), timeout: int = 30) -> dict:
    """CLI で `deltasim test --json` を実行して JSON 結果 dict を返す。"""
    json_p = str(tmp / "out.json")
    subprocess.run(
        [sys.executable, "-m", "deltasim", "test",
         str(sv_p), "--spec", str(spec_p), "--json", json_p, *extra_args],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
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
# 1b. E_LOOP_LIMIT 追加テスト（§18.9 退行修正: always_comb / always_ff / wloop）
# ===========================================================================

# wloop: 再現コードをそのまま使う（ユーザー報告の退行ケース）
_WLOOP_SV = """\
module wloop(input logic clk, input logic a, output logic [31:0] y);
  logic [31:0] count;
  always_comb begin
    count = 0;
    while (count < 32'hFFFFFFFF) count = count + 1;
    y = count & {32{a}};
  end
endmodule
"""
# max_loop は YAML キーでなく ds.test() / --max-loop で渡す
_WLOOP_SPEC = {
    "version": 1, "top": "wloop",
    "clock": {"signal": "clk", "period_ns": 10},
    "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
    "sim": {"cycles": 2},
    "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}],
}

_ALWAYS_COMB_WHILE_SV = """\
module m(input logic clk, input logic a, output logic [31:0] y);
  logic [31:0] cnt;
  always_comb begin
    cnt = 0;
    while (cnt < 32'hFFFFFFF) cnt = cnt + 1;
    y = cnt;
  end
endmodule
"""

_ALWAYS_FF_WHILE_SV = """\
module m(input logic clk, output logic [31:0] y);
  integer i;
  always_ff @(posedge clk) begin
    i = 0;
    while (i < 2000000) begin i = i + 1; end
    y <= i[31:0];
  end
endmodule
"""

# always_comb self-reference → E_DELTA_LIMIT
_ALWAYS_COMB_SELF_SV = """\
module m(input logic clk, output logic y);
  always_comb y = ~y;
endmodule
"""

_LOOP_SPEC_TMPL = {
    "version": 1,
    "clock": {"signal": "clk", "period_ns": 10},
    "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
    "sim": {"cycles": 2},
    "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}],
}
# CLI 引数: --max-loop 10 でループ上限を小さくし Windows CI でもタイムアウトしない
_LOOP_MAX_LOOP_ARGS = ("--max-loop", "10")


class TestLoopLimitAlwaysComb:
    """always_comb 内の while ループが E_LOOP_LIMIT を返すこと（新退行）"""

    def test_wloop_cli(self, tmp_path):
        """CLI: wloop（ユーザー報告の再現コード）→ E_LOOP_LIMIT  --max-loop 10 で高速化"""
        sv_p = _write(tmp_path, "wloop.sv", _WLOOP_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(_WLOOP_SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path, extra_args=_LOOP_MAX_LOOP_ARGS)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected (wloop CLI), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_wloop_api(self, tmp_path):
        """API: wloop → E_LOOP_LIMIT  max_loop=10 で高速化"""
        sv_p = _write(tmp_path, "wloop.sv", _WLOOP_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(_WLOOP_SPEC, f)
        result = _api_test(sv_p, spec_p, max_loop=10)
        codes = _all_codes(result)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected (wloop API), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_always_comb_while_cli(self, tmp_path):
        """CLI: always_comb 内 while → E_LOOP_LIMIT"""
        sv_p = _write(tmp_path, "m.sv", _ALWAYS_COMB_WHILE_SV)
        spec = {**_LOOP_SPEC_TMPL, "top": "m"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv_p, spec_p, tmp_path, extra_args=_LOOP_MAX_LOOP_ARGS)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes
        assert "E_INTERNAL" not in codes

    def test_always_comb_while_api(self, tmp_path):
        """API: always_comb 内 while → E_LOOP_LIMIT"""
        sv_p = _write(tmp_path, "m.sv", _ALWAYS_COMB_WHILE_SV)
        spec = {**_LOOP_SPEC_TMPL, "top": "m"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = _api_test(sv_p, spec_p, max_loop=10)
        codes = _all_codes(result)
        assert "E_LOOP_LIMIT" in codes
        assert "E_INTERNAL" not in codes

    def test_always_ff_while_cli(self, tmp_path):
        """CLI: always_ff 内 while → E_LOOP_LIMIT"""
        sv_p = _write(tmp_path, "m.sv", _ALWAYS_FF_WHILE_SV)
        spec = {**_LOOP_SPEC_TMPL, "top": "m"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv_p, spec_p, tmp_path, extra_args=_LOOP_MAX_LOOP_ARGS)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes
        assert "E_INTERNAL" not in codes

    def test_always_ff_while_api(self, tmp_path):
        """API: always_ff 内 while → E_LOOP_LIMIT"""
        sv_p = _write(tmp_path, "m.sv", _ALWAYS_FF_WHILE_SV)
        spec = {**_LOOP_SPEC_TMPL, "top": "m"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = _api_test(sv_p, spec_p, max_loop=10)
        codes = _all_codes(result)
        assert "E_LOOP_LIMIT" in codes
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 1c. always_comb E_DELTA_LIMIT（自己依存ループ）とデフォルト max_loop のテスト
# ===========================================================================

class TestAlwaysCombDeltaLimit:
    """always_comb の自己参照ループが E_DELTA_LIMIT を返すこと（CLI + API）。
    初期ブロックは delta-cycle メカニズムに参加しないため initial → E_DELTA_LIMIT は不可能:
    DeltaLimitError は _eval_comb_loop() 内の assign/always_comb 収束ループでのみ発生する。
    """

    _DELTA_SPEC = {
        "version": 1, "top": "m",
        "clock": {"signal": "clk", "period_ns": 10},
        "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
        "sim": {"cycles": 1},
        "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}],
    }

    def test_always_comb_self_ref_cli(self, tmp_path):
        """CLI: always_comb y = ~y（自己参照） → E_DELTA_LIMIT"""
        sv_p = _write(tmp_path, "m.sv", _ALWAYS_COMB_SELF_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(self._DELTA_SPEC, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_DELTA_LIMIT" in codes, \
            f"E_DELTA_LIMIT expected (always_comb self-ref CLI), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_always_comb_self_ref_api(self, tmp_path):
        """API: always_comb y = ~y（自己参照） → E_DELTA_LIMIT"""
        sv_p = _write(tmp_path, "m.sv", _ALWAYS_COMB_SELF_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(self._DELTA_SPEC, f)
        result = _api_test(sv_p, spec_p)
        codes = _all_codes(result)
        assert "E_DELTA_LIMIT" in codes
        assert "E_INTERNAL" not in codes


class TestWloopDefaultMaxLoop:
    """wloop をデフォルト max_loop（1M）で実行してもタイムアウトせず E_LOOP_LIMIT を返すこと。

    SimEngine.__init__ で LoopLimitError を pass した後、
    main try の _eval_comb_loop() で必ず再検出されることを担保する。

    Windows では 1M×2 反復が遅い（~30s）ためスキップ。
    学習アプリは Ubuntu CI と同等の環境で動作するため Ubuntu 版で十分。
    """

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="wloop with default max_loop (2M iterations) exceeds 30s on Windows Python"
    )
    def test_wloop_default_max_loop_cli(self, tmp_path):
        """CLI (Ubuntu): wloop をデフォルト max_loop で → E_LOOP_LIMIT（タイムアウトなし）"""
        sv_p = _write(tmp_path, "wloop.sv", _WLOOP_SV)
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(_WLOOP_SPEC, f)
        # --max-loop なし = デフォルト 1,000,000
        out = _cli_test(sv_p, spec_p, tmp_path, timeout=90)
        codes = _all_codes(out)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected (default max_loop CLI), got: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 1d. LoopLimitError が pass 後に必ず再検出されることの担保テスト
# ===========================================================================

class TestLoopLimitRedetection:
    """SimEngine.__init__ と Simulator.__init__ が LoopLimitError を pass した後、
    run()/step() 系で必ず検出されることを担保するテスト。
    cycles=0 でも検出できること（メインループ前の _eval_comb_loop で検出）。
    """

    def test_ds_run_wloop_api(self, tmp_path):
        """ds.run() で wloop → E_LOOP_LIMIT（run() 内の try で検出）"""
        sv_p = _write(tmp_path, "wloop.sv", _WLOOP_SV)
        result = ds.run([sv_p], cycles=2, max_loop=10)
        codes = [e.code for e in result.errors]
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected from ds.run(), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_simulator_step_wloop(self, tmp_path):
        """Simulator.step() で wloop → LoopLimitError が伝播（GUI 入口の確認）。
        Simulator は __init__ で LoopLimitError を pass するが、step() 内で再検出される。
        """
        from deltasim.sim.engine import LoopLimitError as _LLE
        sv_p = _write(tmp_path, "wloop.sv", _WLOOP_SV)
        sim = ds.Simulator([sv_p])
        sim._engine.max_loop = 10  # 高速化
        # step() → step_to_ns() → _run_cycle() → _eval_comb_loop() → LoopLimitError
        with pytest.raises(_LLE) as exc_info:
            sim.step(2)
        assert "E_LOOP_LIMIT" in str(exc_info.value), \
            f"LoopLimitError expected: {exc_info.value}"

    def test_cycles_zero_still_detects_wloop(self, tmp_path):
        """cycles=0 でも wloop は検出される（メインループ前の初期 _eval_comb_loop で検出）"""
        sv_p = _write(tmp_path, "wloop.sv", _WLOOP_SV)
        spec = {
            "version": 1, "top": "wloop",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 0},
            "cases": [{"name": "t", "expected": []}],  # no checks
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = _api_test(sv_p, spec_p, max_loop=10)
        codes = _all_codes(result)
        assert "E_LOOP_LIMIT" in codes, \
            f"E_LOOP_LIMIT expected even with cycles=0, got: {codes}"
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
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        # parse コマンドはエラー時に非ゼロ終了し stderr に E_AMBIGUOUS_TOP を出す
        assert result.returncode != 0, "should fail with ambiguous top"
        assert "E_AMBIGUOUS_TOP" in (result.stderr or ""), \
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
# 10. E_INCLUDE_LOOP — 循環 include（§18.9）
# ===========================================================================

class TestIncludeLoop:
    """E_INCLUDE_LOOP: 2 ファイルの循環 include が E_LOOP_LIMIT でなく E_INCLUDE_LOOP を返すこと。"""

    def _make_circular(self, tmp_path: Path):
        """a.sv → include b.sv → include a.sv (循環)"""
        a = tmp_path / "a.sv"
        b = tmp_path / "b.sv"
        a.write_text('`include "b.sv"\nmodule dummy;\nendmodule\n', encoding="utf-8")
        b.write_text('`include "a.sv"\n', encoding="utf-8")
        return str(a)

    def test_cli(self, tmp_path):
        """CLI: 循環 include が E_INCLUDE_LOOP を返すこと"""
        a_sv = self._make_circular(tmp_path)
        spec = {
            "version": 1, "top": "dummy",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [{"name": "t", "expected": [{"at": 1, "check": {"xxx": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out = _cli_test(a_sv, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E_INCLUDE_LOOP" in codes, \
            f"E_INCLUDE_LOOP expected from CLI, got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_api(self, tmp_path):
        """API: 循環 include が E_INCLUDE_LOOP を返すこと"""
        a_sv = self._make_circular(tmp_path)
        di = ds.load([a_sv])
        codes = [e.code for e in di.errors]
        assert "E_INCLUDE_LOOP" in codes, \
            f"E_INCLUDE_LOOP expected from API, got: {codes}"
        assert "E_INTERNAL" not in codes


# ===========================================================================
# 11. E001 — ファイル未発見
# ===========================================================================

class TestE001:
    def test_missing_file_cli(self, tmp_path):
        """存在しない SV ファイルが E001 を返す（CLI）"""
        result = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse",
             str(tmp_path / "ghost.sv")],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
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
# 10b. E001 (§18.9) — プリプロセッサ: 未定義マクロ / include 未発見
# ===========================================================================

class TestE001Preproc:
    """§18.9 プリプロセッサ由来の E001（CLI + API、1対1 assert）。"""

    def test_undefined_macro_cli(self, tmp_path):
        """CLI: 未定義マクロが E001 を返すこと"""
        sv_p = _write(tmp_path, "m.sv",
                      "module m;\nassign y = `UNDEFINED_MACRO;\nendmodule\n")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E001" in codes, \
            f"E001 expected (undefined macro CLI), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_undefined_macro_api(self, tmp_path):
        """API: 未定義マクロが E001 を返すこと"""
        sv_p = _write(tmp_path, "m.sv",
                      "module m;\nassign y = `UNDEFINED_MACRO;\nendmodule\n")
        di = ds.load([sv_p])
        assert di.status == "ERROR"
        codes = [e.code for e in di.errors]
        assert "E001" in codes, f"E001 expected (undefined macro API): {codes}"
        assert "E_INTERNAL" not in codes

    def test_include_not_found_cli(self, tmp_path):
        """CLI: include ファイル未発見が E001 を返すこと"""
        sv_p = _write(tmp_path, "m.sv",
                      '`include "nonexistent_file.sv"\nmodule m;\nendmodule\n')
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out = _cli_test(sv_p, spec_p, tmp_path)
        codes = _all_codes(out)
        assert "E001" in codes, \
            f"E001 expected (include not found CLI), got: {codes}"
        assert "E_INTERNAL" not in codes

    def test_include_not_found_api(self, tmp_path):
        """API: include ファイル未発見が E001 を返すこと"""
        sv_p = _write(tmp_path, "m.sv",
                      '`include "nonexistent_file.sv"\nmodule m;\nendmodule\n')
        di = ds.load([sv_p])
        assert di.status == "ERROR"
        codes = [e.code for e in di.errors]
        assert "E001" in codes, f"E001 expected (include not found API): {codes}"
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
    # always_comb 自己参照 → E_DELTA_LIMIT
    pytest.param(
        "E_DELTA_LIMIT_always_comb",
        _ALWAYS_COMB_SELF_SV,
        {"version": 1, "top": "m",
         "clock": {"signal": "clk", "period_ns": 10},
         "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
         "sim": {"cycles": 1},
         "cases": [{"name": "t", "expected": [{"at": 1, "check": {"y": 0}}]}]},
        "E_DELTA_LIMIT",
        id="E_DELTA_LIMIT_always_comb",
    ),
    # wloop (always_comb while) — 新退行ケース（max_loop=10 で高速化）
    pytest.param(
        "E_LOOP_LIMIT_always_comb_wloop",
        _WLOOP_SV,
        _WLOOP_SPEC,
        "E_LOOP_LIMIT",
        id="E_LOOP_LIMIT_always_comb_wloop",
    ),
    pytest.param(
        "E_LOOP_LIMIT_always_comb",
        _ALWAYS_COMB_WHILE_SV,
        {**_LOOP_SPEC_TMPL, "top": "m"},
        "E_LOOP_LIMIT",
        id="E_LOOP_LIMIT_always_comb",
    ),
    pytest.param(
        "E_LOOP_LIMIT_always_ff",
        _ALWAYS_FF_WHILE_SV,
        {**_LOOP_SPEC_TMPL, "top": "m"},
        "E_LOOP_LIMIT",
        id="E_LOOP_LIMIT_always_ff",
    ),
]

# max_loop=10 を必要とするパラメータ化テスト ID のセット
_NEEDS_SMALL_MAX_LOOP = frozenset({
    "E_LOOP_LIMIT_always_comb_wloop",
    "E_LOOP_LIMIT_always_comb",
    "E_LOOP_LIMIT_always_ff",
})


@pytest.mark.parametrize("name,sv_code,spec_dict,expected_code", _PARAM_CASES)
def test_error_code_cli(name, sv_code, spec_dict, expected_code, tmp_path):
    """CLI: 各エラーコードが E_INTERNAL に化けていないことを確認"""
    sv_p = str(tmp_path / "m.sv")
    Path(sv_p).write_text(sv_code, encoding="utf-8")
    spec_p = str(tmp_path / "spec.yaml")

    extra_args: list = []
    if expected_code == "E_NO_CASE":
        extra_args = ["--case", "nonexistent_case"]
    if name in _NEEDS_SMALL_MAX_LOOP:
        extra_args = list(extra_args) + list(_LOOP_MAX_LOOP_ARGS)

    with open(spec_p, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True)

    out = _cli_test(sv_p, spec_p, tmp_path, extra_args=tuple(extra_args))
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
    if name in _NEEDS_SMALL_MAX_LOOP:
        kw["max_loop"] = 10

    result = _api_test(sv_p, spec_p, **kw)
    codes = _all_codes(result)

    assert "E_INTERNAL" not in codes, \
        f"{name}: E_INTERNAL must NOT appear (API), got {codes}"
    assert expected_code in codes, \
        f"{name}: expected {expected_code!r} from API, got {codes}"
