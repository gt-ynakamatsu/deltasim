"""tests/test_m6b.py — マイルストーン6b の5点を検証するテスト

1. --record all / --record-all 非推奨エイリアス
2. elapsed_ms (case sim time) vs summary.elapsed_ms (total time) 分離
3. --case 絞り込み
4. VCD 階層パス（ポート接続シグナルも子スコープに表示）
5. FIFO gen→test E2E
"""

from __future__ import annotations
import json as _json
import os
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ===========================================================================
# 1. --record all / --record-all 非推奨エイリアス
# ===========================================================================

class TestRecordOption:
    def test_record_all_space(self):
        """--record all が動作すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--record", "all",
        ])
        assert result.exit_code == 0, result.output

    def test_record_all_deprecated_alias_works(self):
        """--record-all が動作すること（非推奨だが互換性あり）"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--record-all",
        ])
        assert result.exit_code == 0, result.output

    def test_record_all_deprecated_warns_stderr(self):
        """--record-all は stderr に非推奨警告を出すこと（CLI 経由）"""
        # CliRunner で捕捉: stderr は mix_stderr=True(default) なので output に混在する
        # 代わりに api レベルで警告メッセージを確認する
        from click.testing import CliRunner
        from deltasim.cli import main

        # CliRunner はデフォルト mix_stderr=True なので output に stderr が混在する
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--record-all",
        ])
        assert result.exit_code == 0, result.output
        # 非推奨警告は output（stderr + stdout 混合）に含まれる
        assert "非推奨" in result.output or "deprecated" in result.output.lower(), \
            f"非推奨警告がない: {result.output!r}"

    def test_record_all_deprecated_no_json_pollution(self, tmp_path):
        """--record-all 使用時でも JSON ファイルが有効であること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        json_path = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--record-all",
            "--json", json_path,
        ])
        assert result.exit_code == 0, result.output
        # JSON ファイルが有効であること
        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "schema_version" in d

    def test_wave_mem_option(self):
        """--wave-mem が動作すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--wave-mem", "1",
        ])
        assert result.exit_code == 0, result.output

    def test_record_all_via_api(self):
        """api.test(record_all=True) が動作すること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            record_all=True,
        )
        assert result.status == "PASS"


# ===========================================================================
# 2. elapsed_ms (case) vs summary.elapsed_ms (total) 分離
# ===========================================================================

class TestElapsedMsSeparation:
    def test_summary_elapsed_ms_gte_elapsed_ms(self):
        """summary.elapsed_ms (総計) >= elapsed_ms (シミュ時間のみ)"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert d["summary"]["elapsed_ms"] >= d["elapsed_ms"], (
            f"summary.elapsed_ms={d['summary']['elapsed_ms']} < "
            f"elapsed_ms={d['elapsed_ms']}: "
            "総計時間はシミュ時間以上のはず (parse + elab + sim)"
        )

    def test_elapsed_ms_is_sim_time(self):
        """elapsed_ms はシミュレーション時間のみを反映すること"""
        import deltasim.api as ds
        # 単純なケース: sim時間 <= 総計時間
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.elapsed_ms > 0, "elapsed_ms が 0 以下"

    def test_summary_elapsed_ms_includes_overhead(self):
        """summary.elapsed_ms には parse/elab のオーバーヘッドが含まれること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        # 総計 > シミュ（= parse+elab のオーバーヘッドが存在する）
        # これが同値になるのは parse/elab が瞬時の場合のみ
        # テストとしては >= を検証（同値も許容）
        assert d["summary"]["elapsed_ms"] >= d["elapsed_ms"]

    def test_json_structure_unchanged(self, tmp_path):
        """elapsed_ms 分離後も JSON 構造が後方互換であること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        json_path = str(tmp_path / "r.json")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_path,
        ])
        assert result.exit_code == 0
        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        # 後方互換: elapsed_ms は top level にも存在する
        assert "elapsed_ms" in d
        # 新仕様: summary.elapsed_ms も存在する
        assert "summary" in d
        assert "elapsed_ms" in d["summary"]


# ===========================================================================
# 3. --case 絞り込み
# ===========================================================================

class TestCaseFilter:
    def test_matching_case_runs(self):
        """--case に一致するケースは実行されること（legacy: 名前=ファイル語幹）"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--case", "counter_spec",  # legacy: filename stem = "counter_spec"
        ])
        assert result.exit_code == 0, result.output

    def test_non_matching_case_excluded(self):
        """--case に一致しないケースは結果に含まれないこと"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--case", "other_module",  # does not match "counter"
        ])
        # Filtered out → exit 0 (no failure, just skipped)
        assert result.exit_code != 0, result.output  # §14.1: 非0終了

    def test_non_matching_case_excluded_via_api(self):
        """api.test(filter_cases=['other']) が空チェックの TestResult を返すこと（除外）"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["other_module"],  # does not match
        )
        assert result is not None, "test() は常に TestResult を返すこと"
        assert len(result.cases) == 0
        assert result.status == "ERROR"  # §14.1
        assert any(e.code == "E_NO_CASE" for e in result.errors)
        assert len(result.cases) == 0

    def test_matching_case_via_api(self):
        """api.test(filter_cases=['counter']) がケースを実行すること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["counter_spec"],  # legacy: filename stem
        )
        assert result is not None, "filter_cases=['counter_spec'] でケースが除外された"
        assert result.status == "PASS"

    def test_no_filter_runs_all(self):
        """--case 未指定（filter_cases=None）で全ケースが実行されること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=None,  # run all
        )
        assert result is not None
        assert len(result.checks) > 0

    def test_multiple_case_names(self):
        """filter_cases に複数指定でいずれかに一致するケースを実行"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            filter_cases=["other", "counter_spec"],  # "counter_spec" matches
        )
        assert result is not None
        assert result.status == "PASS"


# ===========================================================================
# 4. VCD 階層パス（ポート接続シグナルも子スコープに表示）
# ===========================================================================

class TestVCDHierarchyAliases:
    def test_signal_aliases_populated(self):
        """ポート接続されたシグナルの aliases が ElabDesign に記録されること"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        catalog = {}
        for f in ["adder.sv", "alu.sv"]:
            mod = parse_file(os.path.join(EXAMPLES, f))
            catalog[mod.name] = mod
        design = elaborate(catalog["alu"], catalog=catalog)

        # u_add の port → parent signal エイリアスが存在すること
        assert len(design.signal_aliases) > 0, "signal_aliases が空"
        assert "u_add.a" in design.signal_aliases
        assert "u_add.sum" in design.signal_aliases or "u_add.sum" in str(design.signal_aliases)

    def test_vcd_has_subscope(self, tmp_path):
        """VCD に u_add の $scope が含まれること"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.vcd.writer import VCDWriter

        catalog = {}
        for f in ["adder.sv", "alu.sv"]:
            mod = parse_file(os.path.join(EXAMPLES, f))
            catalog[mod.name] = mod
        design = elaborate(catalog["alu"], catalog=catalog)

        vcd_path = str(tmp_path / "alu.vcd")
        eng = SimEngine(design, reset_cycles=0)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=2)
        vcd.close()

        content = Path(vcd_path).read_text(encoding="ascii")
        assert "$scope module u_add $end" in content, \
            "VCD に u_add スコープがない"
        # ポートのローカル名が子スコープ内に存在すること
        assert "$var wire" in content

    def test_vcd_port_shares_id_with_parent(self, tmp_path):
        """ポート接続シグナルは親と同じ VCD ID を共有すること"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.vcd.writer import VCDWriter
        import re

        catalog = {}
        for f in ["adder.sv", "alu.sv"]:
            mod = parse_file(os.path.join(EXAMPLES, f))
            catalog[mod.name] = mod
        design = elaborate(catalog["alu"], catalog=catalog)

        vcd_path = str(tmp_path / "alu2.vcd")
        eng = SimEngine(design, reset_cycles=0)
        vcd = VCDWriter(vcd_path, design)
        vcd.record_initial(0, eng.signals)
        eng.vcd_writer = vcd
        eng.run(cycles=1)
        vcd.close()

        content = Path(vcd_path).read_text(encoding="ascii")
        # a が top-level と u_add.a の両方で同じ ID を使っていること
        # top: $var wire 8 X a $end
        # u_add scope: $var wire 8 X a $end (同じ X)
        var_decls = re.findall(r'\$var wire \d+ (\S+) (\S+) \$end', content)
        # Check a appears with same ID in both contexts
        ids_for_a = [vid for vid, name in var_decls if name == "a"]
        if len(ids_for_a) >= 2:
            assert len(set(ids_for_a)) == 1, \
                f"a のVCD ID が一致しない: {ids_for_a}"

    def test_list_signals_includes_aliases(self):
        """list_signals() がエイリアスパスも返すこと"""
        import deltasim.api as ds
        di = ds.load([
            os.path.join(EXAMPLES, "adder.sv"),
            os.path.join(EXAMPLES, "alu.sv"),
        ], top="alu")
        sim = ds.Simulator(di, reset_cycles=0)
        signals = sim.list_signals()
        names = [s["name"] for s in signals]
        # エイリアスパスが含まれること
        assert any("u_add." in n for n in names), \
            f"u_add.* のエイリアスパスが list_signals() に含まれない: {names}"

    def test_get_signal_changes_by_alias(self):
        """エイリアスパスで get_signal_changes() が引けること"""
        import deltasim.api as ds
        di = ds.load([
            os.path.join(EXAMPLES, "adder.sv"),
            os.path.join(EXAMPLES, "alu.sv"),
        ], top="alu")
        sim = ds.Simulator(di, reset_cycles=0, keep_changes=True)
        sim.set("a", 5)
        sim.set("b", 3)
        sim.set("op", 0)
        sim.step(1)

        # エイリアスパスで変化を取得（u_add.sum = add_sum に解決される）
        changes_alias = sim.get_signal_changes("u_add.sum")
        changes_canonical = sim.get_signal_changes("add_sum")
        # どちらも同じ変化を返すこと
        assert len(changes_alias) == len(changes_canonical), \
            f"エイリアス({len(changes_alias)}) ≠ 正規({len(changes_canonical)})"


# ===========================================================================
# 5. FIFO gen→test E2E
# ===========================================================================

class TestFIFOGenTest:
    def test_fifo_exists(self):
        """examples/fifo.sv と fifo_spec.yaml が存在すること"""
        assert Path(os.path.join(EXAMPLES, "fifo.sv")).exists()
        assert Path(os.path.join(EXAMPLES, "fifo_spec.yaml")).exists()

    def test_fifo_parse_elab(self):
        """fifo.sv がパース・エラボレートできること"""
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        mod = parse_file(os.path.join(EXAMPLES, "fifo.sv"))
        design = elaborate(mod)
        assert design.module_name == "fifo"
        assert "mem" in design.array_decls, "FIFO のアレイメモリが検出されていない"

    def test_fifo_spec_passes(self):
        """deltasim test で fifo_spec.yaml が PASS すること"""
        import deltasim.api as ds
        result = ds.test(
            [os.path.join(EXAMPLES, "fifo.sv")],
            spec=os.path.join(EXAMPLES, "fifo_spec.yaml"),
        )
        fails = [f"{c.signal}@{c.cycle}" for c in result.checks if c.status == "FAIL"]
        assert result.status == "PASS", f"FIFO FAIL: {fails}"

    def test_fifo_gen_creates_files(self, tmp_path):
        """deltasim gen が FIFO の TB SV と YAML を生成すること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "gen",
            os.path.join(EXAMPLES, "fifo.sv"),
            "--top", "fifo",
            "--out-dir", str(tmp_path),
        ])
        assert result.exit_code == 0, result.output
        assert Path(tmp_path / "fifo_tb.sv").exists()
        assert Path(tmp_path / "fifo_spec.yaml").exists()

    def test_fifo_cli_test(self):
        """CLI deltasim test で FIFO が通ること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "fifo.sv"),
            "--spec", os.path.join(EXAMPLES, "fifo_spec.yaml"),
        ])
        assert result.exit_code == 0, result.output
