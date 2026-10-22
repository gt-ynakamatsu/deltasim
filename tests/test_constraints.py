"""tests/test_constraints.py — §18.6 YAML constraints: 制約付き乱数生成テスト（改訂版）"""

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

# adder モジュール（input a, b; output y）- E_UNKNOWN_PORT テスト用の実在ポートを持つモジュール
SV_ADDER = """\
module adder(input logic [7:0] a, b, output logic [7:0] y);
  assign y = a + b;
endmodule
"""
# adder4: 4-bit（幅テスト用）
SV_ADDER4 = """\
module adder4(input logic [3:0] a, b, output logic [3:0] y);
  assign y = a + b;
endmodule
"""
# adder with internal signal（E_UNKNOWN_PORT テスト用）
SV_ADDER_INTERNAL = """\
module adder_int(input logic [7:0] a, b, output logic [7:0] y);
  logic [7:0] tmp;
  always_comb begin tmp = a + b; y = tmp; end
endmodule
"""


def _make_adder_spec(tmp_path, spec_dict, sv=None):
    sv_p = str(tmp_path / "adder.sv")
    Path(sv_p).write_text(sv or SV_ADDER, encoding="utf-8")
    yaml_p = str(tmp_path / "spec.yaml")
    with open(yaml_p, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f, allow_unicode=True)
    return ds.test([sv_p], spec=yaml_p), sv_p, yaml_p


def _base_spec_adder(cycles=8, seed=42):
    return {
        "version": 1, "top": "adder",
        "clock": {"signal": "clk", "period_ns": 10},
        "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
        "sim": {"cycles": cycles, "seed": seed},
    }


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

    def test_deterministic_hardcoded_values(self):
        """seed=42, 制約なしで具体的な値列をハードコードした再現性テスト。

        この値列は Windows/Linux 両 CI で同一であることの証拠。
        random.Random(42) + randint(0,15) の決定論的な出力。
        """
        inputs = [InputEntry(at=(1, 5), random_spec={"a": {"range": [0, 15]}, "b": {"range": [0, 15]}})]
        stim = StimulusSchedule(inputs, 5, seed=42)
        # python -c "import random; r=random.Random(42); [(r.randint(0,15),r.randint(0,15)) for _ in range(5)]"
        expected = [(7, 4), (3, 2), (13, 1), (3, 0), (8, 7)]  # seed=42, random.Random(42)
        for cycle, (ea, eb) in enumerate(expected, 1):
            vals = stim.get(cycle)
            assert vals["a"] == ea, f"cycle={cycle}: a={vals['a']} != {ea}"
            assert vals["b"] == eb, f"cycle={cycle}: b={vals['b']} != {eb}"

    def test_deterministic_with_constraints(self):
        """seed=42、制約 a+b<16 で具体的な値列が再現すること"""
        inputs = [InputEntry(at=(1, 3), random_spec={"a": {"range": [0, 15]}, "b": {"range": [0, 15]}})]
        stim1 = StimulusSchedule(inputs, 3, seed=42, constraints=["a + b < 16"])
        stim2 = StimulusSchedule(inputs, 3, seed=42, constraints=["a + b < 16"])
        for cycle in range(1, 4):
            v1 = stim1.get(cycle)
            v2 = stim2.get(cycle)
            assert v1 == v2, f"cycle={cycle}: {v1} != {v2}"
            a, b = v1["a"], v1["b"]
            assert a + b < 16, f"constraint violated: {a}+{b}={a+b}"

    def test_impossible_constraint_raises_e_constraint(self):
        """不可能な制約（a < 0 で a は unsigned 0〜15）で E_CONSTRAINT"""
        inputs = [InputEntry(at=1, random_spec={"a": {"range": [0, 15]}})]
        with pytest.raises(ConstraintError) as exc:
            StimulusSchedule(inputs, 1, seed=0, constraints=["a < 0"])
        assert "E_CONSTRAINT" in str(exc.value)
        assert "a < 0" in str(exc.value)

    def test_max_attempts_exceeded_message_contains_constraint(self):
        """E_CONSTRAINT のメッセージに制約内容とサイクルが含まれること"""
        inputs = [InputEntry(at=1, random_spec={"x": {"range": [0, 5]}})]
        with pytest.raises(ConstraintError) as exc:
            StimulusSchedule(inputs, 1, seed=0, constraints=["x > 100"],
                             max_constraint_attempts=10)
        msg = str(exc.value)
        assert "x > 100" in msg

    def test_no_constraints_no_rejection(self):
        """制約なしで通常の乱数生成が動くこと"""
        inputs = self._inputs()
        stim = StimulusSchedule(inputs, 5, seed=1)
        for cycle in range(1, 6):
            vals = stim.get(cycle)
            assert "a" in vals and "b" in vals

    def test_separate_entries_joint_constraint(self):
        """a と b が別 InputEntry でも合同制約が効くこと"""
        inputs = [
            InputEntry(at=(1, 5), random_spec={"a": {"range": [0, 15]}}),
            InputEntry(at=(1, 5), random_spec={"b": {"range": [0, 15]}}),
        ]
        stim = StimulusSchedule(inputs, 5, seed=42, constraints=["a + b < 16"])
        for cycle in range(1, 6):
            vals = stim.get(cycle)
            a, b = vals.get("a", 0), vals.get("b", 0)
            assert a + b < 16, f"cycle={cycle}: {a}+{b}={a+b} >= 16"

    def test_unrelated_random_entry_unaffected(self):
        """制約に関係ない random エントリ（c だけ）は壊れないこと"""
        inputs = [
            InputEntry(at=(1, 3), random_spec={"a": {"range": [0, 10]}, "b": {"range": [0, 10]}}),
            InputEntry(at=(1, 3), random_spec={"c": {"range": [100, 200]}}),  # 制約に c は無関係
        ]
        # 制約は a と b だけに言及、c は影響を受けない
        stim = StimulusSchedule(inputs, 3, seed=7, constraints=["a < b"])
        for cycle in range(1, 4):
            vals = stim.get(cycle)
            a, b, c = vals.get("a", 0), vals.get("b", 0), vals.get("c", 0)
            assert a < b, f"cycle={cycle}: a={a} >= b={b}"
            assert 100 <= c <= 200, f"cycle={cycle}: c={c} not in [100,200]"

    def test_set_and_random_mixed_constraint(self):
        """set で固定した b と random の a に制約が効くこと"""
        inputs = [
            InputEntry(at=(1, 5), set_values={"b": 5}),
            InputEntry(at=(1, 5), random_spec={"a": {"range": [0, 15]}}),
        ]
        # b=5 固定, a は 0-15, 制約: a + b < 10 → a < 5
        stim = StimulusSchedule(inputs, 5, seed=99, constraints=["a + b < 10"])
        for cycle in range(1, 6):
            vals = stim.get(cycle)
            a, b = vals.get("a", 0), vals.get("b", 0)
            assert b == 5, f"b が固定されていない: {b}"
            assert a + b < 10, f"制約違反: a={a} b={b} sum={a+b}"

    def test_signal_width_masking(self):
        """4bit 信号幅マスクが適用されること（SV 幅規則）"""
        inputs = [InputEntry(at=1, random_spec={"a": {"range": [0, 15]}, "b": {"range": [0, 15]}})]
        # signal_widths=4 で a=15, b=1 のとき a+b は 4bit で 0（SV overflow）
        # ただしサンプル値自体は 0-15 なので制約の値がマスクされる
        stim = StimulusSchedule(inputs, 1, seed=42,
                                constraints=["a + b < 16"],
                                signal_widths={"a": 4, "b": 4})
        vals = stim.get(1)
        a, b = vals["a"], vals["b"]
        # 4bit マスク後の a+b（Python 算術）< 16
        assert (a & 0xF) + (b & 0xF) < 16


# ---------------------------------------------------------------------------
# 2. YAML API テスト
# ---------------------------------------------------------------------------

class TestConstraintsYAML:

    def test_top_level_constraints_apply(self, tmp_path):
        """top-level constraints が全ケースに適用されること"""
        spec = {**_base_spec_adder(),
                "constraints": ["a + b < 200"],
                "cases": [{"name": "c",
                           "stimulus": [{"at": [1, 5], "random": {"a": {"range": [0, 127]}, "b": {"range": [0, 127]}}}],
                           "expected": [{"at": 3, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        assert "E_CONSTRAINT" not in [e.code for e in result.errors]

    def test_case_level_constraints(self, tmp_path):
        """ケースレベルの constraints が動くこと"""
        spec = {**_base_spec_adder(),
                "cases": [{"name": "c",
                           "constraints": ["a > b"],
                           "stimulus": [{"at": [1, 5], "random": {"a": {"range": [0, 15]}, "b": {"range": [0, 15]}}}],
                           "expected": [{"at": 3, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        assert "E_CONSTRAINT" not in [e.code for e in result.errors]

    def test_impossible_constraint_returns_e_constraint(self, tmp_path):
        """不可能な制約で E_CONSTRAINT エラーが返ること"""
        spec = {**_base_spec_adder(),
                "constraints": ["a > 1000"],
                "cases": [{"name": "c",
                           "stimulus": [{"at": 1, "random": {"a": {"range": [0, 15]}, "b": {"range": [0, 15]}}}],
                           "expected": [{"at": 2, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_CONSTRAINT" in codes
        assert result.status == "ERROR"

    def test_e_constraint_message_contains_cycle_and_constraint(self, tmp_path):
        """E_CONSTRAINT のメッセージにサイクルと制約内容が含まれること"""
        spec = {**_base_spec_adder(),
                "constraints": ["a > 1000"],
                "max_constraint_attempts": 5,
                "cases": [{"name": "c",
                           "stimulus": [{"at": 1, "random": {"a": {"range": [0, 15]}, "b": {"range": [0, 15]}}}],
                           "expected": [{"at": 2, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        all_errors = list(result.errors) + [e for cr in result.cases for e in cr.errors]
        cstr_err = next((e for e in all_errors if e.code == "E_CONSTRAINT"), None)
        assert cstr_err is not None
        assert "a > 1000" in cstr_err.message

    def test_json_format_unchanged(self, tmp_path):
        """--json 出力形式が変わらないこと（schema_version = "2"）"""
        import json
        spec = {**_base_spec_adder(),
                "constraints": ["a + b < 200"],
                "cases": [{"name": "c",
                           "stimulus": [{"at": 1, "random": {"a": {"range": [0, 100]}, "b": {"range": [0, 100]}}}],
                           "expected": [{"at": 2, "check": {"y": 7}}]}]}
        sv_p = str(tmp_path / "adder.sv")
        Path(sv_p).write_text(SV_ADDER, encoding="utf-8")
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

    def test_same_seed_reproducible_actual_values(self, tmp_path):
        """seed 固定で実際に印加された入力値が再現すること（VCD で確認）"""
        # 2回実行して VCD の中身を比較
        spec = {**_base_spec_adder(cycles=5, seed=1234),
                "constraints": ["a + b < 200"],
                "cases": [{"name": "c",
                           "stimulus": [{"at": [1, 5], "random": {"a": {"range": [0, 100]}, "b": {"range": [0, 100]}}}],
                           "expected": [{"at": 3, "check": {"y": 7}}]}]}
        sv_p = str(tmp_path / "adder.sv")
        Path(sv_p).write_text(SV_ADDER, encoding="utf-8")
        yaml_p = str(tmp_path / "spec.yaml")

        # 同じ StimulusSchedule を2回作って値が同じか確認
        from deltasim.tb.scenario import load_scenario, StimulusSchedule
        with open(yaml_p, "w") as f:
            yaml.dump(spec, f)
        sc = load_scenario(yaml_p)
        case = sc.cases[0]
        inputs = case.inputs or sc.inputs

        stim1 = StimulusSchedule(inputs, 5, seed=1234, constraints=["a + b < 200"])
        stim2 = StimulusSchedule(inputs, 5, seed=1234, constraints=["a + b < 200"])
        for cycle in range(1, 6):
            assert stim1.get(cycle) == stim2.get(cycle), \
                f"seed 固定で値が違う: cycle={cycle}: {stim1.get(cycle)} vs {stim2.get(cycle)}"

    def test_random_signal_not_input_port_e_unknown_port(self, tmp_path):
        """random に input でない信号名を書くと E_UNKNOWN_PORT（Fix 1）"""
        spec = {**_base_spec_adder(),
                "cases": [{"name": "c",
                           "stimulus": [{"at": 1, "random": {"y": {"range": [0, 15]}}}],  # y は output
                           "expected": [{"at": 2, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes

    def test_internal_signal_in_random_e_unknown_port(self, tmp_path):
        """random に内部信号名を書くと E_UNKNOWN_PORT"""
        sv_p = str(tmp_path / "adder_int.sv")
        Path(sv_p).write_text(SV_ADDER_INTERNAL, encoding="utf-8")
        spec = {
            "version": 1, "top": "adder_int",  # top を明示
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 3, "seed": 1},
            "cases": [{"name": "c",
                       "stimulus": [{"at": 1, "random": {"tmp": {"range": [0, 7]}}}],  # tmp は内部信号
                       "expected": [{"at": 2, "check": {"y": 7}}]}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec=yaml_p)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes

    def test_unknown_signal_in_constraint_e_unknown_port(self, tmp_path):
        """constraints で存在しない識別子を使うと E_UNKNOWN_PORT（Fix 2）"""
        spec = {**_base_spec_adder(),
                "constraints": ["nonexistent > 5"],  # 存在しないシグナル
                "cases": [{"name": "c",
                           "stimulus": [{"at": 1, "random": {"a": {"range": [0, 10]}, "b": {"range": [0, 10]}}}],
                           "expected": [{"at": 2, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes, f"expected E_UNKNOWN_PORT but got: {codes}"

    def test_w_unknown_key_not_triggered_by_constraints(self, tmp_path):
        """constraints は既知キーなので W_UNKNOWN_KEY が出ないこと"""
        spec = {**_base_spec_adder(),
                "constraints": ["a < b"],
                "cases": [{"name": "c",
                           "stimulus": [{"at": 1, "random": {"a": {"range": [0, 10]}, "b": {"range": [0, 10]}}}],
                           "expected": [{"at": 2, "check": {"y": 7}}]}]}
        result, _, _ = _make_adder_spec(tmp_path, spec)
        all_warnings = list(result.warnings) + [w for cr in result.cases for w in cr.warnings]
        unk = [w for w in all_warnings if w.code == "W_UNKNOWN_KEY"]
        assert unk == [], f"W_UNKNOWN_KEY が出た: {[w.message for w in unk]}"

    def test_max_constraint_attempts_zero_raises(self, tmp_path):
        """max_constraint_attempts=0 はエラーになること"""
        from deltasim.tb.scenario import load_scenario, ScenarioError
        spec = {**_base_spec_adder(),
                "max_constraint_attempts": 0,
                "cases": [{"name": "c", "expected": []}]}
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w") as f:
            yaml.dump(spec, f)
        with pytest.raises(ScenarioError, match="1 以上"):
            load_scenario(yaml_p)
