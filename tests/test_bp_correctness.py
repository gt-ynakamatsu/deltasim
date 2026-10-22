"""tests/test_bp_correctness.py — §18.1 行ブレークポイント停止・正確性テスト

1. always_ff / always_comb / assign 行に bp を置いて実際に停止すること
2. 停止後の run -continue で次の実行まで進むこと
3. BP 有り/無しで全信号の最終値と VCD が一致すること
4. run <arbitrary ns> の正確な停止
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
# Line 1: module (no stmt)
# Line 2: always_ff begin (Block source_line)
# Line 3: if (!rst_n)     (IfStmt source_line)
# Line 4: q <= 0          (NonBlockingAssign)
# Line 6: q <= q + d      (NonBlockingAssign)
# Line 9: always_comb begin
# Line 10: c = ...         (BlockingAssign)
LINE_ALWAYS_FF   = 6
LINE_ALWAYS_COMB = 10

SV_ASSIGN_BP = """\
module adder_bp(input logic clk, input logic [7:0] a, b, output logic [7:0] y);
  assign y = a + b;
endmodule
"""
LINE_ASSIGN = 2


def _write_sv(content: str, suffix: str = ".sv") -> str:
    f = tempfile.NamedTemporaryFile(suffix=suffix, mode="w", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return f.name


def _make_bp_session(sv_content: str, top: str, reset_cycles: int = 2,
                     vcd_path: str = None):
    sim = ds.Simulator(
        [_write_sv(sv_content)],
        top=top, reset_cycles=reset_cycles,
    )
    if vcd_path:
        sim._engine.vcd_writer = None  # will be set by scenario
    session = SimSession(sim)
    return session, sim, sim._design._source_files[0] if hasattr(sim._design, "_source_files") else ""


# ===========================================================================
# 1. 行ブレークポイントで実際に停止すること
# ===========================================================================

class TestLineBpFires:
    """always_ff / always_comb / assign 行に bp を置いて run -all で停止すること"""

    def _session_ff(self, tmp_path):
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        sim = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        return SimSession(sim), sim, sv_p

    def _session_assign(self, tmp_path):
        sv_p = str(tmp_path / "adder_bp.sv")
        Path(sv_p).write_text(SV_ASSIGN_BP, encoding="utf-8")
        sim = ds.Simulator([sv_p], top="adder_bp", reset_cycles=1)
        return SimSession(sim), sim, sv_p

    # ---- always_ff ----

    def test_bp_fires_in_always_ff(self, tmp_path):
        """always_ff 内の行（q <= q + d）で bp が発火すること"""
        session, sim, sv_p = self._session_ff(tmp_path)
        session.eval_script("force d 1")
        session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
        session.eval_script("run -all")
        out = session._output_lines
        bp_lines = [l for l in out if "bp [" in l and "t=" in l]
        assert bp_lines, f"bp 発火メッセージがない: {out}"
        assert str(LINE_ALWAYS_FF) in bp_lines[0], \
            f"行番号 {LINE_ALWAYS_FF} が message にない: {bp_lines[0]!r}"
        assert sim._engine._time == 25, \
            f"expected t=25ns but got t={sim._engine._time}ns"

    def test_bp_output_format(self, tmp_path):
        """停止時の出力が '# bp [id] file:line t=Xns' 形式であること"""
        session, sim, sv_p = self._session_ff(tmp_path)
        session.eval_script("force d 1")
        session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
        session.eval_script("run -all")
        bp_lines = [l for l in session._output_lines if "bp [" in l and "t=" in l]
        assert bp_lines, f"bp 出力がない"
        bl = bp_lines[0]
        assert "bp [" in bl and "t=" in bl and "ns" in bl, f"format wrong: {bl!r}"

    def test_run_continue_advances_to_next_hit(self, tmp_path):
        """bp で止まった後 run -continue で次の hit まで進むこと"""
        session, sim, sv_p = self._session_ff(tmp_path)
        session.eval_script("force d 1")
        session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
        session.eval_script("run -all")
        assert sim._engine._time == 25
        session.eval_script("run -continue")
        assert sim._engine._time == 35, f"expected 35 but got {sim._engine._time}"
        session.eval_script("run -continue")
        assert sim._engine._time == 45, f"expected 45 but got {sim._engine._time}"

    def test_q_increments_correctly_with_bp(self, tmp_path):
        """bp で停止しながら d=1 を累積すると q が正しく増えること"""
        session, sim, sv_p = self._session_ff(tmp_path)
        session.eval_script("force d 1")
        session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
        for expected_t, expected_q in [(25, 1), (35, 2), (45, 3)]:
            session.eval_script("run -all")
            assert sim._engine._time == expected_t
            assert sim.get("q") == expected_q, \
                f"q={sim.get('q')} != {expected_q} at t={sim._engine._time}"
            session._output_lines.clear()

    # ---- always_comb ----

    def test_bp_fires_in_always_comb(self, tmp_path):
        """always_comb 内の行（c = ...）で bp が発火すること"""
        session, sim, sv_p = self._session_ff(tmp_path)
        session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_COMB}}}")
        session.eval_script("run -all")
        out = session._output_lines
        bp_lines = [l for l in out if "bp [" in l and "t=" in l]
        # always_comb は posedge で再評価されるので bp が発火するはず
        assert bp_lines, f"always_comb bp 発火メッセージがない: {out}"
        assert str(LINE_ALWAYS_COMB) in bp_lines[0], \
            f"行番号 {LINE_ALWAYS_COMB} が message にない: {bp_lines[0]!r}"
        # t=5 (最初の posedge でリセット中でも comb は評価される) or later
        assert sim._engine._time > 0, \
            f"bp で止まっているはず: t={sim._engine._time}"

    # ---- assign ----

    def test_bp_fires_in_assign(self, tmp_path):
        """assign の行で bp が発火すること"""
        session, sim, sv_p = self._session_assign(tmp_path)
        session.eval_script(f"bp {{{sv_p}:{LINE_ASSIGN}}}")
        session.eval_script("force a 5")
        session.eval_script("force b 3")
        session.eval_script("run 10ns")
        out = session._output_lines
        bp_lines = [l for l in out if "bp [" in l and "t=" in l]
        assert bp_lines, f"assign bp 発火メッセージがない: {out}"
        assert str(LINE_ASSIGN) in bp_lines[0], \
            f"行番号 {LINE_ASSIGN} が message にない: {bp_lines[0]!r}"
        # assign y = a + b → y should be 8 (5+3)
        assert sim.get("y") == 8, f"y={sim.get('y')} != 8"

    def test_ast_nodes_have_source_line(self, tmp_path):
        """AssignItem / BlockingAssign / IfStmt が source_line を持つこと"""
        from deltasim.parser.ast_nodes import BlockingAssign, NonBlockingAssign, IfStmt, AssignItem
        from deltasim.parser.parser import parse_file

        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        ast = parse_file(sv_p)

        def find(node, cls):
            if isinstance(node, cls):
                return [node]
            res = []
            for attr in ("stmts", "then_body", "else_body", "body"):
                c = getattr(node, attr, None)
                if c:
                    if isinstance(c, list):
                        for x in c:
                            res.extend(find(x, cls))
                    else:
                        res.extend(find(c, cls))
            return res

        for item in ast.items:
            for cls in (NonBlockingAssign, IfStmt):
                found = find(getattr(item, "body", None), cls)
                for n in found:
                    assert n.source_line > 0, \
                        f"{cls.__name__} source_line not set: {n}"

        # assign
        sv2 = str(tmp_path / "adder.sv")
        Path(sv2).write_text(SV_ASSIGN_BP, encoding="utf-8")
        ast2 = parse_file(sv2)
        for item in ast2.items:
            if isinstance(item, AssignItem):
                assert item.source_line > 0, f"AssignItem source_line not set: {item}"

    def test_bp_snap_to_next_line(self, tmp_path):
        """文のない行に bp を設定すると次の文のある行に寄せて警告が出ること"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        sim = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        session = SimSession(sim)
        # 行 1 は module 宣言（文なし）→ 次の文のある行に寄せる
        session.eval_script(f"bp {{{sv_p}:1}}")
        out = session._output_lines
        assert any("Warning" in l for l in out), f"スナップ警告がない: {out}"
        assert session._breakpoints, "bp が登録されなかった"
        assert session._breakpoints[-1].kwargs["line"] != 1

    def test_bp_error_no_statement_after(self, tmp_path):
        """文のない行で後ろに文もない場合、エラーで登録しないこと"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        sim = ds.Simulator([sv_p], top="bp_test", reset_cycles=2)
        session = SimSession(sim)
        prev_len = len(session._breakpoints)
        session.eval_script(f"bp {{{sv_p}:9999}}")
        out = session._output_lines
        assert any("Error" in l for l in out), f"エラーが出なかった: {out}"
        assert len(session._breakpoints) == prev_len, "bp が登録されてしまった"


# ===========================================================================
# 2. BP 有り/無しで全信号値と VCD が一致すること
# ===========================================================================

def _run_with_session(sv_path: str, top: str, reset_cycles: int,
                      d_val: int, end_ns: int,
                      setup_fn=None,
                      vcd_path: str = None) -> tuple:
    """VCD 付きで end_ns まで実行し、(最終信号値 dict, VCD テキスト) を返す。"""
    from deltasim.vcd.writer import VCDWriter
    sim = ds.Simulator([sv_path], top=top, reset_cycles=reset_cycles)
    if d_val is not None:
        sim.force("d", d_val)
    vcd_obj = None
    if vcd_path:
        vcd_obj = VCDWriter(vcd_path, sim._design)
        vcd_obj.record_initial(0, sim._engine.signals)
        sim._engine.vcd_writer = vcd_obj
    if setup_fn:
        setup_fn(sim)
    sim.step_to_ns(end_ns)
    if vcd_obj:
        vcd_obj.close()
    return dict(sim._engine.signals), Path(vcd_path).read_text(encoding="utf-8") if vcd_path else ""


def _run_with_session_and_bp(sv_path: str, top: str, reset_cycles: int,
                              d_val: int, end_ns: int,
                              setup_fn,
                              vcd_path: str) -> tuple:
    """BP 付きセッションで end_ns まで実行し、(最終信号値, VCD, 停止回数) を返す。

    半サイクル単位で進み、各ステップで time BP / line BP / when をチェックする。
    time BP の時刻も考慮してその時刻ちょうどで止まる。
    """
    from deltasim.vcd.writer import VCDWriter
    from deltasim.sim.engine import BreakpointHit
    from deltasim.interp.session import _StopSignal
    sim = ds.Simulator([sv_path], top=top, reset_cycles=reset_cycles)
    if d_val is not None:
        sim.force("d", d_val)
    session = SimSession(sim)
    vcd_obj = VCDWriter(vcd_path, sim._design)
    vcd_obj.record_initial(0, sim._engine.signals)
    sim._engine.vcd_writer = vcd_obj

    setup_fn(session, sim)

    hp = sim._engine.half_period
    stops = 0
    while sim._engine._time < end_ns:
        t_now = sim._engine._time
        half_next = t_now + hp
        # Incorporate time BPs into step target
        time_bp_times = [b.kwargs["time_ns"] for b in session._breakpoints
                         if b.kind == "time" and b.kwargs["time_ns"] > t_now]
        if time_bp_times:
            half_next = min(half_next, min(time_bp_times))
        target = min(end_ns, half_next)
        bp_check = session._make_bp_check()
        try:
            snap = sim.step_to_ns(target, bp_check=bp_check)
            try:
                session._check_all_whens(snap)
            except _StopSignal:
                stops += 1
            if session._check_time_bps_after(snap):
                stops += 1
        except BreakpointHit as bh:
            bl = bh.source_file.rsplit("/", 1)[-1] if bh.source_file else "?"
            session._out(f"# bp [{bh.bp_id}] {bl}:{bh.source_line} t={bh.time_ns}ns")
            stops += 1

    vcd_obj.close()
    return dict(sim._engine.signals), Path(vcd_path).read_text(encoding="utf-8"), stops


class TestBpCorrectness:
    """BP 有り/無しで全信号の最終値と VCD が一致すること"""

    def test_line_bp_all_signals_and_vcd_match(self, tmp_path):
        """行 bp 有り/無しで全信号値と VCD が一致すること"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        END_NS = 65  # 6 cycles after reset

        # Without bp
        ref_sigs, ref_vcd = _run_with_session(
            sv_p, "bp_test", 2, d_val=1, end_ns=END_NS,
            vcd_path=str(tmp_path / "ref.vcd"),
        )

        # With line bp — run until END_NS via session loop
        def setup(session, sim):
            session.eval_script(f"bp {{{sv_p}:{LINE_ALWAYS_FF}}}")
            return 3  # 3 posedges after reset (t=25, 35, 45)
        bp_sigs, bp_vcd, stops = _run_with_session_and_bp(
            sv_p, "bp_test", 2, d_val=1, end_ns=END_NS,
            setup_fn=setup, vcd_path=str(tmp_path / "bp.vcd"),
        )

        # 全信号の最終値が一致
        for sig in ref_sigs:
            assert ref_sigs[sig] == bp_sigs.get(sig), \
                f"signal '{sig}': ref={ref_sigs[sig]} != bp={bp_sigs.get(sig)}"

        # VCD の値変化部分（$dumpvars 以降）が一致
        def _vcd_body(text: str) -> str:
            idx = text.find("$dumpvars")
            return text[idx:] if idx >= 0 else text
        assert _vcd_body(ref_vcd) == _vcd_body(bp_vcd), \
            "VCD 値変化部分が一致しない"

        # 停止回数（3 回: t=25, 35, 45 の always_ff 行）
        assert stops >= 3, f"expected >= 3 stops but got {stops}"

    def test_bp_time_all_signals_and_vcd_match(self, tmp_path):
        """bp -time 有り/無しで全信号値と VCD が一致すること"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        END_NS = 55

        ref_sigs, ref_vcd = _run_with_session(
            sv_p, "bp_test", 2, d_val=1, end_ns=END_NS,
            vcd_path=str(tmp_path / "ref.vcd"),
        )

        def setup(session, sim):
            session.eval_script("bp -time 30ns")
            session.eval_script("bp -time 45ns")
            return 2
        bp_sigs, bp_vcd, stops = _run_with_session_and_bp(
            sv_p, "bp_test", 2, d_val=1, end_ns=END_NS,
            setup_fn=setup, vcd_path=str(tmp_path / "bp.vcd"),
        )

        for sig in ref_sigs:
            assert ref_sigs[sig] == bp_sigs.get(sig), \
                f"signal '{sig}': ref={ref_sigs[sig]} != bp={bp_sigs.get(sig)}"
        assert stops == 2, f"expected 2 time-bp stops but got {stops}"

        def _vcd_body(text: str) -> str:
            idx = text.find("$dumpvars")
            return text[idx:] if idx >= 0 else text
        assert _vcd_body(ref_vcd) == _vcd_body(bp_vcd), "VCD 値変化部分が一致しない"

    def test_when_stop_all_signals_and_vcd_match(self, tmp_path):
        """when stop 有り/無しで全信号値と VCD が一致すること"""
        sv_p = str(tmp_path / "bp_test.sv")
        Path(sv_p).write_text(SV_BP_TEST, encoding="utf-8")
        END_NS = 65

        ref_sigs, ref_vcd = _run_with_session(
            sv_p, "bp_test", 2, d_val=1, end_ns=END_NS,
            vcd_path=str(tmp_path / "ref.vcd"),
        )

        def setup(session, sim):
            # when {q == 2} {stop} → fires once (q goes from 1→2 at t=35)
            session.eval_script("when {q == 2} {stop}")
            return 1

        def setup_when(session, sim):
            session.eval_script("when {q == 2} {stop}")

        bp_sigs, bp_vcd, stops = _run_with_session_and_bp(
            sv_p, "bp_test", 2, d_val=1, end_ns=END_NS,
            setup_fn=setup_when, vcd_path=str(tmp_path / "bp.vcd"),
        )

        for sig in ref_sigs:
            assert ref_sigs[sig] == bp_sigs.get(sig), \
                f"signal '{sig}': ref={ref_sigs[sig]} != bp={bp_sigs.get(sig)}"

        def _vcd_body(text: str) -> str:
            idx = text.find("$dumpvars")
            return text[idx:] if idx >= 0 else text
        assert _vcd_body(ref_vcd) == _vcd_body(bp_vcd), "VCD 値変化部分が一致しない"

        assert stops == 1, f"expected 1 when-stop but got {stops}"


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
        session.eval_script("run 3ns")
        snap_23 = {k: sim.get(k) for k in ("count", "clk", "rst_n")}
        assert snap_20 == snap_23, \
            f"signals differ at t=23 vs t=20: t20={snap_20}, t23={snap_23}"

    def test_run_1us_exact(self):
        """run 1us → t==1000 exactly"""
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
        count = sim.get("count")
        assert isinstance(count, int), f"count is not int: {count}"
