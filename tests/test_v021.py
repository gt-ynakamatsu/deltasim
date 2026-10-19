"""tests/test_v021.py — v0.2.1 追加機能テスト

1. ANSI 複数ポート名パーサ（input logic clk, rst, d など）
2. E_DELTA_LIMIT: delta サイクル上限（--max-delta / max_delta=N）
3. E_LOOP_LIMIT: ループ回数上限（--max-loop / max_loop=N）
4. --json 時の常時 JSON 出力（E_INTERNAL含む）
"""

from __future__ import annotations
import json, os, subprocess, sys, tempfile, yaml
from pathlib import Path
import pytest
import deltasim.api as ds
from click.testing import CliRunner
from deltasim.cli import main as cli_main

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
PY = sys.executable


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write_sv(content: str, suffix=".sv"):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False, encoding="utf-8")
    f.write(content); f.close()
    return f.name


def _write_yaml(data: dict):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8")
    yaml.dump(data, f, allow_unicode=True); f.close()
    return f.name


def _base_spec(top: str, cycles: int = 2) -> dict:
    return {
        "version": 1, "top": top,
        "clock": {"signal": "clk", "period_ns": 10},
        "reset": {"signal": "rst", "active_low": True, "duration_cycles": 1},
        "sim": {"cycles": cycles},
        "cases": [{"name": "test", "expected": []}],
    }


# ---------------------------------------------------------------------------
# 1. ANSI 複数ポート名パーサ
# ---------------------------------------------------------------------------

class TestMultiPortDecl:
    """input logic clk, rst, d スタイルの ANSI ポート宣言を受け付けること"""

    def test_basic_multi_input(self):
        """1つの input logic の後に複数名を並べて宣言できること"""
        sv = "module t(input logic clk, rst, d, output logic q);\n" \
             "  always_ff @(posedge clk) q <= d;\nendmodule"
        sv_p = _write_sv(sv)
        try:
            di = ds.load([sv_p])
        finally:
            os.unlink(sv_p)
        assert di.status == "PASS", [e.message for e in di.errors]
        names = {p.name for p in di.ports}
        assert {"clk", "rst", "d", "q"} == names
        dirs = {p.name: p.direction for p in di.ports}
        assert dirs["clk"] == "input"
        assert dirs["rst"] == "input"
        assert dirs["d"] == "input"
        assert dirs["q"] == "output"

    def test_multi_output_with_bus(self):
        """output logic [3:0] a, b のように幅付きでも動作すること"""
        sv = "module t(input logic [3:0] a, b, output logic [3:0] y);\n" \
             "  assign y = a + b;\nendmodule"
        sv_p = _write_sv(sv)
        try:
            di = ds.load([sv_p])
        finally:
            os.unlink(sv_p)
        assert di.status == "PASS", [e.message for e in di.errors]
        widths = {p.name: p.width for p in di.ports}
        assert widths["a"] == 4
        assert widths["b"] == 4
        assert widths["y"] == 4

    def test_mixed_directions(self):
        """input a, output b の方向の混在が正しくパースされること"""
        sv = "module t(input logic a, output logic b, inout logic c);\n" \
             "  assign b = a;\nendmodule"
        sv_p = _write_sv(sv)
        try:
            di = ds.load([sv_p])
        finally:
            os.unlink(sv_p)
        assert di.status == "PASS", [e.message for e in di.errors]
        dirs = {p.name: p.direction for p in di.ports}
        assert dirs["a"] == "input"
        assert dirs["b"] == "output"
        assert dirs["c"] == "inout"

    def test_type_omitted(self):
        """input clk, rst（型省略）でも動作すること"""
        sv = "module t(input clk, rst);\nendmodule"
        sv_p = _write_sv(sv)
        try:
            di = ds.load([sv_p])
        finally:
            os.unlink(sv_p)
        assert di.status == "PASS", [e.message for e in di.errors]
        names = {p.name for p in di.ports}
        assert {"clk", "rst"} == names

    def test_multiline(self):
        """複数行にまたがる複数ポート名宣言も動作すること"""
        sv = "module t(\n  input logic clk,\n  rst,\n  d,\n  output logic q\n);\n" \
             "  always_ff @(posedge clk) q <= d;\nendmodule"
        sv_p = _write_sv(sv)
        try:
            di = ds.load([sv_p])
        finally:
            os.unlink(sv_p)
        assert di.status == "PASS", [e.message for e in di.errors]
        assert {p.name for p in di.ports} == {"clk", "rst", "d", "q"}

    def test_multi_port_simulation(self, tmp_path):
        """複数ポート名宣言モジュールのシミュレーションが正常動作すること"""
        sv = "module dff2(input logic clk, rst, d, output logic q);\n" \
             "  always_ff @(posedge clk) if (!rst) q <= 0; else q <= d;\nendmodule"
        spec = {
            "version": 1, "top": "dff2",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "basic",
                       "stimulus": [{"at": 3, "set": {"d": 1}}],
                       "expected": [{"at": 4, "check": {"q": 1}}]}],
        }
        sv_p = str(tmp_path / "dff2.sv")
        Path(sv_p).write_text(sv, encoding="utf-8")
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec=yaml_p)
        assert result.status == "PASS", [e.message for e in result.errors]


# ---------------------------------------------------------------------------
# 2. E_DELTA_LIMIT
# ---------------------------------------------------------------------------

# assign y = ~y; → 組み合わせループ
_SV_COMB_LOOP = """\
module comb_loop(input logic x, output logic y);
  assign y = ~y;
endmodule
"""

class TestDeltaLimit:
    """デルタサイクル上限 E_DELTA_LIMIT"""

    def _spec(self):
        return _base_spec("comb_loop")

    def test_delta_limit_via_api(self, tmp_path):
        """max_delta=5 で E_DELTA_LIMIT が返ること（例外にならない）"""
        sv_p = str(tmp_path / "comb_loop.sv")
        Path(sv_p).write_text(_SV_COMB_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec()); 
        try:
            result = ds.test([sv_p], spec=yaml_p, max_delta=5)
        finally:
            os.unlink(yaml_p)
        assert result.status == "ERROR"
        codes = [e.code for e in result.errors]
        assert "E_DELTA_LIMIT" in codes

    def test_delta_limit_message_contains_time(self, tmp_path):
        """E_DELTA_LIMIT の message に時刻(ns)が含まれること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        Path(sv_p).write_text(_SV_COMB_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        try:
            result = ds.test([sv_p], spec=yaml_p, max_delta=5)
        finally:
            os.unlink(yaml_p)
        e = next(e for e in result.errors if e.code == "E_DELTA_LIMIT")
        assert "ns" in e.message

    def test_delta_limit_case_also_error(self, tmp_path):
        """cases[].status も ERROR になること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        Path(sv_p).write_text(_SV_COMB_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        try:
            result = ds.test([sv_p], spec=yaml_p, max_delta=5)
        finally:
            os.unlink(yaml_p)
        assert any(cr.status == "ERROR" for cr in result.cases)

    def test_delta_limit_json_via_cli(self, tmp_path):
        """CLI --max-delta で E_DELTA_LIMIT が JSON に出て終了コード非0になること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        Path(sv_p).write_text(_SV_COMB_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test", sv_p, "--spec", yaml_p,
            "--json", json_p, "--max-delta", "5",
        ])
        os.unlink(yaml_p)
        assert result.exit_code != 0, f"expected non-0 but got 0"
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E_DELTA_LIMIT" in codes

    def test_normal_circuit_no_delta_limit(self, tmp_path):
        """収束する回路ではデフォルトの max_delta でエラーにならないこと"""
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# 3. E_LOOP_LIMIT
# ---------------------------------------------------------------------------

_SV_INF_LOOP = """\
module inf_loop(input logic clk, output logic [7:0] count);
  integer i;
  always @(posedge clk) begin
    for (i = 0; 1; i = i + 1) count <= count + 1;
  end
endmodule
"""

class TestLoopLimit:
    """ループ回数上限 E_LOOP_LIMIT"""

    def _spec(self):
        return _base_spec("inf_loop")

    def test_loop_limit_via_api(self, tmp_path):
        """max_loop=100 で E_LOOP_LIMIT が返ること（例外にならない）"""
        sv_p = str(tmp_path / "inf.sv")
        Path(sv_p).write_text(_SV_INF_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        try:
            result = ds.test([sv_p], spec=yaml_p, max_loop=100)
        finally:
            os.unlink(yaml_p)
        assert result.status == "ERROR"
        codes = [e.code for e in result.errors]
        assert "E_LOOP_LIMIT" in codes

    def test_loop_limit_message_contains_time(self, tmp_path):
        """E_LOOP_LIMIT の message に時刻(ns)が含まれること"""
        sv_p = str(tmp_path / "inf.sv")
        Path(sv_p).write_text(_SV_INF_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        try:
            result = ds.test([sv_p], spec=yaml_p, max_loop=100)
        finally:
            os.unlink(yaml_p)
        e = next(e for e in result.errors if e.code == "E_LOOP_LIMIT")
        assert "ns" in e.message

    def test_loop_limit_json_via_cli(self, tmp_path):
        """CLI --max-loop で E_LOOP_LIMIT が JSON に出て終了コード非0になること"""
        sv_p = str(tmp_path / "inf.sv")
        Path(sv_p).write_text(_SV_INF_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test", sv_p, "--spec", yaml_p,
            "--json", json_p, "--max-loop", "100",
        ])
        os.unlink(yaml_p)
        assert result.exit_code != 0
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E_LOOP_LIMIT" in codes

    def test_loop_limit_json_is_valid(self, tmp_path):
        """JSON 出力が有効な JSON で status/errors[] を含むこと"""
        sv_p = str(tmp_path / "inf.sv")
        Path(sv_p).write_text(_SV_INF_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(self._spec())
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        runner.invoke(cli_main, [
            "test", sv_p, "--spec", yaml_p,
            "--json", json_p, "--max-loop", "100",
        ])
        os.unlink(yaml_p)
        text = Path(json_p).read_text(encoding="utf-8")
        d = json.loads(text)   # SyntaxError なら失敗
        assert "status" in d
        assert "errors" in d


# ---------------------------------------------------------------------------
# 4. --json 常時出力 + E_INTERNAL
# ---------------------------------------------------------------------------

class TestJsonAlways:
    """--json 付きのときはどんな例外でも JSON を出力すること"""

    def test_valid_run_writes_json(self, tmp_path):
        """正常実行でも --json ファイルが書かれること"""
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_p,
        ])
        assert result.exit_code == 0, result.output
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "PASS"

    def test_error_run_writes_json(self, tmp_path):
        """エラー終了でも --json ファイルが書かれること（E_DELTA_LIMIT）"""
        sv_p = str(tmp_path / "comb_loop.sv")
        Path(sv_p).write_text(_SV_COMB_LOOP, encoding="utf-8")
        yaml_p = _write_yaml(_base_spec("comb_loop"))
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test", sv_p, "--spec", yaml_p,
            "--json", json_p, "--max-delta", "5",
        ])
        os.unlink(yaml_p)
        assert result.exit_code != 0
        text = Path(json_p).read_text(encoding="utf-8")
        d = json.loads(text)
        assert d["status"] == "ERROR"


# ---------------------------------------------------------------------------
# 5. 他ケース続行（続行する実装であることの確認）
# ---------------------------------------------------------------------------

class TestContinueOnError:
    """1ケースでエラーが起きても他ケースを続行すること"""

    def test_second_case_runs_after_first_errors(self, tmp_path):
        """cases[0] が E_DELTA_LIMIT でも cases[1] が実行されること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        Path(sv_p).write_text(_SV_COMB_LOOP, encoding="utf-8")
        spec = {
            "version": 1, "top": "comb_loop",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": True, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [
                {"name": "bad",  "expected": []},
                {"name": "also", "expected": []},
            ],
        }
        yaml_p = _write_yaml(spec)
        try:
            result = ds.test([sv_p], spec=yaml_p, max_delta=5)
        finally:
            os.unlink(yaml_p)
        names = [cr.name for cr in result.cases]
        assert "bad" in names
        assert "also" in names
        # "also" ケースも実行された（SKIP ではない — ERROR か PASS）
        also = next(cr for cr in result.cases if cr.name == "also")
        assert also.status != "SKIP"
