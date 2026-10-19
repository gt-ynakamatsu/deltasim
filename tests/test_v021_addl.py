"""tests/test_v021_addl.py — v0.2.1 追加確認テスト

PR #13 レビュー指摘対応:
1. ds.run() / deltasim run コマンド の max_delta/max_loop + エラーハンドリング
2. E_INTERNAL: monkeypatch で RuntimeError / RecursionError
3. VCD: エラー時に正しく閉じられること
4. E_DELTA_LIMIT ヒント: always_comb の変化も対象
"""

from __future__ import annotations
import json, os, tempfile, yaml
from pathlib import Path
import pytest
import deltasim.api as ds
from click.testing import CliRunner
from deltasim.cli import main as cli_main

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")

# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _write(path: str, content: str):
    Path(path).write_text(content, encoding="utf-8")


_SV_COMB_LOOP = """\
module comb_loop(input logic x, output logic y);
  assign y = ~y;
endmodule
"""

_SV_COMB_LOOP_ALWAYS = """\
module comb_loop_always(input logic a, output logic y);
  always_comb begin
    y = ~y;
  end
endmodule
"""

_SV_INF_LOOP = """\
module inf_loop(input logic clk, output logic [7:0] count);
  integer i;
  always @(posedge clk) begin
    for (i = 0; 1; i = i + 1) count <= count + 1;
  end
endmodule
"""


def _base_spec(top: str, cycles: int = 2) -> dict:
    return {
        "version": 1, "top": top,
        "clock": {"signal": "clk", "period_ns": 10},
        "reset": {"signal": "rst", "active_low": True, "duration_cycles": 1},
        "sim": {"cycles": cycles},
        "cases": [{"name": "test", "expected": []}],
    }


# ---------------------------------------------------------------------------
# 1. ds.run() / CLI run コマンドの max_delta / max_loop
# ---------------------------------------------------------------------------

class TestRunCommand:
    """deltasim run コマンドの max_delta/max_loop 対応"""

    def test_run_api_delta_limit(self, tmp_path):
        """ds.run() が max_delta=5 で E_DELTA_LIMIT を errors[] に返すこと"""
        sv_p = str(tmp_path / "comb_loop.sv")
        _write(sv_p, _SV_COMB_LOOP)
        result = ds.run([sv_p], top="comb_loop", cycles=2, max_delta=5)
        assert result.status == "ERROR"
        codes = [e.code for e in result.errors]
        assert "E_DELTA_LIMIT" in codes

    def test_run_api_loop_limit(self, tmp_path):
        """ds.run() が max_loop=100 で E_LOOP_LIMIT を errors[] に返すこと"""
        sv_p = str(tmp_path / "inf.sv")
        _write(sv_p, _SV_INF_LOOP)
        result = ds.run([sv_p], top="inf_loop", cycles=2, max_loop=100)
        assert result.status == "ERROR"
        codes = [e.code for e in result.errors]
        assert "E_LOOP_LIMIT" in codes

    def test_run_cli_delta_limit_json(self, tmp_path):
        """CLI run --max-delta で E_DELTA_LIMIT が --json に出て非0終了すること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        _write(sv_p, _SV_COMB_LOOP)
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "run", sv_p, "--max-delta", "5", "--json", json_p,
        ])
        assert result.exit_code != 0
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E_DELTA_LIMIT" in codes

    def test_run_cli_loop_limit_json(self, tmp_path):
        """CLI run --max-loop で E_LOOP_LIMIT が --json に出て非0終了すること"""
        sv_p = str(tmp_path / "inf.sv")
        _write(sv_p, _SV_INF_LOOP)
        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "run", sv_p, "--max-loop", "100", "--json", json_p,
        ])
        assert result.exit_code != 0
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E_LOOP_LIMIT" in codes

    def test_run_no_error_normal(self):
        """正常動作では run() がエラーにならないこと"""
        result = ds.run(
            [os.path.join(EXAMPLES, "counter.sv")],
            top="counter", cycles=10,
        )
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# 2. E_INTERNAL テスト
# ---------------------------------------------------------------------------

class TestEInternal:
    """想定外例外が E_INTERNAL として JSON に出ること"""

    def test_runtime_error_becomes_e_internal(self, tmp_path, monkeypatch):
        """ds.test 内で RuntimeError が起きたとき CLI が E_INTERNAL JSON を出すこと"""
        import deltasim.api as _ds_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("forced boom for test")

        monkeypatch.setattr(_ds_mod, "test", _boom)

        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_p,
        ])
        assert result.exit_code != 0
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E_INTERNAL" in codes
        # message に例外の型名が含まれること
        msgs = [e["message"] for e in d["errors"] if e["code"] == "E_INTERNAL"]
        assert any("RuntimeError" in m for m in msgs), f"msgs={msgs}"

    def test_recursion_error_becomes_e_internal(self, tmp_path, monkeypatch):
        """RecursionError も E_INTERNAL として JSON に出ること"""
        import deltasim.api as _ds_mod

        def _recurse(*args, **kwargs):
            raise RecursionError("maximum recursion depth exceeded")

        monkeypatch.setattr(_ds_mod, "test", _recurse)

        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_p,
        ])
        assert result.exit_code != 0
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["status"] == "ERROR"
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E_INTERNAL" in codes

    def test_e_internal_json_has_required_fields(self, tmp_path, monkeypatch):
        """E_INTERNAL の JSON に schema_version / deltasim_version / warnings: [] があること"""
        import deltasim.api as _ds_mod
        import deltasim

        monkeypatch.setattr(_ds_mod, "test", lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("boom")))

        json_p = str(tmp_path / "result.json")
        runner = CliRunner()
        runner.invoke(cli_main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_p,
        ])
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert "schema_version" in d, f"schema_version missing: {list(d.keys())}"
        assert d["schema_version"] == "2"
        assert "deltasim_version" in d, f"deltasim_version missing"
        assert d["deltasim_version"] == deltasim.__version__
        assert "warnings" in d, f"warnings missing"
        assert isinstance(d["warnings"], list)


# ---------------------------------------------------------------------------
# 3. VCD: エラー時に正しく閉じられること
# ---------------------------------------------------------------------------

class TestVcdOnError:
    """E_DELTA_LIMIT / E_LOOP_LIMIT でも VCD が正しく閉じられること"""

    def test_vcd_written_on_delta_limit_test(self, tmp_path):
        """deltasim test の E_DELTA_LIMIT でも VCD ファイルが閉じられること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        _write(sv_p, _SV_COMB_LOOP)
        vcd_p = str(tmp_path / "out.vcd")
        yaml_p = str(tmp_path / "spec.yaml")
        spec = {**_base_spec("comb_loop"),
                "sim": {"cycles": 2, "vcd": vcd_p}}
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        ds.test([sv_p], spec=yaml_p, max_delta=5)
        # VCD ファイルが存在すること
        assert Path(vcd_p).exists(), "VCD ファイルが作成されていない"
        content = Path(vcd_p).read_text(encoding="utf-8", errors="replace")
        # ヘッダが含まれること
        assert "$enddefinitions" in content, "$enddefinitions が VCD にない"

    def test_vcd_written_on_loop_limit_test(self, tmp_path):
        """deltasim test の E_LOOP_LIMIT でも VCD ファイルが閉じられること"""
        sv_p = str(tmp_path / "inf.sv")
        _write(sv_p, _SV_INF_LOOP)
        vcd_p = str(tmp_path / "out.vcd")
        yaml_p = str(tmp_path / "spec.yaml")
        spec = {**_base_spec("inf_loop"),
                "sim": {"cycles": 2, "vcd": vcd_p}}
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        ds.test([sv_p], spec=yaml_p, max_loop=100)
        assert Path(vcd_p).exists(), "VCD ファイルが作成されていない"
        content = Path(vcd_p).read_text(encoding="utf-8", errors="replace")
        assert "$enddefinitions" in content

    def test_vcd_deletable_after_error(self, tmp_path):
        """エラー後も VCD ファイルが閉じられていること（Windows で削除可能）"""
        sv_p = str(tmp_path / "comb_loop.sv")
        _write(sv_p, _SV_COMB_LOOP)
        vcd_p = str(tmp_path / "out.vcd")
        yaml_p = str(tmp_path / "spec.yaml")
        spec = {**_base_spec("comb_loop"),
                "sim": {"cycles": 1, "vcd": vcd_p}}
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        ds.test([sv_p], spec=yaml_p, max_delta=5)
        # 削除できること（ファイルハンドルが開いていないことの確認）
        Path(vcd_p).unlink()
        assert not Path(vcd_p).exists()

    def test_vcd_on_run_delta_limit(self, tmp_path):
        """ds.run() の E_DELTA_LIMIT でも VCD が正しく書かれること"""
        sv_p = str(tmp_path / "comb_loop.sv")
        _write(sv_p, _SV_COMB_LOOP)
        vcd_p = str(tmp_path / "out.vcd")
        ds.run([sv_p], top="comb_loop", cycles=2, vcd=vcd_p, max_delta=5)
        assert Path(vcd_p).exists()
        content = Path(vcd_p).read_text(encoding="utf-8", errors="replace")
        assert "$enddefinitions" in content


# ---------------------------------------------------------------------------
# 4. E_DELTA_LIMIT ヒント: always_comb の変化も対象
# ---------------------------------------------------------------------------

class TestDeltaHintAlwaysComb:
    """always_comb のループでも信号名ヒントが出ること"""

    def test_hint_in_always_comb_loop(self, tmp_path):
        """always_comb の組み合わせループでも signal_hint に信号名が含まれること"""
        sv_p = str(tmp_path / "comb_always.sv")
        _write(sv_p, _SV_COMB_LOOP_ALWAYS)
        yaml_p = str(tmp_path / "spec.yaml")
        spec = _base_spec("comb_loop_always")
        with open(yaml_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec=yaml_p, max_delta=5)
        assert result.status == "ERROR"
        e = next((e for e in result.errors if e.code == "E_DELTA_LIMIT"), None)
        assert e is not None, "E_DELTA_LIMIT が出なかった"
        # message に信号名 "y" が含まれること（ヒント）
        assert "y" in e.message, f"信号名ヒントが message にない: {e.message}"
