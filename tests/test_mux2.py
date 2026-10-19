"""tests/test_mux2.py — mux2.sv のシミュレーション検証"""

import pytest
from deltasim.parser.parser import parse_file
from deltasim.elab.elaborator import elaborate
from deltasim.sim.engine import SimEngine


def _make_engine(sv_path, param_overrides=None):
    mod = parse_file(sv_path)
    design = elaborate(mod, param_overrides=param_overrides or {})
    return SimEngine(design, reset_cycles=0)


class TestMux2:
    """純組み合わせ 2 入力 MUX のテスト"""

    def test_sel0_outputs_b(self, mux2_sv):
        """sel=0 のとき y=b"""
        eng = _make_engine(mux2_sv)
        eng.signals["a"] = 0xAA
        eng.signals["b"] = 0x55
        eng.signals["sel"] = 0
        eng.evaluate()
        assert eng.get_output("y") == 0x55

    def test_sel1_outputs_a(self, mux2_sv):
        """sel=1 のとき y=a"""
        eng = _make_engine(mux2_sv)
        eng.signals["a"] = 0xAA
        eng.signals["b"] = 0x55
        eng.signals["sel"] = 1
        eng.evaluate()
        assert eng.get_output("y") == 0xAA

    def test_dynamic_switch(self, mux2_sv):
        """sel を切り替えると出力が追従する"""
        eng = _make_engine(mux2_sv)
        eng.set_input("a", 10)
        eng.set_input("b", 20)

        eng.set_input("sel", 0)
        assert eng.get_output("y") == 20

        eng.set_input("sel", 1)
        assert eng.get_output("y") == 10

    def test_width4_parameter(self, mux2_sv):
        """WIDTH=4 パラメータ: 値が 4 ビットにマスクされる"""
        eng = _make_engine(mux2_sv, param_overrides={"WIDTH": 4})
        eng.set_input("a", 0xF)
        eng.set_input("b", 0x0)
        eng.set_input("sel", 1)
        assert eng.get_output("y") == 0xF
        # オーバーフロー値は WIDTH ビットにマスク
        eng.set_input("a", 0xFF)
        assert eng.get_output("y") == 0xF  # 0xFF & 0xF = 0xF

    def test_run_one_cycle(self, mux2_sv):
        """run(cycles=1) でも組み合わせ結果が取れる（クロックなし）"""
        eng = _make_engine(mux2_sv)
        eng.signals["a"] = 42
        eng.signals["b"] = 7
        eng.signals["sel"] = 0
        result = eng.run(cycles=1, reset_cycles=0)
        # 組み合わせ回路: クロックがないのでどのサイクルも同じ値
        assert result.get_value("y", 0) == 7
