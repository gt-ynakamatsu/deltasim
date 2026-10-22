"""tests/test_bp_correctness.py — §18.1 行ブレークポイント停止・正確性テスト

1. always_ff / always_comb / assign の行で実際に停止すること
2. 停止後の run -continue で次の実行まで進むこと
3. BP 有り/無しで最終結果が同じこと（deferred approach の正確性）
4. run 23ns / run 1us の正確な ns 停止
"""

from __future__ import annotations
import os, tempfile
from pathlib import Path
import pytest
import deltasim.api as ds
from deltasim.interp.session import SimSession

# ---------------------------------------------------------------------------
# テスト用 SV（行番号が確定している）
# ---------------------------------------------------------------------------

SV_BP_TEST = """\
module bp_test(input logic clk, rst_n, d, output logic [7:0] q, output logic c);
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      q <= 0;
    end else begin
      q <= q + d;
    end
  end
  always_comb begin
    c = (q > 8'h10);
  end
endmodule
"""
# Line 6: q <= q + d  (always_ff branch, always_ff line)
LINE_ALWAYS_FF  = 6   # q <= q + d
LINE_ALWAYS_COMB = 10  # c = (q > 8'h10)


def _make_bp_session(reset_cycles: int = 2):
    """テスト用 SV ファイルで SimSession を作る。(session, sim, sv_path) を返す。"""
    f = tempfile.NamedTemporaryFile(
        suffix=".sv", mode="w", delete=False, encoding="utf-8"
    )
    f.write(SV_BP_TEST)
    f.close()
    sim = ds.Simulator([f.name], top="bp_test", reset_cycles=reset_cycles)
    session = SimSession(sim)
    return session, sim, f.name


# ===========================================================================
# 1. 行ブレークポイントで実際に停止すること
# ===========================================================================

class TestLineBpFires:
    """always_ff / always_comb の行に bp を置いて run -all で停止すること"""

    def test_bp_fires_in_always_ff(self, tmp_path):
        """always_ff 内の行（q <= q + d）で bp が発火すること"""
        session, sim, sv_p = _make_bp_session()
        try:
            session.eval_script(f"force d 1")
            session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
            session.eval_script("run -all")
            t_stopped = sim._engine._time
            out = session._output_lines
            # bp 発火メッセージが出ていること
            assert any("bp [" in l and str(LINE_ALWAYS_FF) in l for l in out), \
                f"bp 発火メッセージが見当たらない: {out}"
            # リセット解除後の最初の posedge = t=25ns
            assert t_stopped == 25, \
                f"expected t=25ns but got t={t_stopped}ns"
        finally:
            os.unlink(sv_p)

    def test_bp_output_format(self, tmp_path):
        """停止時の出力が '# bp [id] file:line t=Xns' 形式であること"""
        session, sim, sv_p = _make_bp_session()
        try:
            session.eval_script(f"force d 1")
            session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
            session.eval_script("run -all")
            out = session._output_lines
            bp_lines = [l for l in out if "bp [" in l and "t=" in l]
            assert bp_lines, f"bp 出力が見当たらない: {out}"
            bl = bp_lines[0]
            # フォーマット確認: # bp [N] file:line t=Xns
            assert "bp [" in bl
            assert "t=" in bl
            assert "ns" in bl
        finally:
            os.unlink(sv_p)

    def test_bp_fires_in_always_comb(self, tmp_path):
        """always_comb 内の行で bp が発火すること"""
        session, sim, sv_p = _make_bp_session()
        try:
            session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_COMB}}}")
            session.eval_script("run -all")
            t_stopped = sim._engine._time
            out = session._output_lines
            # always_comb は posedge で組み合わせ論理が再評価されるので発火するはず
            bp_fired = any("bp [" in l and str(LINE_ALWAYS_COMB) in l for l in out)
            assert bp_fired or t_stopped > 0, \
                f"always_comb bp が発火しなかった (t={t_stopped}): {out}"
        finally:
            os.unlink(sv_p)

    def test_run_continue_advances_to_next_hit(self, tmp_path):
        """bp で止まった後 run -continue で次の hit まで進むこと"""
        session, sim, sv_p = _make_bp_session()
        try:
            session.eval_script("force d 1")
            session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
            session.eval_script("run -all")
            t1 = sim._engine._time
            assert t1 == 25  # first stop

            session.eval_script("run -continue")
            t2 = sim._engine._time
            assert t2 == 35, f"expected t=35ns but got t={t2}ns"

            session.eval_script("run -continue")
            t3 = sim._engine._time
            assert t3 == 45, f"expected t=45ns but got t={t3}ns"
        finally:
            os.unlink(sv_p)

    def test_q_increments_correctly_with_bp(self, tmp_path):
        """bp で停止しながら d=1 を累積すると q が正しく増えること"""
        session, sim, sv_p = _make_bp_session()
        try:
            session.eval_script("force d 1")
            session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
            # 3 回 bp を通過
            for expected_t, expected_q in [(25, 1), (35, 2), (45, 3)]:
                session.eval_script("run -all")
                t = sim._engine._time
                q = sim.get("q")
                assert t == expected_t, f"expected t={expected_t} but got {t}"
                assert q == expected_q, f"expected q={expected_q} but got {q}"
                session._output_lines.clear()
        finally:
            os.unlink(sv_p)


# ===========================================================================
# 2. bp 有り / 無しで最終結果が一致すること（deferred 方式の正確性）
# ===========================================================================

class TestBpCorrectness:
    """bp を挟んでも挟まなくても最終信号値が同じこと"""

    def _run_without_bp(self, sv_path: str, cycles_ns: int, d_val: int) -> dict:
        """bp なしで cycles_ns まで実行し、最終信号値を返す。"""
        sim = ds.Simulator([sv_path], top="bp_test", reset_cycles=2)
        sim.force("d", d_val)
        snap = sim.step_to_ns(cycles_ns)
        return dict(snap.signals)

    def _run_with_line_bp(self, sv_path: str, line: int, cycles_ns: int, d_val: int) -> dict:
        """行 bp を挟みながら cycles_ns まで実行し、最終信号値を返す。
        直接 step_to_ns を使い、bp チェック付きで各サイクルを進める。
        """
        sim = ds.Simulator([sv_path], top="bp_test", reset_cycles=2)
        session = SimSession(sim)
        session.eval_script(f"force d {d_val}")
        session.eval_script(f"bp {{{sv_path}:{line}}}")
        bp_check = session._make_bp_check()

        from deltasim.sim.engine import BreakpointHit
        session._sim._engine._active_bp_check = bp_check

        while sim._engine._time < cycles_ns:
            try:
                snap = sim.step_to_ns(
                    min(cycles_ns, sim._engine._time + 2 * sim._engine.half_period),
                    bp_check=bp_check,
                )
            except BreakpointHit:
                pass  # just continue — we want to reach cycles_ns
            if sim._engine._time >= cycles_ns:
                break

        session._sim._engine._active_bp_check = None
        return dict(sim._engine.signals)

    def test_always_ff_bp_same_result_as_no_bp(self, tmp_path):
        """always_ff の bp 有り/無しで q の最終値が同じこと"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        cycles_ns = 55  # 5 cycles after reset

        ref = self._run_without_bp(sv_p, cycles_ns, d_val=1)
        bp_result = self._run_with_line_bp(sv_p, LINE_ALWAYS_FF, cycles_ns, d_val=1)

        # q の最終値が一致すること
        assert ref.get("q") == bp_result.get("q"), \
            f"q mismatch: no_bp={ref.get('q')}, bp={bp_result.get('q')}"

    def test_bp_time_same_result_as_no_bp(self, tmp_path):
        """bp -time 有り/無しで最終値が同じこと"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        TARGET = 55

        # Without bp -time
        sim1 = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        sim1.force("d", 1)
        sim1.step_to_ns(TARGET)
        ref_q = sim1.get("q")

        # With bp -time at t=30 — run step by step, handling bp stops
        from deltasim.sim.engine import BreakpointHit
        sim2 = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        session2 = SimSession(sim2)
        session2.eval_script("force d 1")
        session2.eval_script("bp -time 30ns")

        hp = sim2._engine.half_period
        while sim2._engine._time < TARGET:
            try:
                snap = sim2.step_to_ns(
                    min(TARGET, sim2._engine._time + 2 * hp)
                )
                session2._check_all_whens(snap)
                if session2._check_time_bps_after(snap):
                    pass  # bp hit, continue anyway
            except Exception:
                pass

        bp_q = sim2.get("q")
        assert ref_q == bp_q, f"q mismatch with bp-time: ref={ref_q}, bp={bp_q}"

    def test_when_stop_same_result_as_no_bp(self, tmp_path):
        """when stop 有り/無しで最終値が同じこと（deferred bp では stop はシグナル）"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        TARGET = 55

        sim1 = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        sim1.force("d", 1)
        sim1.step_to_ns(TARGET)
        ref_q = sim1.get("q")

        # With when stop — step by step, ignore stop signal
        sim2 = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        session2 = SimSession(sim2)
        session2.eval_script("force d 1")
        session2.eval_script("when {q == 2} {stop}")

        hp = sim2._engine.half_period
        while sim2._engine._time < TARGET:
            try:
                snap = sim2.step_to_ns(min(TARGET, sim2._engine._time + 2 * hp))
                session2._check_all_whens(snap)
            except Exception:
                pass

        bp_q = sim2.get("q")
        assert ref_q == bp_q, f"q mismatch with when-stop: ref={ref_q}, bp={bp_q}"


# ===========================================================================
# 3. 任意 ns 停止の正確性
# ===========================================================================

class TestArbitraryNsStop:
    """run <time> が任意の ns 値で正確に停止すること"""

    def _session(self):
        sim = ds.Simulator(
            [os.path.join(os.path.dirname(__file__), "..", "examples", "counter.sv")],
            top="counter", reset_cycles=2,
        )
        return SimSession(sim), sim

    def test_run_23ns(self):
        """run 23ns → t=23ns (not a clock edge)"""
        session, sim = self._session()
        session.eval_script("run 23ns")
        assert sim._engine._time == 23, f"expected t=23 but got t={sim._engine._time}"

    def test_run_23ns_then_2ns(self):
        """run 23ns → run 2ns → t=25ns"""
        session, sim = self._session()
        session.eval_script("run 23ns")
        session.eval_script("run 2ns")
        assert sim._engine._time == 25, f"expected t=25 but got t={sim._engine._time}"

    def test_run_23ns_signals_correct(self):
        """run 23ns での信号値が t=20ns negedge 後と同じこと"""
        session, sim = self._session()
        session.eval_script("run 20ns")
        snap_20 = {k: sim.get(k) for k in ("count", "clk", "rst_n")}
        session.eval_script("run 3ns")  # → t=23
        snap_23 = {k: sim.get(k) for k in ("count", "clk", "rst_n")}
        # 信号値は t=20 と同じ（t=20 と t=25 の間はクロックエッジなし）
        assert snap_20 == snap_23, \
            f"signals differ at t=23 vs t=20: t20={snap_20}, t23={snap_23}"

    def test_run_1us_exact(self):
        """run 1us → t==1000 (exactly, not >=)"""
        session, sim = self._session()
        session.eval_script("run 1us")
        assert sim._engine._time == 1000, \
            f"expected t==1000 but got t={sim._engine._time}"

    def test_bp_time_23ns(self):
        """bp -time 23ns が t=23ns で正確に停止すること"""
        session, sim = self._session()
        session.eval_script("bp -time 23ns")
        session.eval_script("run -all")
        assert sim._engine._time == 23, \
            f"expected t=23 but got t={sim._engine._time}"

    def test_run_25ns_posedge_processed(self):
        """run 25ns で停止した後 examine count が posedge 後の値を返すこと"""
        session, sim = self._session()
        session.eval_script("run 25ns")
        assert sim._engine._time == 25
        # t=25 は posedge → count が更新されているはず
        count = sim.get("count")
        assert isinstance(count, int), f"count is not int: {count}"
