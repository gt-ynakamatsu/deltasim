"""tests/test_shift_reg.py — shift_reg.sv のシミュレーション検証"""

import pytest
from deltasim.parser.parser import parse_file
from deltasim.elab.elaborator import elaborate
from deltasim.sim.engine import SimEngine


def _make_engine(sv_path, reset_cycles=2):
    mod = parse_file(sv_path)
    design = elaborate(mod)
    return SimEngine(design, reset_cycles=reset_cycles)


class TestShiftReg:
    def test_reset_clears_output(self, shift_reg_sv):
        """リセット中は q=0"""
        eng = _make_engine(shift_reg_sv, reset_cycles=3)
        result = eng.run(cycles=3)
        for c in range(0, 4):
            assert result.get_value("q", c) == 0, f"cycle {c}: expected 0"

    def test_shift_in_ones(self, shift_reg_sv):
        """d=1 を 4 サイクル入力すると q=0b1111=15"""
        mod = parse_file(shift_reg_sv)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=1)

        # d=1 を固定
        eng.signals["d"] = 1
        result = eng.run(cycles=5)

        # reset 後: cycle2→0001, cycle3→0011, cycle4→0111, cycle5→1111
        assert result.get_value("q", 2) == 0b0001
        assert result.get_value("q", 3) == 0b0011
        assert result.get_value("q", 4) == 0b0111
        assert result.get_value("q", 5) == 0b1111

    def test_shift_pattern(self, shift_reg_sv):
        """パターン 1010 をシフトインする"""
        mod = parse_file(shift_reg_sv)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)

        # signals を直接制御してサイクルごとに検証
        result = eng.run(cycles=0)  # 初期化のみ
        assert eng.signals.get("q", 0) == 0

        # 手動でクロックを刻む代わりに、run(1) を複数回呼ぶ
        eng.signals["d"] = 1
        r = eng.run(cycles=1, reset_cycles=0)
        assert r.get_value("q", 1) == 0b0001

        eng.signals["d"] = 0
        r = eng.run(cycles=1, reset_cycles=0)
        assert r.get_value("q", 1) == 0b0010

        eng.signals["d"] = 1
        r = eng.run(cycles=1, reset_cycles=0)
        assert r.get_value("q", 1) == 0b0101

        eng.signals["d"] = 0
        r = eng.run(cycles=1, reset_cycles=0)
        assert r.get_value("q", 1) == 0b1010
