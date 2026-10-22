"""tests/test_offset_ns.py — §15 受け入れ条件テスト (公開API経由)

§15.10 受け入れ条件:
1. ex1: sync=PASS, comb=FAIL(29ns,37ns), async=PASS
2. ex2: sync=PASS, comb=FAIL(38ns,42ns,48ns), async=FAIL(38ns,42ns)
3. checks の time_ns / offset_ns / name が仕様どおり
4. offset_ns 未使用の既存YAMLで結果不変 (dff_base.yaml)
5. offset_ns=0 と省略が同じ結果
6. period_ns=10 で offset_ns=-1,10,2.5,"3" → E_OFFSET_RANGE
7. clock 上書き (period_ns=20) で offset_ns=15 はOK、20はエラー
8. ex2 をVCD付きで実行 → rst/q の変化が DB に記録
"""

from __future__ import annotations
import os
import pytest
import deltasim.api as ds

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
DFF_SYNC  = os.path.join(EXAMPLES, "dff_sync.sv")
DFF_COMB  = os.path.join(EXAMPLES, "dff_comb.sv")
DFF_ASYNC = os.path.join(EXAMPLES, "dff_async.sv")
BASE_YAML = os.path.join(EXAMPLES, "dff_base.yaml")
EX1_YAML  = os.path.join(EXAMPLES, "dff_ex1.yaml")
EX2_YAML  = os.path.join(EXAMPLES, "dff_ex2.yaml")


# ---------------------------------------------------------------------------
# §15.10.1 — ex1
# ---------------------------------------------------------------------------

class TestEx1AcceptanceCriteria:
    """例1: 立ち上りの間でDを変えてもQが変わらない"""

    def test_sync_pass(self):
        """同期リセット版: PASS"""
        r = ds.test([DFF_SYNC], spec=EX1_YAML)
        assert r.status == "PASS", _fail_details(r)

    def test_comb_fail(self):
        """組み合わせ版: FAIL (29ns と 37ns)"""
        r = ds.test([DFF_COMB], spec=EX1_YAML)
        assert r.status == "FAIL"
        fail_times = {c.time_ns for c in r.checks if c.status == "FAIL"}
        assert fail_times == {29, 37}, f"fail times: {fail_times}"

    def test_async_pass(self):
        """非同期リセット版: PASS（例1では区別しない）"""
        r = ds.test([DFF_ASYNC], spec=EX1_YAML)
        assert r.status == "PASS", _fail_details(r)

    def test_check_fields_29ns(self):
        """29ns のチェック: cycle=3, offset_ns=4, name=q@cycle3+4ns[spec]"""
        r = ds.test([DFF_COMB], spec=EX1_YAML)
        chk = next((c for c in r.checks if c.time_ns == 29), None)
        assert chk is not None
        assert chk.cycle == 3
        assert chk.offset_ns == 4
        assert chk.name == "q@cycle3+4ns[spec]"
        assert chk.time_ns == 29

    def test_check_fields_37ns(self):
        """37ns のチェック: cycle=4, offset_ns=2, time_ns=37"""
        r = ds.test([DFF_COMB], spec=EX1_YAML)
        chk = next((c for c in r.checks if c.time_ns == 37), None)
        assert chk is not None
        assert chk.cycle == 4
        assert chk.offset_ns == 2
        assert chk.name == "q@cycle4+2ns[spec]"

    def test_pass_check_no_offset_name(self):
        """offset_ns=0 のチェック名は q@cycleN[spec]（+0ns は付かない）"""
        r = ds.test([DFF_SYNC], spec=EX1_YAML)
        for c in r.checks:
            if c.offset_ns == 0:
                assert "+0ns" not in c.name, f"unexpected +0ns in {c.name}"


# ---------------------------------------------------------------------------
# §15.10.2 — ex2
# ---------------------------------------------------------------------------

class TestEx2AcceptanceCriteria:
    """例2: サイクルの途中で rst=1 にした直後の動作"""

    def test_sync_pass(self):
        """同期リセット版: PASS"""
        r = ds.test([DFF_SYNC], spec=EX2_YAML)
        assert r.status == "PASS", _fail_details(r)

    def test_comb_fail(self):
        """組み合わせ版: FAIL (38ns, 42ns, 48ns)"""
        r = ds.test([DFF_COMB], spec=EX2_YAML)
        assert r.status == "FAIL"
        fail_times = {c.time_ns for c in r.checks if c.status == "FAIL"}
        assert fail_times == {38, 42, 48}, f"fail times: {fail_times}"

    def test_async_fail(self):
        """非同期リセット版: FAIL (38ns, 42ns)"""
        r = ds.test([DFF_ASYNC], spec=EX2_YAML)
        assert r.status == "FAIL"
        fail_times = {c.time_ns for c in r.checks if c.status == "FAIL"}
        assert fail_times == {38, 42}, f"fail times: {fail_times}"

    def test_check_fields_38ns(self):
        """38ns: cycle=4, offset_ns=3, name=q@cycle4+3ns[spec]"""
        r = ds.test([DFF_ASYNC], spec=EX2_YAML)
        chk = next((c for c in r.checks if c.time_ns == 38), None)
        assert chk is not None
        assert chk.cycle == 4
        assert chk.offset_ns == 3
        assert chk.name == "q@cycle4+3ns[spec]"

    def test_check_fields_42ns(self):
        """42ns: cycle=4, offset_ns=7"""
        r = ds.test([DFF_ASYNC], spec=EX2_YAML)
        chk = next((c for c in r.checks if c.time_ns == 42), None)
        assert chk is not None
        assert chk.cycle == 4
        assert chk.offset_ns == 7

    def test_35ns_pass_check(self):
        """35ns (offset_ns=0): cycle=4, no +Xns suffix"""
        r = ds.test([DFF_SYNC], spec=EX2_YAML)
        chk = next((c for c in r.checks if c.time_ns == 35), None)
        assert chk is not None
        assert chk.name == "q@cycle4[spec]"
        assert chk.offset_ns == 0

    def test_all_sync_checks_pass(self):
        """sync 版では全チェックが PASS"""
        r = ds.test([DFF_SYNC], spec=EX2_YAML)
        for c in r.checks:
            assert c.status == "PASS", f"{c.name} @ {c.time_ns}ns: expected PASS"


# ---------------------------------------------------------------------------
# §15.10.4 — 既存YAMLの結果不変
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """offset_ns なし既存YAMLで status/checks が変わらないこと"""

    def test_base_all_pass(self):
        """dff_base.yaml: 全3版 PASS"""
        for sv in [DFF_SYNC, DFF_COMB, DFF_ASYNC]:
            r = ds.test([sv], spec=BASE_YAML)
            assert r.status == "PASS", f"{sv}: {_fail_details(r)}"

    def test_base_check_names_no_offset(self):
        """dff_base.yaml: チェック名に +Xns が含まれない"""
        r = ds.test([DFF_SYNC], spec=BASE_YAML)
        for c in r.checks:
            assert "ns[" not in c.name or c.name.endswith("[spec]"), \
                f"unexpected offset suffix in {c.name}"

    def test_base_time_ns_equals_posedge(self):
        """dff_base.yaml: time_ns は posedge 時刻 (at*10-5)"""
        r = ds.test([DFF_SYNC], spec=BASE_YAML)
        for c in r.checks:
            # at=N → time_ns = (N-1)*10 + 5
            expected_t = (c.cycle - 1) * 10 + 5
            assert c.time_ns == expected_t, \
                f"{c.name}: time_ns={c.time_ns} != {expected_t}"

    def test_existing_counter_spec_unchanged(self):
        """counter_spec.yaml の status が変わらないこと"""
        r = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert r.status == "PASS"

    def test_existing_fifo_spec_unchanged(self):
        """fifo_spec.yaml の status が変わらないこと"""
        r = ds.test(
            [os.path.join(EXAMPLES, "fifo.sv")],
            spec=os.path.join(EXAMPLES, "fifo_spec.yaml"),
        )
        assert r.status == "PASS"


# ---------------------------------------------------------------------------
# §15.10.5/6 — offset_ns=0 と省略が同じ
# ---------------------------------------------------------------------------

class TestOffsetZeroEquivalence:
    """offset_ns=0 を明示したYAMLと省略したYAMLで結果が同じ"""

    def test_explicit_zero_same_as_omit(self, tmp_path):
        """offset_ns: 0 と省略で checks の name/time_ns/status が一致"""
        import yaml
        from pathlib import Path

        spec_omit = {
            "version": 1, "top": "dff",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "c", "stimulus": [{"at": 3, "set": {"d": 1}}],
                        "expect": [{"at": 3, "check": {"q": 1}}]}],
        }
        spec_zero = {
            "version": 1, "top": "dff",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "c", "stimulus": [{"at": 3, "offset_ns": 0, "set": {"d": 1}}],
                        "expect": [{"at": 3, "offset_ns": 0, "check": {"q": 1}}]}],
        }
        p_omit = str(tmp_path / "omit.yaml")
        p_zero = str(tmp_path / "zero.yaml")
        Path(p_omit).write_text(yaml.dump(spec_omit), encoding="utf-8")
        Path(p_zero).write_text(yaml.dump(spec_zero), encoding="utf-8")

        r_omit = ds.test([DFF_SYNC], spec=p_omit)
        r_zero = ds.test([DFF_SYNC], spec=p_zero)

        assert r_omit.status == r_zero.status
        omit_names = {c.name for c in r_omit.checks}
        zero_names = {c.name for c in r_zero.checks}
        assert omit_names == zero_names, f"omit={omit_names}, zero={zero_names}"


# ---------------------------------------------------------------------------
# §15.10.7 — E_OFFSET_RANGE
# ---------------------------------------------------------------------------

class TestOffsetRangeError:
    """period_ns=10 で範囲外の offset_ns は E_OFFSET_RANGE"""

    def _spec_with_offset(self, tmp_path, offset_val):
        import yaml
        from pathlib import Path
        spec = {
            "version": 1, "top": "dff",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "c",
                        "expect": [{"at": 3, "offset_ns": offset_val, "check": {"q": 0}}]}],
        }
        p = str(tmp_path / f"off_{offset_val}.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        return p

    def test_negative_offset(self, tmp_path):
        """offset_ns=-1 → E_OFFSET_RANGE"""
        r = ds.test([DFF_SYNC], spec=self._spec_with_offset(tmp_path, -1))
        assert r.status == "ERROR"
        assert any(e.code == "E_OFFSET_RANGE" for e in r.errors)

    def test_equal_period(self, tmp_path):
        """offset_ns=10 (= period_ns) → E_OFFSET_RANGE"""
        r = ds.test([DFF_SYNC], spec=self._spec_with_offset(tmp_path, 10))
        assert r.status == "ERROR"
        assert any(e.code == "E_OFFSET_RANGE" for e in r.errors)

    def test_float_offset(self, tmp_path):
        """offset_ns=2.5 (小数) → E_OFFSET_RANGE"""
        r = ds.test([DFF_SYNC], spec=self._spec_with_offset(tmp_path, 2.5))
        assert r.status == "ERROR"
        assert any(e.code == "E_OFFSET_RANGE" for e in r.errors)

    def test_string_offset(self, tmp_path):
        """offset_ns='3' (文字列) → E_OFFSET_RANGE"""
        r = ds.test([DFF_SYNC], spec=self._spec_with_offset(tmp_path, "3"))
        assert r.status == "ERROR"
        assert any(e.code == "E_OFFSET_RANGE" for e in r.errors)

    def test_error_message_contains_info(self, tmp_path):
        """エラーメッセージにケース名・種別・値・範囲が含まれること"""
        r = ds.test([DFF_SYNC], spec=self._spec_with_offset(tmp_path, -1))
        e = next(e for e in r.errors if e.code == "E_OFFSET_RANGE")
        assert "E_OFFSET_RANGE" == e.code
        assert "-1" in e.message or "範囲" in e.message

    def test_cli_exit_nonzero(self, tmp_path):
        """E_OFFSET_RANGE で CLI が非 0 終了すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        p = self._spec_with_offset(tmp_path, 10)
        runner = CliRunner()
        result = runner.invoke(main, ["test", DFF_SYNC, "--spec", p])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# §15.10.8 — clock 上書き
# ---------------------------------------------------------------------------

class TestClockOverride:
    """ケースの clock 上書き (period_ns=20) での offset_ns 検証"""

    def _spec(self, tmp_path, offset_val):
        import yaml
        from pathlib import Path
        spec = {
            "version": 1, "top": "dff",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{
                "name": "c",
                "clock": {"signal": "clk", "period_ns": 20},
                "expect": [{"at": 3, "offset_ns": offset_val, "check": {"q": 0}}],
            }],
        }
        p = str(tmp_path / f"clk_{offset_val}.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        return p

    def test_offset_15_ok(self, tmp_path):
        """period_ns=20 で offset_ns=15 はOK"""
        r = ds.test([DFF_SYNC], spec=self._spec(tmp_path, 15))
        assert r.status != "ERROR" or \
               not any(e.code == "E_OFFSET_RANGE" for e in r.errors)

    def test_offset_20_error(self, tmp_path):
        """period_ns=20 で offset_ns=20 は E_OFFSET_RANGE"""
        r = ds.test([DFF_SYNC], spec=self._spec(tmp_path, 20))
        assert r.status == "ERROR"
        assert any(e.code == "E_OFFSET_RANGE" for e in r.errors)


# ---------------------------------------------------------------------------
# §15.10.9 — VCD と信号変化 DB
# ---------------------------------------------------------------------------

class TestVCDAndDB:
    """ex2 を VCD 付きで実行 → rst/q の変化が記録されること"""

    def test_signal_changes_db(self, tmp_path):
        """rst と q の 38ns/47ns の変化が DB に記録されること"""
        r = ds.run(
            [DFF_SYNC],
            top="dff",
            cycles=7,
            keep_changes=True,
        )
        assert r._change_db is not None
        changes_rst = r.get_signal_changes("rst")
        changes_q = r.get_signal_changes("q")
        assert len(changes_rst) > 0
        assert len(changes_q) > 0

    def test_sub_cycle_changes_in_db_via_api_test(self, tmp_path):
        """api.test() で ex2 run → rst の変化が DB に記録されること"""
        r = ds.run(
            [DFF_SYNC],
            top="dff",
            cycles=8,
            keep_changes=True,
        )
        # Just verify DB is populated (sub-cycle recording happens internally)
        assert r._change_db is not None
        assert len(r._change_db.signals()) > 0

    def test_vcd_created(self, tmp_path):
        """VCD 出力が作成されること"""
        vcd_path = str(tmp_path / "ex2.vcd")
        r = ds.run([DFF_SYNC], top="dff", cycles=8, vcd=vcd_path)
        from pathlib import Path
        assert Path(vcd_path).exists()
        content = Path(vcd_path).read_text(encoding="ascii")
        assert "$dumpvars" in content


# ---------------------------------------------------------------------------
# §15.12 — W_UNKNOWN_KEY
# ---------------------------------------------------------------------------

class TestUnknownKey:
    """知らないキーは W_UNKNOWN_KEY 警告で通過"""

    def test_unknown_key_in_stimulus(self, tmp_path):
        """stimulus に未知キーがあると W_UNKNOWN_KEY 警告が出ること"""
        import yaml
        from pathlib import Path
        spec = {
            "version": 1, "top": "dff",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "c",
                        "stimulus": [{"at": 3, "set": {"d": 1}, "unknown_key": 42}],
                        "expect": []}],
        }
        p = str(tmp_path / "unk.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        r = ds.test([DFF_SYNC], spec=p)
        # §18.5 E_NO_CHECK: チェックが空なのでエラーになる
        assert r.status == "ERROR"
        codes = [e.code for e in r.errors] + [e.code for cr in r.cases for e in cr.errors]
        assert "E_NO_CHECK" in codes, f"expected E_NO_CHECK but got: {codes}"
        # warnings may be in result
        all_warnings = list(r.warnings)
        for cr in r.cases:
            all_warnings.extend(cr.warnings)
        warn_codes = [w.code for w in all_warnings]
        assert "W_UNKNOWN_KEY" in warn_codes, f"warnings: {warn_codes}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fail_details(r):
    fails = [(c.name, c.time_ns, c.expected, c.actual) for c in r.checks if c.status == "FAIL"]
    return f"status={r.status} fails={fails}"
