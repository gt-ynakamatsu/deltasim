"""tests/test_counter.py — counter.sv のシミュレーション検証"""

import pytest
from deltasim.parser.parser import parse_file
from deltasim.elab.elaborator import elaborate
from deltasim.sim.engine import SimEngine


def _make_engine(sv_path, param_overrides=None, reset_cycles=10):
    mod = parse_file(sv_path)
    design = elaborate(mod, param_overrides=param_overrides or {})
    eng = SimEngine(design, reset_cycles=reset_cycles)
    return eng


class TestCounterBasic:
    """WIDTH=8 デフォルトカウンタのテスト"""

    def test_reset_holds_zero(self, counter_sv):
        """リセット中（最初の10サイクル）はcount=0のまま"""
        eng = _make_engine(counter_sv)
        result = eng.run(cycles=10)
        # cycle 0 = 初期状態, cycle 1..10 = リセット期間
        for cycle in range(0, 11):
            assert result.get_value("count", cycle) == 0, \
                f"cycle {cycle}: expected 0, got {result.get_value('count', cycle)}"

    def test_increments_after_reset(self, counter_sv):
        """リセット解除後、毎サイクルインクリメント"""
        eng = _make_engine(counter_sv)
        result = eng.run(cycles=20)
        # reset_cycles=10 → cycle 11 が最初の count=1
        for step in range(1, 11):
            expected = step
            actual = result.get_value("count", 10 + step)
            assert actual == expected, \
                f"cycle {10+step}: expected {expected}, got {actual}"

    def test_final_count(self, counter_sv):
        """30サイクル後に count=20（10リセット+20カウント）"""
        eng = _make_engine(counter_sv)
        result = eng.run(cycles=30)
        assert result.get_value("count", 30) == 20

    def test_overflow_8bit(self, counter_sv):
        """8ビットオーバーフロー: 255+1=0"""
        eng = _make_engine(counter_sv, reset_cycles=2)
        result = eng.run(cycles=260)
        # after reset(2) + 255 increments = cycle 257 → count=255
        assert result.get_value("count", 257) == 255
        # cycle 258 → count = 0 (overflow)
        assert result.get_value("count", 258) == 0


class TestCounterParameterized:
    """WIDTH パラメータ上書きテスト"""

    def test_4bit_overflow(self, counter_sv):
        """WIDTH=4: 0..15 で循環する"""
        eng = _make_engine(counter_sv, param_overrides={"WIDTH": 4}, reset_cycles=2)
        result = eng.run(cycles=22)
        # after 2 reset cycles, 16 increments → cycle 18 = count 16 & 0xF = 0
        assert result.get_value("count", 2 + 15) == 15  # 最大値
        assert result.get_value("count", 2 + 16) == 0   # オーバーフロー

    def test_1bit_counter(self, counter_sv):
        """WIDTH=1: トグルフリップフロップ"""
        eng = _make_engine(counter_sv, param_overrides={"WIDTH": 1}, reset_cycles=1)
        result = eng.run(cycles=5)
        assert result.get_value("count", 1) == 0  # reset
        assert result.get_value("count", 2) == 1
        assert result.get_value("count", 3) == 0
        assert result.get_value("count", 4) == 1


class TestCounterVCD:
    """VCD 出力テスト"""

    def test_vcd_created(self, counter_sv, tmp_path):
        """VCD ファイルが生成されること"""
        from deltasim.vcd.writer import VCDWriter
        vcd_path = str(tmp_path / "counter.vcd")

        mod = parse_file(counter_sv)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=5)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=15)
        vcd.close()

        assert (tmp_path / "counter.vcd").exists()
        content = (tmp_path / "counter.vcd").read_text()
        assert "$timescale" in content
        assert "$dumpvars" in content
        assert "counter" in content

    def test_vcd_has_changes(self, counter_sv, tmp_path):
        """VCD にクロックエッジが記録されること"""
        from deltasim.vcd.writer import VCDWriter
        vcd_path = str(tmp_path / "counter2.vcd")

        mod = parse_file(counter_sv)
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=2)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=5)
        vcd.close()

        content = (tmp_path / "counter2.vcd").read_text()
        # タイムスタンプが存在すること
        assert "#" in content
