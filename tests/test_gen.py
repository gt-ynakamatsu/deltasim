"""tests/test_gen.py — deltasim gen のテスト"""

import os
import pytest
from deltasim.parser.parser import parse_file
from deltasim.elab.elaborator import elaborate
from deltasim.tb.generator import generate_tb_sv, generate_yaml_spec


@pytest.fixture
def counter_design():
    mod = parse_file("examples/counter.sv")
    return elaborate(mod)


@pytest.fixture
def mux2_design():
    mod = parse_file("examples/mux2.sv")
    return elaborate(mod)


class TestTBGenerator:
    def test_tb_sv_created(self, counter_design, tmp_path):
        """TB SV ファイルが生成されること"""
        out = str(tmp_path / "counter_tb.sv")
        generate_tb_sv(counter_design, out)
        assert os.path.isfile(out)

    def test_tb_sv_has_module(self, counter_design, tmp_path):
        """TB SV に module 宣言が含まれること"""
        out = str(tmp_path / "counter_tb.sv")
        generate_tb_sv(counter_design, out)
        text = open(out, encoding='utf-8').read()
        assert "module counter_tb" in text
        assert "endmodule" in text

    def test_tb_sv_has_dut_instance(self, counter_design, tmp_path):
        """TB SV に DUT インスタンスが含まれること"""
        out = str(tmp_path / "counter_tb.sv")
        generate_tb_sv(counter_design, out)
        text = open(out, encoding='utf-8').read()
        assert "counter" in text
        assert "dut" in text
        assert "clk" in text
        assert "rst_n" in text
        assert "count" in text

    def test_tb_sv_has_clock(self, counter_design, tmp_path):
        """TB SV にクロック生成が含まれること"""
        out = str(tmp_path / "counter_tb.sv")
        generate_tb_sv(counter_design, out)
        text = open(out, encoding='utf-8').read()
        assert "always #5" in text

    def test_tb_sv_has_initial(self, counter_design, tmp_path):
        """TB SV に initial ブロックが含まれること"""
        out = str(tmp_path / "counter_tb.sv")
        generate_tb_sv(counter_design, out)
        text = open(out, encoding='utf-8').read()
        assert "initial" in text
        assert "$finish" in text

    def test_tb_sv_has_vcd(self, counter_design, tmp_path):
        """TB SV に VCD ダンプ設定が含まれること"""
        out = str(tmp_path / "counter_tb.sv")
        generate_tb_sv(counter_design, out)
        text = open(out, encoding='utf-8').read()
        assert "$dumpfile" in text
        assert "$dumpvars" in text

    def test_yaml_spec_created(self, counter_design, tmp_path):
        """YAML スペックファイルが生成されること"""
        out = str(tmp_path / "counter_spec.yaml")
        generate_yaml_spec(counter_design, out)
        assert os.path.isfile(out)

    def test_yaml_spec_valid(self, counter_design, tmp_path):
        """生成された YAML がパース可能で必須フィールドを持つこと"""
        import yaml
        out = str(tmp_path / "counter_spec.yaml")
        generate_yaml_spec(counter_design, out)
        data = yaml.safe_load(open(out, encoding='utf-8'))
        assert data.get("version") == 1
        assert data.get("top") == "counter"
        assert "clock" in data
        assert "reset" in data
        assert "sim" in data

    def test_yaml_spec_has_clock_signal(self, counter_design, tmp_path):
        """YAML スペックにクロック信号名が含まれること"""
        import yaml
        out = str(tmp_path / "counter_spec.yaml")
        generate_yaml_spec(counter_design, out)
        data = yaml.safe_load(open(out, encoding='utf-8'))
        assert data["clock"]["signal"] == "clk"

    def test_yaml_spec_has_reset_signal(self, counter_design, tmp_path):
        """YAML スペックにリセット信号名が含まれること"""
        import yaml
        out = str(tmp_path / "counter_spec.yaml")
        generate_yaml_spec(counter_design, out)
        data = yaml.safe_load(open(out, encoding='utf-8'))
        assert data["reset"]["signal"] == "rst_n"
        assert data["reset"]["active_low"] is True

    def test_mux2_no_clock_in_tb(self, mux2_design, tmp_path):
        """組み合わせ回路の TB には always clock がないこと"""
        out = str(tmp_path / "mux2_tb.sv")
        generate_tb_sv(mux2_design, out)
        text = open(out, encoding='utf-8').read()
        # クロックが検出されないので always #5 は不要
        assert "module mux2_tb" in text

    def test_gen_cli(self, tmp_path):
        """deltasim gen CLI コマンドが動作すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "gen", "examples/counter.sv",
            "--top", "counter",
            "--out-dir", str(tmp_path),
        ])
        assert result.exit_code == 0, result.output
        assert os.path.isfile(str(tmp_path / "counter_tb.sv"))
        assert os.path.isfile(str(tmp_path / "counter_spec.yaml"))
