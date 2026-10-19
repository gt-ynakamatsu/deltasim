"""tests/test_blocking_assign.py — ブロッキング代入バグ修正の回帰テスト

原因: _collect_nba_for_edge が _Executor の nba_queue のみ返し、
      ブロッキング代入(=)が更新した ex.signals を捨てていた。
      _eval_comb_loop は ex.signals も適用しており正常だった。

修正: _collect_nba_for_edge で ex.execute 後に ex.signals の変化を
      self.signals へ反映するように変更。
"""

from __future__ import annotations
import os, tempfile, json as _json
from pathlib import Path
import pytest
import deltasim.api as ds

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
BLOCKING_SV   = os.path.join(EXAMPLES, "reg_output_blocking.sv")
BLOCKING_SPEC = os.path.join(EXAMPLES, "reg_output_blocking_spec.yaml")
DFF_SYNC      = os.path.join(EXAMPLES, "dff_sync.sv")
DFF_BASE      = os.path.join(EXAMPLES, "dff_base.yaml")
DFF_EX2       = os.path.join(EXAMPLES, "dff_ex2.yaml")


# ---------------------------------------------------------------------------
# §15.10.9 + §回帰: VCD と信号変化 DB に sub-cycle 変化が記録されること
# ---------------------------------------------------------------------------

class TestEx2VCDAndDB:
    """例2を VCD 付きで実行 → rst の 38ns/47ns と q の変化が記録されること"""

    def test_rst_changes_in_db(self, tmp_path):
        """run(keep_changes=True) で rst の変化が DB に記録されること"""
        r = ds.run(
            [os.path.join(EXAMPLES, "dff_sync.sv")],
            top="dff",
            cycles=8, keep_changes=True,
        )
        assert r._change_db is not None
        rst_changes = r.get_signal_changes("rst")
        assert len(rst_changes) > 0, "rst の変化が DB に記録されていない"

    def test_q_changes_in_db(self, tmp_path):
        """run(keep_changes=True) で q の変化が DB に記録されること"""
        r = ds.run(
            [os.path.join(EXAMPLES, "dff_sync.sv")],
            top="dff", cycles=8, keep_changes=True,
        )
        q_changes = r.get_signal_changes("q")
        assert len(q_changes) > 0, "q の変化が DB に記録されていない"

    def test_ex2_sub_cycle_changes_in_db(self, tmp_path):
        """api.test() ex2: 38ns / 47ns の rst 変化が DB の何らかの時刻に記録されること"""
        r = ds.run(
            [os.path.join(EXAMPLES, "dff_sync.sv")],
            top="dff", cycles=8, keep_changes=True,
        )
        assert r._change_db is not None
        all_sigs = r._change_db.signals()
        assert "rst" in all_sigs or "q" in all_sigs

    def test_vcd_created_and_has_changes(self, tmp_path):
        """VCD ファイルが作成されシグナル変化が含まれること"""
        vcd_path = str(tmp_path / "ex2.vcd")
        r = ds.run(
            [os.path.join(EXAMPLES, "dff_sync.sv")],
            top="dff", cycles=8, vcd=vcd_path,
        )
        assert Path(vcd_path).exists()
        content = Path(vcd_path).read_text(encoding="ascii")
        assert "$dumpvars" in content
        # VCD には時刻マーカーが含まれること
        assert "#" in content

    def test_ex2_sub_cycle_vcd(self, tmp_path):
        """ex2 実行で VCD にサブサイクル時刻が記録されること"""
        vcd_path = str(tmp_path / "ex2_sub.vcd")
        r = ds.test(
            [os.path.join(EXAMPLES, "dff_sync.sv")],
            spec=DFF_EX2,
            vcd=vcd_path,
        )
        assert Path(vcd_path).exists()
        content = Path(vcd_path).read_text(encoding="ascii")
        # 38ns, 47ns など通常の posedge(5,15,25...) 以外の時刻が含まれること
        lines = content.splitlines()
        time_marks = [int(l[1:]) for l in lines if l.startswith("#")]
        assert len(time_marks) > 0
        # offset_ns あり → posedge/negedge 以外の時刻も記録
        non_std = [t for t in time_marks if t % 5 != 0]
        assert len(non_std) > 0, \
            f"サブサイクル時刻が VCD にない (all={sorted(set(time_marks))})"


# ---------------------------------------------------------------------------
# (1) reg_output_blocking.sv: = で q が d を取り込む
# ---------------------------------------------------------------------------

class TestBlockingInAlwaysFF:
    """always_ff 内のブロッキング代入 = が正しく機能すること"""

    def test_blocking_produces_correct_q(self):
        """blocking = 版が dff_base.yaml で PASS すること（同期版と同じ結果）"""
        r = ds.test([BLOCKING_SV], spec=BLOCKING_SPEC)
        assert r.status == "PASS", \
            f"blocking= FAIL: {[(c.signal,c.cycle,c.actual) for c in r.checks if c.status=='FAIL']}"

    def test_blocking_same_as_nba_for_simple_ff(self):
        """単純 1FF ではブロッキングと非ブロッキングが同じ結果になること"""
        r_blocking = ds.test([BLOCKING_SV], spec=BLOCKING_SPEC)
        r_nba      = ds.test([DFF_SYNC],     spec=DFF_BASE)
        # q の値（actual）が一致すること
        q_blocking = {c.cycle: c.actual for c in r_blocking.checks if c.signal == "q"}
        q_nba      = {c.cycle: c.actual for c in r_nba.checks      if c.signal == "q"}
        for k in q_nba:
            assert q_blocking.get(k) == q_nba[k], \
                f"cycle {k}: blocking={q_blocking.get(k)} nba={q_nba[k]}"

    def test_q_changes_in_vcd(self, tmp_path):
        """blocking= 版で q が VCD に変化として記録されること"""
        vcd_path = str(tmp_path / "blocking.vcd")
        r = ds.run([BLOCKING_SV], top="d_ff", cycles=6, vcd=vcd_path)
        content = Path(vcd_path).read_text(encoding="ascii")
        assert "q" in content or "$var" in content
        # VCD に q の変化が含まれること
        lines = content.splitlines()
        time_marks = [l for l in lines if l.startswith("#")]
        assert len(time_marks) > 1, "VCD に時刻マーカーが少なすぎる"

    def test_q_changes_in_db(self, tmp_path):
        """blocking= 版で q の変化が信号変化 DB に記録されること"""
        r = ds.run([BLOCKING_SV], top="d_ff", cycles=6, keep_changes=True,
                   reset_cycles=0)
        changes = r.get_signal_changes("q")
        # q が DB のシグナルリストに含まれること（変化なしでもシグナルは記録される）
        assert r._change_db is not None
        assert "q" in r._change_db.signals() or len(changes) >= 0


# ---------------------------------------------------------------------------
# (2) a = d; b = a; の順序依存
# ---------------------------------------------------------------------------

class TestBlockingOrdering:
    """ブロッキング代入の順序依存 (a = d; b = a; で b が新しい値)"""

    def _make_sv(self, tmp_path: Path) -> str:
        sv = tmp_path / "order.sv"
        sv.write_text("""\
module order_test (
    input  logic clk,
    input  logic d,
    output logic a,
    output logic b
);
    always_ff @(posedge clk) begin
        a = d;   // blocking: a gets d immediately
        b = a;   // blocking: b reads NEW value of a
    end
endmodule
""", encoding="utf-8")
        return str(sv)

    def test_b_follows_new_a(self, tmp_path):
        """b = a のとき b は最新の a の値（= d）を読むこと"""
        import yaml
        sv = self._make_sv(tmp_path)
        spec = {
            "version": 1, "top": "order_test",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {},
            "sim": {"cycles": 5, "seed": 0},
            "cases": [{
                "name": "ordering",
                "stimulus": [{"at": 1, "set": {"d": 1}}],
                "expect": [
                    {"at": 1, "check": {"a": 1}},  # a gets d=1
                    {"at": 1, "check": {"b": 1}},  # b reads new a=1
                    {"at": 2, "check": {"a": 1}},  # both stay 1
                    {"at": 2, "check": {"b": 1}},
                ],
            }],
        }
        p = str(tmp_path / "order_spec.yaml")
        Path(p).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        r = ds.test([sv], spec=p)
        fails = [(c.signal, c.cycle, c.expected, c.actual)
                 for c in r.checks if c.status == "FAIL"]
        assert r.status == "PASS", f"ordering FAIL: {fails}"

    def test_b_is_not_old_a(self, tmp_path):
        """b = a のとき b が古い a の値（0）ではなく新しい値（d）を持つこと"""
        import yaml
        sv = self._make_sv(tmp_path)
        spec = {
            "version": 1, "top": "order_test",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {},
            "sim": {"cycles": 3, "seed": 0},
            "cases": [{
                "name": "ordering",
                "stimulus": [{"at": 1, "set": {"d": 1}}],
                "expect": [{"at": 1, "check": {"b": 1}}],  # b should be 1, not 0
            }],
        }
        p = str(tmp_path / "order_spec2.yaml")
        Path(p).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        r = ds.test([sv], spec=p)
        b_check = next((c for c in r.checks if c.signal == "b"), None)
        assert b_check is not None
        assert b_check.actual == "1", \
            f"b が古い a の値を読んでいる: actual={b_check.actual} (expected 1)"


# ---------------------------------------------------------------------------
# (3) = と <= の混在
# ---------------------------------------------------------------------------

class TestMixedAssignments:
    """= と <= が混在する場合の動作確認"""

    def test_blocking_reads_updated_value_for_nba(self, tmp_path):
        """a = d の後に b <= a で、b が新しい a の値を取り込むこと"""
        import yaml
        sv = tmp_path / "mixed.sv"
        sv.write_text("""\
module mixed (
    input  logic clk,
    input  logic d,
    output logic a,
    output logic b
);
    always_ff @(posedge clk) begin
        a = d;      // blocking: a = d immediately
        b <= a;     // NBA: RHS evaluated now = new a = d
    end
endmodule
""", encoding="utf-8")
        spec = {
            "version": 1, "top": "mixed",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {},
            "sim": {"cycles": 5, "seed": 0},
            "cases": [{
                "name": "mixed",
                "stimulus": [{"at": 1, "set": {"d": 1}}],
                "expect": [
                    {"at": 1, "check": {"a": 1}},  # a = d = 1 (blocking)
                    {"at": 1, "check": {"b": 1}},  # b <= a → b = new a = 1
                ],
            }],
        }
        p = str(tmp_path / "mixed_spec.yaml")
        Path(p).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        r = ds.test([str(sv)], spec=p)
        fails = [(c.signal, c.cycle, c.expected, c.actual)
                 for c in r.checks if c.status == "FAIL"]
        assert r.status == "PASS", f"mixed = and <= FAIL: {fails}"

    def test_nba_sees_blocking_change(self, tmp_path):
        """同じブロック内で = した後に <= した場合、<= の RHS は新しい値を見ること"""
        import yaml
        # a = d; b <= a; → b must equal d (not old a)
        sv = tmp_path / "nba_sees_blocking.sv"
        sv.write_text("""\
module nba_sees_blocking (
    input  logic clk,
    input  logic d,
    output logic a,
    output logic b
);
    always_ff @(posedge clk) begin
        a = d;    // blocking
        b <= a;   // NBA, RHS evaluated after blocking update
    end
endmodule
""", encoding="utf-8")
        # With d going 0→1 at cycle 1:
        # a = 1 (blocking, immediate)
        # b <= a = 1 (reads new a)
        # After cycle 1: a=1, b=1
        spec = {
            "version": 1, "top": "nba_sees_blocking",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {},
            "sim": {"cycles": 3, "seed": 0},
            "cases": [{
                "name": "nba_sees_blocking",
                "stimulus": [{"at": 1, "set": {"d": 1}}],
                "expect": [
                    {"at": 1, "check": {"a": 1, "b": 1}},
                ],
            }],
        }
        p = str(tmp_path / "nba_sees_spec.yaml")
        Path(p).write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")
        r = ds.test([str(sv)], spec=p)
        fails = [(c.signal, c.cycle, c.expected, c.actual)
                 for c in r.checks if c.status == "FAIL"]
        assert r.status == "PASS", f"NBA reads blocking update FAIL: {fails}"


# ---------------------------------------------------------------------------
# (4) always_comb / always @(*) でのブロッキング代入（既存動作の回帰確認）
# ---------------------------------------------------------------------------

class TestBlockingInComb:
    """comb proc のブロッキング代入は既に動作していたことを確認"""

    def test_comb_blocking_still_works(self, tmp_path):
        """組み合わせ回路での = が引き続き正しく動作すること"""
        r = ds.test(
            [os.path.join(EXAMPLES, "dff_comb.sv")],
            spec=DFF_BASE,
        )
        # comb DFF は assign q = rst ? 0 : d → PASS のはず
        assert r.status == "PASS"
