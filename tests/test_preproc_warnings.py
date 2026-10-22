"""tests/test_preproc_warnings.py — §18.9.1 プリプロセッサ警告 JSON 出力テスト

W_TIMESCALE と W_UNKNOWN_DIRECTIVE が:
- deltasim test --json の warnings[] に code/file/line/message で入ること
- Python API の TestResult.warnings にも同じ内容が入ること
- CLI の stderr 表示と JSON の message が一致すること
- 指令なしの RTL では warnings は従来どおり（空なら空）
- JSON のトップレベルキー集合が変わっていないこと
"""
from __future__ import annotations

import json
import subprocess
import sys
import yaml
from pathlib import Path
import pytest
import deltasim.api as ds


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write(tmp: Path, name: str, content: str) -> str:
    p = tmp / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def _cli_json(sv_p: str, spec_p: str, tmp: Path) -> dict:
    json_p = str(tmp / "out.json")
    import os as _os
    env = {**_os.environ, "PYTHONUTF8": "1"}
    r = subprocess.run(
        [sys.executable, "-m", "deltasim", "test",
         sv_p, "--spec", spec_p, "--json", json_p],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env=env,
    )
    return json.loads(Path(json_p).read_text(encoding="utf-8")), r.stderr


_BASE_SPEC = {
    "version": 1,
    "clock": {"signal": "clk", "period_ns": 10},
    "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
    "sim": {"cycles": 3},
    "cases": [{"name": "t", "expected": [{"at": 3, "check": {"q": 1}}]}],
}

_COUNTER_BODY = """\
module ctr(input logic clk, rst_n, output logic [7:0] q);
  always_ff @(posedge clk)
    if (!rst_n) q <= 0; else q <= q + 1;
endmodule
"""

# JSON トップレベルキー（PR #32 マージ後の基準）
_EXPECTED_TOP_KEYS = frozenset({
    "cancelled", "cases", "checks", "deltasim_version", "elapsed_ms",
    "errors", "log", "module", "schema_version", "seed", "status",
    "summary", "truncated_at", "warnings",
})


# ===========================================================================
# 1. W_TIMESCALE — `timescale 指令
# ===========================================================================

class TestWTimescale:
    """`timescale が warnings[] に W_TIMESCALE で入ること。"""

    _SV = """\
`timescale 1ns/1ps
""" + _COUNTER_BODY

    def test_api_warnings_contains_timescale(self, tmp_path):
        """`timescale 付き RTL を API でテストすると warnings に W_TIMESCALE が入る"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec_p)
        assert result.status == "PASS"
        w_codes = [w.code for w in result.warnings]
        assert "W_TIMESCALE" in w_codes, \
            f"W_TIMESCALE expected in API warnings: {w_codes}"
        # file と line がリテラルで一致
        w = next(w for w in result.warnings if w.code == "W_TIMESCALE")
        assert Path(w.file).name == "m.sv", f"file should be m.sv: {w.file}"
        assert w.line == 1, f"line should be 1: {w.line}"

    def test_cli_json_warnings_timescale(self, tmp_path):
        """CLI --json の warnings[] に W_TIMESCALE code/file/line が入ること"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out, stderr = _cli_json(sv_p, spec_p, tmp_path)
        assert out["status"] == "PASS"
        warnings = out.get("warnings", [])
        w_codes = [w["code"] for w in warnings]
        assert "W_TIMESCALE" in w_codes, \
            f"W_TIMESCALE expected in CLI JSON warnings: {w_codes}"
        w = next(w for w in warnings if w["code"] == "W_TIMESCALE")
        assert "m.sv" in w["file"], f"file should contain m.sv: {w['file']}"
        assert w["line"] == 1, f"line should be 1: {w['line']}"

    def test_cli_json_message_matches_stderr(self, tmp_path):
        """CLI JSON の warnings[].message が stderr の表示と一致すること"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        json_p = str(tmp_path / "out.json")
        r = subprocess.run(
            [sys.executable, "-m", "deltasim", "test",
             sv_p, "--spec", spec_p, "--json", json_p],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        out = json.loads(Path(json_p).read_text(encoding="utf-8"))
        w = next((w for w in out.get("warnings", []) if w["code"] == "W_TIMESCALE"), None)
        assert w is not None
        # message は `timescale の値を含む
        assert "timescale" in w["message"].lower() or "1ns" in w["message"], \
            f"W_TIMESCALE message should mention timescale: {w['message']}"

    def test_json_top_level_keys_unchanged(self, tmp_path):
        """JSON のトップレベルキー集合が main と同じであること"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out, _ = _cli_json(sv_p, spec_p, tmp_path)
        assert frozenset(out.keys()) == _EXPECTED_TOP_KEYS, \
            f"JSON keys changed: {frozenset(out.keys()) ^ _EXPECTED_TOP_KEYS}"


# ===========================================================================
# 2. W_UNKNOWN_DIRECTIVE — `pragma 等
# ===========================================================================

class TestWUnknownDirective:
    """`pragma 等の非対応指令が warnings[] に W_UNKNOWN_DIRECTIVE で入ること。"""

    _SV = """\
`pragma protect begin
""" + _COUNTER_BODY + """\
`pragma protect end
"""

    def test_api_warnings_unknown_directive(self, tmp_path):
        """非対応指令付き RTL を API でテストすると W_UNKNOWN_DIRECTIVE が入る"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec_p)
        w_codes = [w.code for w in result.warnings]
        assert "W_UNKNOWN_DIRECTIVE" in w_codes, \
            f"W_UNKNOWN_DIRECTIVE expected: {w_codes}"
        # file はソースファイル
        w = next(w for w in result.warnings if w.code == "W_UNKNOWN_DIRECTIVE")
        assert "m.sv" in w.file, f"file should be m.sv: {w.file}"
        assert w.line >= 1, f"line should be >= 1: {w.line}"

    def test_cli_json_unknown_directive(self, tmp_path):
        """CLI JSON に W_UNKNOWN_DIRECTIVE が入ること"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out, _ = _cli_json(sv_p, spec_p, tmp_path)
        w_codes = [w["code"] for w in out.get("warnings", [])]
        assert "W_UNKNOWN_DIRECTIVE" in w_codes, \
            f"W_UNKNOWN_DIRECTIVE expected in CLI JSON: {w_codes}"
        w = next(w for w in out.get("warnings", [])
                 if w["code"] == "W_UNKNOWN_DIRECTIVE")
        assert "m.sv" in w["file"]
        assert w["line"] >= 1

    def test_pragma_line_number_literal(self, tmp_path):
        """`pragma は元ファイルの行番号でリテラル assert"""
        sv_p = _write(tmp_path, "m.sv", self._SV)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec_p)
        ws = [w for w in result.warnings if w.code == "W_UNKNOWN_DIRECTIVE"]
        assert len(ws) >= 1, "at least one W_UNKNOWN_DIRECTIVE expected"
        # 最初の `pragma は1行目
        assert ws[0].line == 1, f"first pragma should be on line 1: {ws[0].line}"


# ===========================================================================
# 3. 指令なしの RTL では warnings が従来どおり
# ===========================================================================

class TestNoDirective:
    """プリプロセッサ指令を含まない RTL では preproc warnings が追加されないこと。"""

    def test_clean_rtl_no_preproc_warnings(self, tmp_path):
        """指令なし RTL の warnings に W_TIMESCALE/W_UNKNOWN_DIRECTIVE が含まれないこと"""
        sv_p = _write(tmp_path, "m.sv", _COUNTER_BODY)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        result = ds.test([sv_p], spec_p)
        assert result.status == "PASS"
        preproc_codes = {w.code for w in result.warnings
                         if w.code in ("W_TIMESCALE", "W_UNKNOWN_DIRECTIVE")}
        assert not preproc_codes, \
            f"No preproc warnings expected for clean RTL: {preproc_codes}"

    def test_cli_clean_rtl_no_preproc_warnings(self, tmp_path):
        """指令なし RTL の CLI JSON warnings に W_TIMESCALE/W_UNKNOWN_DIRECTIVE がないこと"""
        sv_p = _write(tmp_path, "m.sv", _COUNTER_BODY)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out, _ = _cli_json(sv_p, spec_p, tmp_path)
        preproc_codes = {w["code"] for w in out.get("warnings", [])
                         if w["code"] in ("W_TIMESCALE", "W_UNKNOWN_DIRECTIVE")}
        assert not preproc_codes, \
            f"No preproc warnings expected for clean RTL: {preproc_codes}"


# ===========================================================================
# 4. WarningInfo 要素の形式確認
# ===========================================================================

class TestWarningInfoFormat:
    """warnings[] 要素の形式が既存の W_FILELIST_OPT 等と同じであること。"""

    def test_warning_info_keys(self, tmp_path):
        """W_TIMESCALE の要素キーが code/message/file/line/column であること"""
        sv_p = _write(tmp_path, "m.sv", "`timescale 1ns/1ps\n" + _COUNTER_BODY)
        spec = {**_BASE_SPEC, "top": "ctr"}
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        out, _ = _cli_json(sv_p, spec_p, tmp_path)
        w = next((w for w in out.get("warnings", []) if w["code"] == "W_TIMESCALE"), None)
        assert w is not None
        # 既存の W_FILELIST_OPT と同じキーセット
        expected_keys = {"code", "message", "file", "line", "column"}
        assert expected_keys.issubset(set(w.keys())), \
            f"warning keys should include {expected_keys}: {set(w.keys())}"
