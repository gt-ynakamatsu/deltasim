"""tests/test_preproc.py — §18.9 プリプロセッサテスト

テスト対象:
  - 各指令（define/undef/ifdef/ifndef/elsif/else/endif/include/timescale/default_nettype）
  - 入れ子 ifdef
  - `include の探索順（ファイル位置→+incdir+）
  - E_INCLUDE_LOOP（2ファイル循環）
  - 未定義マクロの E001（元ファイル名と行を指すこと）
  - include 先の構文エラーが include 先ファイルの行を指すこと
  - -D/-I と +define+/+incdir+ の E2E（ds.load / CLI）
  - ifdef で切り替えた RTL が実際に異なるシミュ結果になること
  - プリプロセッサ指令を含まない既存 RTL で結果が変わらないこと
  - 引数付きマクロ（デフォルト値、引数数不一致）
  - CRLF ファイルの扱い
  - 複数行マクロ（バックスラッシュ継続）
  - コメント・文字列内のバッククォートは指令と扱わない
  - YAML の defines/incdirs キー
"""
from __future__ import annotations

import os
import sys
import yaml
from pathlib import Path
import pytest
import deltasim.api as ds


EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# 1. プリプロセッサ単体テスト
# ---------------------------------------------------------------------------

class TestPreprocBasic:
    """define/undef/ifdef/etc の単体テスト"""

    def test_define_simple(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define WIDTH 8\nlogic [`WIDTH-1:0] x;\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "WIDTH" not in text
        assert "8-1:0" in text or "[8-1:0]" in text

    def test_define_with_macro_use(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define BITS 4\n`define MASK ((1<<`BITS)-1)\nassign y = `MASK;\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "((1<<4)-1)" in text

    def test_undef(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"SIM": "1"})
        src = "`undef SIM\n`ifdef SIM\nassert(1);\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "assert" not in text

    def test_ifdef_active(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"DEBUG": "1"})
        src = "`ifdef DEBUG\nassert(1);\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "assert" in text

    def test_ifdef_inactive(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`ifdef DEBUG\nassert(1);\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "assert" not in text

    def test_ifndef_active(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`ifndef RELEASE\nassert(1);\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "assert" in text

    def test_ifndef_inactive(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"RELEASE": "1"})
        src = "`ifndef RELEASE\nassert(1);\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "assert" not in text

    def test_elsif_first_branch(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"A": "1"})
        src = "`ifdef A\nA_TEXT\n`elsif B\nB_TEXT\n`else\nC_TEXT\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "A_TEXT" in text
        assert "B_TEXT" not in text
        assert "C_TEXT" not in text

    def test_elsif_second_branch(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"B": "1"})
        src = "`ifdef A\nA_TEXT\n`elsif B\nB_TEXT\n`else\nC_TEXT\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "A_TEXT" not in text
        assert "B_TEXT" in text
        assert "C_TEXT" not in text

    def test_else_branch(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`ifdef A\nA_TEXT\n`else\nC_TEXT\n`endif\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "A_TEXT" not in text
        assert "C_TEXT" in text

    def test_nested_ifdef(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"OUTER": "1", "INNER": "1"})
        src = (
            "`ifdef OUTER\n"
            "  `ifdef INNER\n"
            "  BOTH\n"
            "  `else\n"
            "  OUTER_ONLY\n"
            "  `endif\n"
            "`endif\n"
        )
        text, sm = pp.process_string(src, "t.sv")
        assert "BOTH" in text
        assert "OUTER_ONLY" not in text

    def test_nested_ifdef_outer_only(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor(defines={"OUTER": "1"})
        src = (
            "`ifdef OUTER\n"
            "  `ifdef INNER\n"
            "  BOTH\n"
            "  `else\n"
            "  OUTER_ONLY\n"
            "  `endif\n"
            "`endif\n"
        )
        text, sm = pp.process_string(src, "t.sv")
        assert "BOTH" not in text
        assert "OUTER_ONLY" in text

    def test_timescale_accepted(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`timescale 1ns/1ps\nmodule m;\nendmodule\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "timescale" not in text
        assert len(pp.warnings) >= 1
        assert pp.warnings[0].code == "W_TIMESCALE"

    def test_default_nettype_accepted(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`default_nettype none\nmodule m;\nendmodule\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "default_nettype" not in text

    def test_unknown_directive_warning(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`pragma protect begin\nlogic x;\n`pragma protect end\n"
        text, sm = pp.process_string(src, "t.sv")
        assert any(w.code == "W_UNKNOWN_DIRECTIVE" for w in pp.warnings)


class TestPreprocParametricMacro:
    """引数付きマクロのテスト"""

    def test_simple_args(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define ADD(a, b) ((a) + (b))\nassign y = `ADD(x, 1);\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "((x) + (1))" in text

    def test_default_value_used(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define MULT(a, b=2) (a * b)\nassign y = `MULT(x);\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "(x * 2)" in text

    def test_default_value_overridden(self):
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define MULT(a, b=2) (a * b)\nassign y = `MULT(x, 3);\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "(x * 3)" in text

    def test_arg_count_mismatch(self):
        from deltasim.preproc import Preprocessor, PreprocError
        pp = Preprocessor()
        src = "`define F(a, b) a + b\nassign y = `F(x);\n"
        with pytest.raises(PreprocError) as exc_info:
            pp.process_string(src, "t.sv")
        assert exc_info.value.code == "E001"

    def test_nested_parens_in_arg(self):
        """括弧内のカンマは引数区切りにしない"""
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define WRAP(a) [a]\nassign y = `WRAP((x, z));\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "[(x, z)]" in text

    def test_multiline_macro(self):
        """行末バックスラッシュで複数行マクロ"""
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = (
            "`define BIG_ASSIGN(a, b) \\\n"
            "  if (a) \\\n"
            "    b <= 1;\n"
            "`BIG_ASSIGN(en, q)\n"
        )
        text, sm = pp.process_string(src, "t.sv")
        assert "if (en)" in text
        assert "q <= 1" in text

    def test_comment_in_line_not_macro(self):
        """// コメント後のバッククォートは指令ではない"""
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = "`define X 1\nlogic y; // this is `X in a comment\n"
        text, sm = pp.process_string(src, "t.sv")
        assert "// this is `X in a comment" in text

    def test_string_not_macro(self):
        """文字列内のバッククォートは指令ではない"""
        from deltasim.preproc import Preprocessor
        pp = Preprocessor()
        src = '`define MSG "use `define"\n$display(`MSG);\n'
        text, sm = pp.process_string(src, "t.sv")
        assert '"use `define"' in text


class TestPreprocSourceMap:
    """ソースマップ（元ファイル・元行対応）のテスト"""

    def test_sourcemap_simple(self, tmp_path):
        """展開後の各行が元ファイルの正確な行番号にマップされること。

        `define は展開後テキストに現れず、後続行の番号がその分ずれること。
        """
        from deltasim.preproc import Preprocessor
        p = _write(tmp_path, "t.sv",
                   "logic a;\n`define X 1\nlogic b;\n")
        pp = Preprocessor()
        text, sm = pp.process_file(p)
        exp_lines = text.split("\n")

        # 展開後行1: "logic a;" → 元 t.sv:1
        assert exp_lines[0] == "logic a;", f"exp[0]={exp_lines[0]!r}"
        assert sm[1].file == p and sm[1].line == 1, f"sm[1]={sm[1]}"

        # 展開後行2: "logic b;" → 元 t.sv:3 (行2の define はスキップ)
        assert exp_lines[1] == "logic b;", f"exp[1]={exp_lines[1]!r}"
        assert sm[2].file == p and sm[2].line == 3, f"sm[2]={sm[2]}"

    def test_sourcemap_multiline_define(self, tmp_path):
        """行末バックスラッシュの複数行 define 後の行が正しくマップされること。"""
        from deltasim.preproc import Preprocessor
        # 3行でdefineを定義、4行目が通常テキスト
        p = _write(tmp_path, "t.sv",
                   "`define F(a) \\\n  a+1\nassign y = `F(x);\nlogic z;\n")
        pp = Preprocessor()
        text, sm = pp.process_file(p)
        exp_lines = text.split("\n")

        # "assign y = " は元 t.sv:3
        assign_idx = next(i for i, l in enumerate(exp_lines, 1) if "assign y" in l)
        assert sm[assign_idx].file == p
        assert sm[assign_idx].line == 3, f"assign line: sm={sm[assign_idx]}"

        # "logic z;" は元 t.sv:4
        z_idx = next(i for i, l in enumerate(exp_lines, 1) if "logic z" in l)
        assert sm[z_idx].file == p
        assert sm[z_idx].line == 4, f"logic z: sm={sm[z_idx]}"

    def test_sourcemap_include(self, tmp_path):
        """include 後の各行が元ファイル・元行を正確に指すこと。"""
        from deltasim.preproc import Preprocessor
        inc = _write(tmp_path, "inc.sv", "logic x;\n")
        main = _write(tmp_path, "main.sv", '`include "inc.sv"\nlogic y;\n')
        pp = Preprocessor()
        text, sm = pp.process_file(main)
        exp_lines = text.split("\n")

        # 展開後行1: "logic x;" → inc.sv:1
        assert exp_lines[0] == "logic x;", f"exp[0]={exp_lines[0]!r}"
        assert sm[1].file == inc, f"sm[1].file={sm[1].file!r}"
        assert sm[1].line == 1, f"sm[1].line={sm[1].line}"

        # "logic y;" → main.sv:2
        y_idx = next(i for i, l in enumerate(exp_lines, 1) if "logic y" in l)
        assert sm[y_idx].file == main, f"sm[{y_idx}].file={sm[y_idx].file!r}"
        assert sm[y_idx].line == 2, f"sm[{y_idx}].line={sm[y_idx].line}"


# ---------------------------------------------------------------------------
# 2. include 関連テスト
# ---------------------------------------------------------------------------

class TestPreprocInclude:
    """`include のパス解決・エラーテスト"""

    def test_include_relative(self, tmp_path):
        from deltasim.preproc import Preprocessor
        inc = _write(tmp_path, "inc.sv", "logic y;\n")
        main = _write(tmp_path, "main.sv", '`include "inc.sv"\nlogic z;\n')
        pp = Preprocessor()
        text, _ = pp.process_file(main)
        assert "logic y" in text
        assert "logic z" in text

    def test_include_incdir(self, tmp_path):
        from deltasim.preproc import Preprocessor
        sub = tmp_path / "subdir"
        sub.mkdir()
        inc = _write(sub, "utils.sv", "logic u;\n")
        main = _write(tmp_path, "main.sv", '`include "utils.sv"\n')
        pp = Preprocessor(incdirs=[str(sub)])
        text, _ = pp.process_file(main)
        assert "logic u" in text

    def test_include_not_found(self, tmp_path):
        from deltasim.preproc import Preprocessor, PreprocError
        main = _write(tmp_path, "main.sv", '`include "missing.sv"\n')
        pp = Preprocessor()
        with pytest.raises(PreprocError) as exc_info:
            pp.process_file(main)
        e = exc_info.value
        assert e.code == "E001"
        assert "missing.sv" in str(e)
        assert e.file == main      # エラーは元ファイルを指す
        assert e.line == 1         # 行番号も正確

    def test_include_not_found_line_number(self, tmp_path):
        """include が2行目にある場合、エラーが2行目を指す"""
        from deltasim.preproc import Preprocessor, PreprocError
        main = _write(tmp_path, "main.sv",
                      "logic a;\n`include \"missing.sv\"\n")
        pp = Preprocessor()
        with pytest.raises(PreprocError) as exc_info:
            pp.process_file(main)
        e = exc_info.value
        assert e.line == 2

    def test_include_search_order_file_first(self, tmp_path):
        """ファイルの場所（相対パス）が incdir より優先されること"""
        from deltasim.preproc import Preprocessor
        # ファイル側: "FILE_VERSION"
        local_inc = _write(tmp_path, "common.sv", "// FILE_VERSION\n")
        # incdir 側: "DIR_VERSION"
        sub = tmp_path / "inc"
        sub.mkdir()
        dir_inc = _write(sub, "common.sv", "// DIR_VERSION\n")
        main = _write(tmp_path, "main.sv", '`include "common.sv"\n')
        pp = Preprocessor(incdirs=[str(sub)])
        text, _ = pp.process_file(main)
        assert "FILE_VERSION" in text
        assert "DIR_VERSION" not in text

    def test_include_loop_two_files(self, tmp_path):
        """2ファイル循環 include が E_INCLUDE_LOOP を発生させること"""
        from deltasim.preproc import Preprocessor, PreprocError
        a = _write(tmp_path, "a.sv", '`include "b.sv"\n')
        b = _write(tmp_path, "b.sv", '`include "a.sv"\n')
        pp = Preprocessor()
        with pytest.raises(PreprocError) as exc_info:
            pp.process_file(a)
        assert exc_info.value.code == "E_INCLUDE_LOOP"

    def test_include_windows_crlf(self, tmp_path):
        """CRLF ファイルの include が正しく動作すること"""
        from deltasim.preproc import Preprocessor
        inc = tmp_path / "inc.sv"
        inc.write_bytes(b"logic x;\r\n")
        main = _write(tmp_path, "main.sv", '`include "inc.sv"\n')
        pp = Preprocessor()
        text, _ = pp.process_file(main)
        assert "logic x" in text


# ---------------------------------------------------------------------------
# 3. エラー位置（ソースマップ経由）のテスト
# ---------------------------------------------------------------------------

class TestPreprocErrorLocation:
    """エラーが元ファイルの行を指すことの確認"""

    def test_undefined_macro_error_location(self, tmp_path):
        """未定義マクロのエラーが元ファイルの行を指すこと"""
        from deltasim.preproc import Preprocessor, PreprocError
        src = "logic a;\n`UNDEFINED_MACRO\n"
        pp = Preprocessor()
        with pytest.raises(PreprocError) as exc_info:
            pp.process_string(src, "my_file.sv")
        e = exc_info.value
        assert e.code == "E001"
        assert "UNDEFINED_MACRO" in str(e)
        assert e.file == "my_file.sv"
        assert e.line == 2   # 2行目

    def test_include_syntax_error_points_to_include_file(self, tmp_path):
        """include 先の ParseError が include 先ファイルの行を指すこと（無条件 assert）。"""
        from deltasim.parser.parser import parse_file, ParseError
        # bad.sv: 2行目に本当の構文エラー（ポートリストが閉じられない）
        inc = _write(tmp_path, "bad.sv",
                     "module bad(input logic d\n"  # missing ')' → ParseError at line 2
                     "endmodule\n")
        main = _write(tmp_path, "main.sv",
                      '`include "bad.sv"\nmodule m;\nendmodule\n')
        with pytest.raises(ParseError) as exc_info:
            parse_file(main)
        e = exc_info.value
        # エラーメッセージに bad.sv と行番号 2 が含まれること
        assert "bad.sv" in str(e), f"error should point to bad.sv: {e}"
        assert ":2:" in str(e), f"error should be on line 2: {e}"

    def test_include_syntax_error_cli_json(self, tmp_path):
        """include 先構文エラーが CLI --json の errors[].file/line でも元ファイルを指すこと。"""
        import subprocess, sys, json as _json
        inc = _write(tmp_path, "bad.sv",
                     "module bad(input logic d\n"
                     "endmodule\n")
        main = _write(tmp_path, "main.sv",
                      '`include "bad.sv"\nmodule m;\nendmodule\n')
        spec = {
            "version": 1, "top": "bad",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [{"name": "t", "expected": [{"at": 1, "check": {"d": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)
        json_p = str(tmp_path / "out.json")
        subprocess.run(
            [sys.executable, "-m", "deltasim", "test", main,
             "--spec", spec_p, "--json", json_p],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        out = _json.loads(Path(json_p).read_text(encoding="utf-8"))
        errors = out.get("errors", [])
        assert errors, "errors should be non-empty"
        e = errors[0]
        assert "bad.sv" in e.get("file", ""), \
            f"errors[0].file should contain bad.sv: {e}"
        assert e.get("line") == 2, \
            f"errors[0].line should be 2: {e}"

    def test_main_file_syntax_error_points_to_main(self, tmp_path):
        """include より後ろの main.sv 構文エラーが main.sv の元行を指すこと。"""
        from deltasim.parser.parser import parse_file, ParseError
        inc = _write(tmp_path, "ok.sv", "`define OK 1\n")
        # main.sv の5行目に構文エラー（ポートリスト未完了）
        main = _write(tmp_path, "main.sv",
                      '`include "ok.sv"\n'        # line 1
                      '// comment\n'               # line 2
                      '// comment2\n'              # line 3
                      '// comment3\n'              # line 4
                      'module m(input logic d\n'   # line 5 - missing )
                      'endmodule\n')               # line 6
        with pytest.raises(ParseError) as exc_info:
            parse_file(main)
        e = exc_info.value
        assert "main.sv" in str(e), f"error should point to main.sv: {e}"
        assert ":6:" in str(e), f"error should be on line 6 (endmodule): {e}"


# ---------------------------------------------------------------------------
# 4. パーサ統合テスト
# ---------------------------------------------------------------------------

class TestPreprocParserIntegration:
    """プリプロセッサをパーサと組み合わせたテスト"""

    def test_ifdef_rtl_different_width(self, tmp_path):
        """ifdef で切り替えた RTL が実際に異なる WIDTH になること"""
        sv = _write(tmp_path, "m.sv", """\
`ifdef WIDE
module m(input logic [15:0] d, output logic [15:0] q);
`else
module m(input logic [7:0] d, output logic [7:0] q);
`endif
  assign q = d;
endmodule
""")
        # WIDE なし
        di_narrow = ds.load([sv])
        # WIDE あり
        di_wide = ds.load([sv], defines={"WIDE": "1"})

        assert di_narrow.status == "PASS"
        assert di_wide.status == "PASS"
        # WIDTH の違い
        narrow_width = next(p.width for p in di_narrow.ports if p.name == "d")
        wide_width = next(p.width for p in di_wide.ports if p.name == "d")
        assert narrow_width == 8
        assert wide_width == 16

    def test_no_directive_rtl_unchanged(self):
        """プリプロセッサ指令を含まない既存 RTL では結果が変わらないこと"""
        counter = os.path.join(EXAMPLES, "counter.sv")
        if not os.path.exists(counter):
            pytest.skip("examples/counter.sv not found")
        # defines なし
        di_plain = ds.load([counter])
        # defines あり（RTL に影響なし）
        di_with = ds.load([counter], defines={"UNUSED": "1"})
        assert di_plain.status == "PASS"
        assert di_with.status == "PASS"
        assert di_plain.module == di_with.module
        # ポート数・幅も同じ
        assert len(di_plain.ports) == len(di_with.ports)


# ---------------------------------------------------------------------------
# 5. E2E テスト（ds.test / CLI）
# ---------------------------------------------------------------------------

class TestPreprocE2E:
    """ds.test / CLI で -D/-I が機能すること"""

    def test_ds_test_with_define(self, tmp_path):
        """ds.test に defines を渡すと ifdef 分岐が有効になること"""
        sv = _write(tmp_path, "m.sv", """\
`ifdef RESET_VAL_ONE
module m(input logic clk, rst_n, output logic [7:0] q);
  always_ff @(posedge clk)
    if (!rst_n) q <= 8'd1;
    else q <= q + 1;
endmodule
`else
module m(input logic clk, rst_n, output logic [7:0] q);
  always_ff @(posedge clk)
    if (!rst_n) q <= 8'd0;
    else q <= q + 1;
endmodule
`endif
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "cases": [
                {"name": "reset_val_one",
                 "expected": [{"at": 2, "check": {"q": 1}}]},
            ],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)

        # defines={RESET_VAL_ONE: 1} → q リセット後 = 1
        result = ds.test([sv], spec_p, defines={"RESET_VAL_ONE": "1"})
        assert result.status == "PASS", \
            f"Expected PASS with RESET_VAL_ONE: {[e.message for e in result.errors]}"

        # defines なし → q リセット後 = 0
        spec2 = dict(spec)
        spec2["cases"] = [{"name": "reset_val_zero",
                           "expected": [{"at": 2, "check": {"q": 0}}]}]
        spec_p2 = str(tmp_path / "spec2.yaml")
        with open(spec_p2, "w") as f:
            yaml.dump(spec2, f)
        result2 = ds.test([sv], spec_p2)
        assert result2.status == "PASS"

    def test_ds_test_with_include(self, tmp_path):
        """ds.test で include が正しく動作すること"""
        inc = _write(tmp_path, "types.sv",
                     "`define DATA_W 8\n")
        sv = _write(tmp_path, "m.sv", """\
`include "types.sv"
module m(input logic [`DATA_W-1:0] d, output logic [`DATA_W-1:0] q);
  assign q = d;
endmodule
""")
        di = ds.load([sv], incdirs=[str(tmp_path)])
        assert di.status == "PASS"
        w = next(p.width for p in di.ports if p.name == "d")
        assert w == 8

    def test_cli_define_option(self, tmp_path):
        """-D あり/なしで出力が異なることを確認。"""
        sv = _write(tmp_path, "m.sv", """\
`ifdef USE_CLK
module m(input logic clk, input logic [7:0] d, output logic [7:0] q);
  always_ff @(posedge clk) q <= d;
endmodule
`else
module m(input logic [7:0] d, output logic [7:0] q);
  assign q = d;
endmodule
`endif
""")
        import subprocess, sys
        # -D USE_CLK: clk ポートがあること
        r_with = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv, "-D", "USE_CLK"],
            capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        assert r_with.returncode == 0, r_with.stderr
        assert "clk" in r_with.stdout, f"-D あり: clk が stdout にない: {r_with.stdout!r}"

        # -D なし: clk ポートがないこと
        r_without = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv],
            capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        assert r_without.returncode == 0, r_without.stderr
        assert "clk" not in r_without.stdout, \
            f"-D なし: clk が stdout にある（意図しない）: {r_without.stdout!r}"

    def test_cli_incdir_option(self, tmp_path):
        """-I でインクルードパスを追加し、ポート幅が PKG_W=4 になることを確認。
        -I なしでは E001 + include ファイル名が stderr に出ること。"""
        sub = tmp_path / "inc"
        sub.mkdir()
        _write(sub, "pkg.sv", "`define PKG_W 4\n")
        sv = _write(tmp_path, "m.sv", """\
`include "pkg.sv"
module m(input logic [`PKG_W-1:0] d, output logic [`PKG_W-1:0] q);
  assign q = d;
endmodule
""")
        import subprocess, sys
        # -I あり: ポート幅 4 になること
        r_with = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv, "-I", str(sub)],
            capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        assert r_with.returncode == 0, r_with.stderr
        assert "4" in r_with.stdout, f"-I あり: 幅4が stdout にない: {r_with.stdout!r}"

        # -I なし: エラーになること（pkg.sv が見つからない）
        r_without = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv],
            capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        assert r_without.returncode != 0, "-I なし: エラーになるはず"
        assert "pkg.sv" in r_without.stderr or "E001" in r_without.stderr, \
            f"-I なし: pkg.sv または E001 が stderr にない: {r_without.stderr!r}"

    def test_yaml_defines_key(self, tmp_path):
        """YAML の defines キーが機能すること"""
        sv = _write(tmp_path, "m.sv", """\
`ifdef MY_DEFINE
module m(input logic clk, rst_n, output logic [7:0] q);
  always_ff @(posedge clk)
    if (!rst_n) q <= 0;
    else q <= q + 1;
endmodule
`else
// no module without MY_DEFINE
`endif
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "defines": ["+define+MY_DEFINE"],
            "cases": [{"name": "t",
                       "expected": [{"at": 3, "check": {"q": 1}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv], spec_p)
        assert result.status == "PASS", \
            f"YAML defines should activate MY_DEFINE: {[e.message for e in result.errors]}"

    def test_yaml_incdirs_key(self, tmp_path):
        """YAML の incdirs キーが機能すること"""
        sub = tmp_path / "inc"
        sub.mkdir()
        _write(sub, "defs.sv", "`define CNT_W 4\n")
        sv = _write(tmp_path, "m.sv", """\
`include "defs.sv"
module m(input logic clk, rst_n, output logic [`CNT_W-1:0] q);
  always_ff @(posedge clk)
    if (!rst_n) q <= 0;
    else q <= q + 1;
endmodule
""")
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "incdirs": [str(sub)],
            "cases": [{"name": "t",
                       "expected": [{"at": 3, "check": {"q": 1}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w") as f:
            yaml.dump(spec, f)
        result = ds.test([sv], spec_p)
        assert result.status == "PASS"


# ---------------------------------------------------------------------------
# 6. safety.py 分類テスト
# ---------------------------------------------------------------------------

class TestPreprocSafetyClassification:
    """safety.py での -I/-D オプション分類テスト"""

    def test_D_is_non_path(self):
        from deltasim.llm.safety import _NON_PATH_OPTS
        assert "-D" in _NON_PATH_OPTS or "--define" in _NON_PATH_OPTS

    def test_I_is_read_opt(self):
        from deltasim.llm.safety import _READ_OPTS_SHORT, _READ_OPTS_LONG
        assert "-I" in _READ_OPTS_SHORT or "--incdir" in _READ_OPTS_LONG

    def test_classify_D_safe(self):
        """deltasim parse に -D オプションを付けても safe 扱いになること"""
        from deltasim.llm.safety import check_command
        cmd, allowed, denied = check_command("deltasim parse m.sv -D DEBUG")
        assert not denied, f"-D should be safe: denied={denied}"

    def test_classify_I_safe(self):
        """deltasim parse に -I オプションを付けても safe 扱いになること"""
        from deltasim.llm.safety import check_command
        cmd, allowed, denied = check_command("deltasim parse m.sv -I ./inc")
        assert not denied, f"-I should be safe: denied={denied}"


# ---------------------------------------------------------------------------
# 7. filelist (.f) の +define+/+incdir+ との統合テスト
# ---------------------------------------------------------------------------

class TestPreprocFilelist:
    """.f ファイルの +define+ / +incdir+ が機能すること"""

    def test_define_from_filelist(self, tmp_path):
        sv = _write(tmp_path, "m.sv", """\
`ifdef SIM_MODE
module m(input logic clk, output logic q);
  assign q = 1;
endmodule
`else
module m(input logic clk, output logic q);
  assign q = 0;
endmodule
`endif
""")
        flist = _write(tmp_path, "files.f",
                       f"{sv}\n+define+SIM_MODE\n")
        from deltasim.filelist import expand_filelist_args
        all_files, fl = expand_filelist_args((), (flist,))
        _pp = {"defines": fl.defines or None, "incdirs": fl.incdirs or None}
        di = ds.load(all_files, **{k: v for k, v in _pp.items() if v})
        assert di.status == "PASS"

    def test_incdir_from_filelist(self, tmp_path):
        sub = tmp_path / "hdr"
        sub.mkdir()
        _write(sub, "defs.sv", "`define FLAG 1\n")
        sv = _write(tmp_path, "m.sv", """\
`include "defs.sv"
module m(output logic q);
  assign q = `FLAG;
endmodule
""")
        flist = _write(tmp_path, "files.f",
                       f"{sv}\n+incdir+{str(sub)}\n")
        from deltasim.filelist import expand_filelist_args
        all_files, fl = expand_filelist_args((), (flist,))
        _pp = {"defines": fl.defines or None, "incdirs": fl.incdirs or None}
        di = ds.load(all_files, **{k: v for k, v in _pp.items() if v})
        assert di.status == "PASS"

    def test_cli_overrides_filelist(self, tmp_path):
        """.f の +define+W=4 と CLI の -D W=8 で、CLI が後勝ちしてポート幅が 8 になること。"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic [`W-1:0] d, output logic [`W-1:0] q);
  assign q = d;
endmodule
""")
        flist = _write(tmp_path, "files.f", f"{sv}\n+define+W=4\n")
        import subprocess, sys
        # .f W=4, CLI -D W=8 → CLI 後勝ちで 8
        r_cli_wins = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv,
             "-f", flist, "-D", "W=8"],
            capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        assert r_cli_wins.returncode == 0, r_cli_wins.stderr
        # ポート幅 8 になること（"8" が stdout に含まれる）
        assert "8" in r_cli_wins.stdout, \
            f"CLI -D W=8 が後勝ちしてポート幅8にならない: {r_cli_wins.stdout!r}"

        # .f のみ (W=4): ポート幅 4
        r_flist_only = subprocess.run(
            [sys.executable, "-m", "deltasim", "parse", sv, "-f", flist],
            capture_output=True, text=True, encoding="utf-8", timeout=15
        )
        assert r_flist_only.returncode == 0, r_flist_only.stderr
        assert "4" in r_flist_only.stdout, \
            f".f のみ: ポート幅4にならない: {r_flist_only.stdout!r}"

    def test_yaml_defines_overrides_cli(self, tmp_path):
        """YAML defines と CLI -D の優先順位: CLI が後勝ちしてポート幅が変わること。"""
        sv = _write(tmp_path, "m.sv", """\
module m(input logic clk, rst_n, input logic [`W-1:0] d, output logic [`W-1:0] q);
  always_ff @(posedge clk) if (!rst_n) q <= 0; else q <= d;
endmodule
""")
        # YAML defines W=4
        spec = {
            "version": 1, "top": "m",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 3},
            "defines": ["+define+W=4"],
            "cases": [{"name": "t", "expected": [{"at": 3, "check": {"q": 0}}]}],
        }
        spec_p = str(tmp_path / "spec.yaml")
        with open(spec_p, "w", encoding="utf-8") as f:
            yaml.dump(spec, f)

        # YAML W=4 のみ → ポート幅 4
        di_yaml = ds.load([sv], defines=["+define+W=4"])
        w_yaml = next(p.width for p in di_yaml.ports if p.name == "d")
        assert w_yaml == 4, f"YAML W=4 のみ: {w_yaml}"

        # CLI W=8 が後から追加 → ポート幅は 8（後勝ち）
        di_cli = ds.load([sv], defines=["+define+W=4", "+define+W=8"])
        w_cli = next(p.width for p in di_cli.ports if p.name == "d")
        assert w_cli == 8, f"CLI W=8 後勝ち: {w_cli}"
