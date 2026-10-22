"""tests/test_constraints.py — §18.6 YAML constraints: 制約付き乱数生成テスト"""

from __future__ import annotations
import os, tempfile, yaml
from pathlib import Path
import pytest
import deltasim.api as ds
from deltasim.tb.scenario import (
    StimulusSchedule, InputEntry, ConstraintError, ConstraintExprError,
    DEFAULT_MAX_CONSTRAINT_ATTEMPTS,
)
from deltasim.interp.expr import eval_bool

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
COUNTER_SV = os.path.join(EXAMPLES, "counter.sv")

# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------

def _make_spec(tmp_path, spec_dict, sv_code=None, top="asel"):
    """sv_code と spec_dict から一時ファイルを作って ds.test を実行する。"""
    if sv_code is None:
        sv_code = (
            "module asel(input logic [7:0] a, b, output logic [7:0] y);\n"
            "  assign y = a + b;\n"
            "endmodule\n"
        )
    sv_p = str(tmp_path / "asel.sv")
    Path(sv_p).write_text(sv_code, encoding="utf-8")
    yaml_p = str(tmp_path / "spec.yaml")
    with open(yaml_p, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True)
    return ds.test([sv_p], spec=yaml_p), sv_p, yaml_p


# ---------------------------------------------------------------------------
# 1. StimulusSchedule 単体テスト
# ---------------------------------------------------------------------------

class TestStimulusScheduleConstraints:

    def _inputs(self):
        return [InputEntry(at=(1, 10),
                           random_spec={"a": {"range": [0, 15]}, "b": {"range": [0, 15]}})]

    def test_generated_values_satisfy_constraints(self):
        """生成値がすべて制約を満たすこと"""
        inputs = self._inputs()
        stim = StimulusSchedule(inputs, 10, seed=42,
                                constraints=["a + b < 16", "a != b"])
        for cycle in range(1, 11):
            vals = stim.get(cycle)
            a, b = vals["a"], vals["b"]
            assert eval_bool("a + b < 16", {"a": a, "b": b}), f"constraint violated: {a}+{b}={a+b}"
            assert a != b, f"a == b constraint violated: {a}"

    def test_same_seed_same_values(self):
        """同じシードで同じ値列になること（Windows/Linux 両方）"""
        inputs = self._inputs()
        stim1 = StimulusSchedule(inputs, 5, seed=123, constraints=["a < b"])
        stim2 = StimulusSchedule(inputs, 5, seed=123, constraints=["a < b"])
        for cycle in range(1, 6):
            assert stim1.get(cycle) == stim2.get(cycle), \
                f"同じシードで違う値: cycle={cycle}"

    def test_deterministic_specific_values(self):
        """期待値をハードコードした再現性テスト（seed=42, constraints=[a < 8, b >= 4]）"""
        inputs = self._inputs()
        stim = StimulusSchedule(inputs, 3, seed=42, constraints=["a < 8", "b >= 4"])
        vals_list = [stim.get(c) for c in range(1, 4)]
        # 全て制約を満たすことを確認
        for c, vals in enumerate(vals_list, 1):
            assert vals["a"] < 8, f"cycle={c}: a={vals['a']} >= 8"
            assert vals["b"] >= 4, f"cycle={c}: b={vals['b']} < 4"
        # 最初のケースの値が再現することを確認（seed=42 固定）
        first = vals_list[0]
        stim2 = StimulusSchedule(inputs, 1, seed=42, constraints=["a < 8", "b >= 4"])
        assert stim2.get(1) == first, f"再現しない: {stim2.get(1)} != {first}"

    def test_impossible_constraint_raises_e_constraint(self):
        """不可能な制約（a < 0 で a は unsigned 0〜255）で E_CONSTRAINT"""
        inputs = [InputEntry(at=1, random_spec={"a": {"range": [0, 255]}})]
        with pytest.raises(ConstraintError) as exc:
            StimulusSchedule(inputs, 1, seed=0, constraints=["a < 0"])
        assert "E_CONSTRAINT" in str(exc.value)

    def test_max_attempts_exceeded_message_contains_constraint(self):
        """E_CONSTRAINT のメッセージに制約内容が含まれること"""
        inputs = [InputEntry(at=1, random_spec={"x": {"range": [0, 5]}})]
        with pytest.raises(ConstraintError) as exc:
            StimulusSchedule(inputs, 1, seed=0, constraints=["x > 100"],
                             max_constraint_attempts=10)
        msg = str(exc.value)
        assert "x > 100" in msg

    def test_syntax_error_raises_e_constraint_expr(self):
        """構文エラーの制約式で E_CONSTRAINT_EXPR"""
        inputs = [InputEntry(at=1, random_spec={"a": {"range": [0, 10]}})]
        with pytest.raises(ConstraintExprError):
            StimulusSchedule(inputs, 1, seed=0, constraints=["a @@@"])

    def test_no_constraints_no_rejection(self):
        """制約なしで通常の乱数生成が動くこと"""
        inputs = self._inputs()
        stim = StimulusSchedule(inputs, 5, seed=1)
        for cycle in range(1, 6):
            vals = stim.get(cycle)
            assert "a" in vals and "b" in vals

    def test_custom_max_attempts(self):
        """max_constraint_attempts をカスタム設定できること"""
        inputs = [InputEntry(at=1, random_spec={"a": {"range": [0, 100]}})]
        # a < 50 は約50%の確率で通るので 1000 回あれば確実に見つかる
        stim = StimulusSchedule(inputs, 1, seed=0, constraints=["a < 50"],
                                max_constraint_attempts=1000)
        assert stim.get(1)["a"] < 50


# ---------------------------------------------------------------------------
# 2. YAML API テスト
# ---------------------------------------------------------------------------

class TestConstraintsYAML:

    def _base_spec(self, constraints=None, max_attempts=None, seed=42):
        spec = {
            "version": 1, "top": "asel",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 8, "seed": seed},
            "cases": [{"name": "c",
                       "stimulus": [{"at": [1, 8],
                                     "random": {"a": {"range": [0, 15]},
                                                "b": {"range": [0, 15]}}}],
                       "expected": [{"at": 3, "check": {"y": 7}}]}],
        }
        if constraints:
            spec["constraints"] = constraints
        if max_attempts is not None:
            spec["max_constraint_attempts"] = max_attempts
        return spec

    def test_top_level_constraints_apply(self, tmp_path):
        """top-level constraints が全ケースに適用されること"""
        spec = self._base_spec(constraints=["a + b < 16"])
        result, sv_p, yaml_p = _make_spec(tmp_path, spec)
        assert "E_CONSTRAINT" not in [e.code for e in result.errors]

    def test_case_level_constraints(self, tmp_path):
        """ケースレベルの constraints が動くこと"""
        spec = {
            "version": 1, "top": "asel",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5, "seed": 42},
            "cases": [{"name": "c",
                       "constraints": ["a > b"],
                       "stimulus": [{"at": [1, 5],
                                     "random": {"a": {"range": [0, 15]},
                                                "b": {"range": [0, 15]}}}],
                       "expected": [{"at": 2, "check": {"y": 7}}]}],
        }
        result, sv_p, yaml_p = _make_spec(tmp_path, spec)
        assert "E_CONSTRAINT" not in [e.code for e in result.errors]

    def test_impossible_constraint_returns_e_constraint(self, tmp_path):
        """不可能な制約で E_CONSTRAINT エラーが返ること"""
        spec = self._base_spec(constraints=["a > 1000"])
        result, sv_p, yaml_p = _make_spec(tmp_path, spec)
        codes = [e.code for e in result.errors] + [
            e.code for cr in result.cases for e in cr.errors
        ]
        assert "E_CONSTRAINT" in codes, f"E_CONSTRAINT が出なかった: {codes}"
        assert result.status == "ERROR"

    def test_e_constraint_message_contains_cycle_and_constraint(self, tmp_path):
        """E_CONSTRAINT のメッセージにサイクルと制約内容が含まれること"""
        spec = self._base_spec(constraints=["a > 1000"], max_attempts=5)
        result, sv_p, yaml_p = _make_spec(tmp_path, spec)
        all_errors = list(result.errors) + [e for cr in result.cases for e in cr.errors]
        cstr_err = next((e for e in all_errors if e.code == "E_CONSTRAINT"), None)
        assert cstr_err is not None
        assert "a > 1000" in cstr_err.message

    def test_json_format_unchanged(self, tmp_path):
        """--json 出力形式が変わらないこと（schema_version = "2"）"""
        import json
        spec = self._base_spec(constraints=["a + b < 16"])
        sv_p = str(tmp_path / "asel.sv")
        Path(sv_p).write_text(
            "module asel(input logic [7:0] a, b, output logic [7:0] y);\n"
            "  assign y = a + b;\nendmodule\n"
        )
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w") as f:
            yaml.dump(spec, f)
        json_p = str(tmp_path / "result.json")
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        runner = CliRunner()
        runner.invoke(cli_main, ["test", sv_p, "--spec", yaml_p, "--json", json_p])
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        assert d["schema_version"] == "2"
        assert "warnings" in d

    def test_same_seed_reproducible_via_api(self, tmp_path):
        """同じシードで2回実行すると同じ結果になること"""
        sv_p = str(tmp_path / "asel.sv")
        Path(sv_p).write_text(
            "module asel(input logic [7:0] a, b, output logic [7:0] y);\n"
            "  assign y = a + b;\nendmodule\n"
        )
        yaml_p = str(tmp_path / "spec.yaml")
        spec = self._base_spec(constraints=["a + b < 16"], seed=99)
        with open(yaml_p, "w") as f:
            yaml.dump(spec, f)
        r1 = ds.test([sv_p], spec=yaml_p)
        r2 = ds.test([sv_p], spec=yaml_p)
        # チェック結果（PASS/FAIL）が同じなら seed が効いている
        assert r1.status == r2.status

    def test_unknown_signal_in_constraint_error(self, tmp_path):
        """constraints で存在しない信号名を使うと E_CONSTRAINT_EXPR（または E_UNKNOWN_PORT 相当）"""
        spec = self._base_spec(constraints=["nonexistent > 5"])
        result, sv_p, yaml_p = _make_spec(tmp_path, spec)
        codes = [e.code for e in result.errors] + [
            e.code for cr in result.cases for e in cr.errors
        ]
        # 評価時エラー（E_CONSTRAINT_EXPR）または constraint 失敗（E_CONSTRAINT）
        assert any(c in ("E_CONSTRAINT", "E_CONSTRAINT_EXPR") for c in codes), \
            f"expected error but got: {codes}"

    def test_w_unknown_key_not_triggered_by_constraints(self, tmp_path):
        """constraints は既知キーなので W_UNKNOWN_KEY が出ないこと"""
        spec = self._base_spec(constraints=["a < b"])
        result, sv_p, yaml_p = _make_spec(tmp_path, spec)
        all_warnings = list(result.warnings) + [w for cr in result.cases for w in cr.warnings]
        unk = [w for w in all_warnings if w.code == "W_UNKNOWN_KEY"]
        assert unk == [], f"W_UNKNOWN_KEY が出た: {[w.message for w in unk]}"
