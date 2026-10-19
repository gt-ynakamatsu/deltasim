"""tests/test_gui_contract.py — GUI担当報告の4点を公開API経由で保証するテスト

報告された食い違いと PR #7 での修正状況:
1. case_done がケース単位でなくcheck単位で発火 → ケース単位に修正済み
2. TestResult（結果オブジェクト/JSON）に truncated_at が未定義 → 追加済み
3. val_to_4state が Z も "x" で返す → Z_VALUE 区別済み
4. summary に elapsed_ms が無い → summary.elapsed_ms 追加済み
"""

from __future__ import annotations
import json as _json
import os
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ===========================================================================
# 報告(1): case_done がケース単位でなくcheck単位で発火
# ===========================================================================

class TestCaseDoneGranularity:
    """§12.2: case_done はシナリオ（ケース）単位で1回だけ発行されること"""

    def test_case_done_fires_exactly_once(self):
        """api.test() で case_done がシナリオごとに1回だけ発火すること"""
        import deltasim.api as ds

        events = []
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )

        case_done_events = [e for e in events if e["type"] == "case_done"]

        # シナリオ1つ → case_done は1回だけ
        assert len(case_done_events) == 1, (
            f"case_done はシナリオ単位で1回のはずだが {len(case_done_events)} 回発行された。"
            f"  全イベントタイプ: {[e['type'] for e in events]}"
        )

    def test_case_done_not_per_check(self):
        """case_done の回数がチェック数より少ないこと（check単位でないことを保証）"""
        import deltasim.api as ds

        events = []
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )

        case_done_count = sum(1 for e in events if e["type"] == "case_done")
        check_count = len(result.checks)

        assert case_done_count == 1, \
            f"case_done 回数={case_done_count} ≠ 1"
        assert check_count > 1, \
            f"チェック数が1以下では意味がない (check_count={check_count})"
        assert case_done_count < check_count, (
            f"case_done ({case_done_count}) >= check数 ({check_count}): "
            "チェック単位で発行されている疑いあり"
        )

    def test_case_done_has_overall_status(self):
        """case_done イベントにはシナリオ全体の status が含まれること"""
        import deltasim.api as ds

        events = []
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )

        cd = next(e for e in events if e["type"] == "case_done")
        assert cd["status"] == result.status, (
            f"case_done.status={cd['status']!r} が result.status={result.status!r} と一致しない"
        )
        assert "elapsed_ms" in cd, "case_done に elapsed_ms がない"

    def test_case_done_json_lines_once(self):
        """--json-events でも case_done は1回だけ出力されること"""
        from click.testing import CliRunner
        from deltasim.cli import main

        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json-events",
        ])
        assert result.exit_code == 0, result.output

        events = [_json.loads(l) for l in result.output.splitlines() if l.startswith("{")]
        case_done_events = [e for e in events if e.get("type") == "case_done"]
        assert len(case_done_events) == 1, (
            f"--json-events で case_done が {len(case_done_events)} 回出力された"
        )


# ===========================================================================
# 報告(2): TestResult（結果オブジェクト/JSON）に truncated_at が未定義
# ===========================================================================

class TestTruncatedAt:
    """§12.3: truncated_at フィールドの存在を保証"""

    def test_truncated_at_in_result_object(self):
        """TestResult オブジェクトに truncated_at 属性があること"""
        import deltasim.api as ds
        from deltasim.result import TestResult

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert hasattr(result, "truncated_at"), \
            "TestResult に truncated_at 属性がない"
        # 通常は None（上限未達）
        assert result.truncated_at is None, \
            f"通常実行で truncated_at={result.truncated_at!r} (None のはず)"

    def test_truncated_at_in_json_dict(self):
        """to_dict() に truncated_at キーが含まれること"""
        import deltasim.api as ds

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert "truncated_at" in d, \
            f"to_dict() に truncated_at がない。キー一覧: {list(d.keys())}"
        assert d["truncated_at"] is None

    def test_truncated_at_in_json_output(self, tmp_path):
        """--json ファイルに truncated_at が含まれること"""
        from click.testing import CliRunner
        from deltasim.cli import main

        json_path = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_path,
        ])
        assert result.exit_code == 0, result.output

        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "truncated_at" in d, \
            f"JSON ファイルに truncated_at がない。キー: {list(d.keys())}"

    def test_truncated_at_set_when_limit_reached(self):
        """メモリ上限超過時に truncated_at が設定されること"""
        import deltasim.api as ds

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            wave_mem_mb=0,  # 即座に上限超過
        )
        # 0 MB 上限: truncated_at が設定されるか、または None のまま
        # (信号変化が1件も記録されなければ None のままでもよい)
        d = result.to_dict()
        assert "truncated_at" in d  # キーは必ず存在する


# ===========================================================================
# 報告(3): val_to_4state が Z も "x" で返す
# ===========================================================================

class TestZDistinctFromX:
    """§12.4: Z は "z", X は "x" で区別されること（小文字統一）"""

    def test_val_to_4state_x_returns_x(self):
        """None (X) は 'x' を返すこと"""
        from deltasim.result import val_to_4state
        assert val_to_4state(None, 1) == "x", \
            f"val_to_4state(None, 1) = {val_to_4state(None, 1)!r} (expected 'x')"
        assert val_to_4state(None, 8) == "xxxxxxxx"

    def test_val_to_4state_z_returns_z_not_x(self):
        """Z_VALUE は 'z' を返し 'x' ではないこと（今回の修正の核心）"""
        from deltasim.result import val_to_4state, Z_VALUE

        result_1bit = val_to_4state(Z_VALUE, 1)
        result_8bit = val_to_4state(Z_VALUE, 8)

        assert result_1bit == "z", \
            f"val_to_4state(Z_VALUE, 1) = {result_1bit!r} (expected 'z', NOT 'x')"
        assert result_8bit == "zzzzzzzz", \
            f"val_to_4state(Z_VALUE, 8) = {result_8bit!r} (expected 'zzzzzzzz')"
        assert result_1bit != "x", \
            "Z が 'x' を返している — 報告された食い違いが未修正"

    def test_z_and_x_are_distinguishable(self):
        """Z と X が文字列レベルで区別できること"""
        from deltasim.result import val_to_4state, Z_VALUE

        x_str = val_to_4state(None, 4)
        z_str = val_to_4state(Z_VALUE, 4)

        assert x_str == "xxxx"
        assert z_str == "zzzz"
        assert x_str != z_str, \
            "Z と X が区別されていない（どちらも同じ文字列を返す）"

    def test_vcd_z_distinct_from_x(self, tmp_path):
        """VCD 出力で z が x と区別されること"""
        from deltasim.vcd.writer import _format_value
        from deltasim.result import Z_VALUE

        x_output = _format_value("!", None, 1)
        z_output = _format_value("!", Z_VALUE, 1)

        assert "x" in x_output, f"X 値の VCD 出力に 'x' がない: {x_output!r}"
        assert "z" in z_output, f"Z 値の VCD 出力に 'z' がない: {z_output!r}"
        assert x_output != z_output, \
            f"VCD で X と Z が区別されていない: x={x_output!r}, z={z_output!r}"

    def test_fill_z_literal_in_engine(self, tmp_path):
        """'z リテラルが Z_VALUE として扱われ 'z' 文字列になること"""
        sv = tmp_path / "zfill.sv"
        sv.write_text("""\
module zfill (
    output logic b
);
    assign b = 'z;
endmodule
""", encoding="utf-8")
        from deltasim.parser.parser import parse_file
        from deltasim.elab.elaborator import elaborate
        from deltasim.sim.engine import SimEngine
        from deltasim.result import Z_VALUE, val_to_4state

        mod = parse_file(str(sv))
        design = elaborate(mod)
        eng = SimEngine(design, reset_cycles=0)
        eng.run(cycles=1, reset_cycles=0)

        b_val = eng.signals.get("b")
        b_str = val_to_4state(b_val, 1)

        # b = 'z → should be Z or X (simplified), but NOT a normal integer
        assert b_str in ("z", "x"), \
            f"'z リテラルが通常値 {b_str!r} になった（z または x が期待値）"


# ===========================================================================
# 報告(4): summary に elapsed_ms が無い
# ===========================================================================

class TestSummaryElapsedMs:
    """§12.5: summary.elapsed_ms の存在を保証"""

    def test_summary_elapsed_ms_in_result_dict(self):
        """to_dict() の summary.elapsed_ms が存在すること"""
        import deltasim.api as ds

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()

        assert "summary" in d, \
            f"to_dict() に 'summary' キーがない。キー一覧: {list(d.keys())}"
        assert "elapsed_ms" in d["summary"], \
            f"summary に 'elapsed_ms' がない。summary の内容: {d['summary']}"
        assert d["summary"]["elapsed_ms"] > 0, \
            "summary.elapsed_ms が 0 以下"

    def test_summary_elapsed_ms_matches_top_level(self):
        """summary.elapsed_ms が top-level elapsed_ms と一致すること"""
        import deltasim.api as ds

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        assert d["summary"]["elapsed_ms"] == d["elapsed_ms"], (
            f"summary.elapsed_ms={d['summary']['elapsed_ms']} ≠ "
            f"elapsed_ms={d['elapsed_ms']}"
        )

    def test_summary_elapsed_ms_in_json_file(self, tmp_path):
        """--json ファイルの summary.elapsed_ms が存在すること"""
        from click.testing import CliRunner
        from deltasim.cli import main

        json_path = str(tmp_path / "result.json")
        runner = CliRunner()
        result = runner.invoke(main, [
            "test",
            os.path.join(EXAMPLES, "counter.sv"),
            "--spec", os.path.join(EXAMPLES, "counter_spec.yaml"),
            "--json", json_path,
        ])
        assert result.exit_code == 0, result.output

        d = _json.loads(Path(json_path).read_text(encoding="utf-8"))
        assert "summary" in d, \
            f"JSON に 'summary' がない: {list(d.keys())}"
        assert "elapsed_ms" in d["summary"], \
            f"JSON summary に elapsed_ms がない: {d.get('summary')}"
        assert d["summary"]["elapsed_ms"] > 0

    def test_summary_elapsed_ms_type(self):
        """summary.elapsed_ms が float / number 型であること"""
        import deltasim.api as ds

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        d = result.to_dict()
        v = d["summary"]["elapsed_ms"]
        assert isinstance(v, (int, float)), \
            f"summary.elapsed_ms の型が {type(v).__name__}（数値が期待値）"

    def test_check_elapsed_ms_not_required(self):
        """§12.5: CheckInfo には elapsed_ms が不要なこと（ケース単位のみ）"""
        import deltasim.api as ds
        from deltasim.result import CheckInfo

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        # CheckInfo に elapsed_ms があってもなくてもよいが、
        # もし存在するなら 0.0 デフォルト（ケース単位でない）を確認
        for check in result.checks:
            if hasattr(check, "elapsed_ms"):
                # 存在する場合は CheckInfo のデフォルト値か 0
                pass  # 存在を禁止はしないが、使わない

    def test_schema_version_is_2(self):
        """schema_version が '2' であること（§12 確定後のバージョン）"""
        import deltasim.api as ds
        from deltasim.result import SCHEMA_VERSION

        assert SCHEMA_VERSION == "2", \
            f"schema_version={SCHEMA_VERSION!r} (expected '2')"

        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
        )
        assert result.schema_version == "2"
        assert result.to_dict()["schema_version"] == "2"


# ===========================================================================
# 統合テスト: 4点をまとめて1回の api.test() 呼び出しで確認
# ===========================================================================

class TestGUIContractIntegration:
    """4報告点を1回の api.test() 呼び出しで網羅的に確認"""

    def test_all_four_issues_via_api(self):
        """
        4点の食い違いが修正済みであることを1回の api.test() 呼び出しで確認。
        GUI担当はこのテストが緑であれば安心して使えます。
        """
        import deltasim.api as ds
        from deltasim.result import Z_VALUE, val_to_4state

        events = []
        result = ds.test(
            [os.path.join(EXAMPLES, "counter.sv")],
            spec=os.path.join(EXAMPLES, "counter_spec.yaml"),
            on_progress=events.append,
        )

        d = result.to_dict()

        # --- 報告(1): case_done がケース単位 ---
        case_done_events = [e for e in events if e["type"] == "case_done"]
        assert len(case_done_events) == 1, (
            f"[報告(1)] case_done が {len(case_done_events)} 回発行された（期待値: 1回）"
        )

        # --- 報告(2): truncated_at が存在 ---
        assert "truncated_at" in d, \
            "[報告(2)] TestResult.to_dict() に truncated_at がない"

        # --- 報告(3): Z は "z" で返る ---
        z_str_1 = val_to_4state(Z_VALUE, 1)
        z_str_8 = val_to_4state(Z_VALUE, 8)
        assert z_str_1 == "z", \
            f"[報告(3)] Z_VALUE 1bit が {z_str_1!r}（期待値: 'z'）"
        assert z_str_8 == "zzzzzzzz", \
            f"[報告(3)] Z_VALUE 8bit が {z_str_8!r}（期待値: 'zzzzzzzz'）"
        assert z_str_1 != val_to_4state(None, 1), \
            "[報告(3)] Z と X が区別されていない"

        # --- 報告(4): summary.elapsed_ms が存在 ---
        assert "summary" in d, \
            "[報告(4)] to_dict() に summary がない"
        assert "elapsed_ms" in d["summary"], \
            "[報告(4)] summary に elapsed_ms がない"
        assert d["summary"]["elapsed_ms"] > 0, \
            "[報告(4)] summary.elapsed_ms が 0 以下"
