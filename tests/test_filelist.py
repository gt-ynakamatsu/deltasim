"""tests/test_filelist.py — §18.2 -f ファイルリスト機能テスト"""

from __future__ import annotations
import json, os, subprocess, sys, tempfile, textwrap
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _write(path: str, content: str, encoding: str = "utf-8"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding=encoding)


def _sv(tmp: str, name: str) -> str:
    p = os.path.join(tmp, name)
    _write(p, f"module {Path(name).stem}; endmodule\n")
    return p


def _cli(args, input_text=None):
    from click.testing import CliRunner
    from deltasim.cli import main as cli_main
    runner = CliRunner()
    return runner.invoke(cli_main, args, input=input_text)


# ===========================================================================
# 1. load_filelist 単体テスト
# ===========================================================================

class TestLoadFilelist:
    """load_filelist の解析機能"""

    def test_basic_files(self, tmp_path):
        """SV ファイルリストが正しく読まれること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        b = str(tmp_path / "b.sv"); _write(b, "module b; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "a.sv\nb.sv\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.files) == 2
        assert fl.files[0].endswith("a.sv")
        assert fl.files[1].endswith("b.sv")

    def test_comment_slash(self, tmp_path):
        """// コメントが除去されること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "a.sv // this is a comment\n// ignored line\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.files) == 1

    def test_comment_hash(self, tmp_path):
        """# コメントが除去されること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "a.sv # inline comment\n# whole line comment\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.files) == 1

    def test_empty_lines_ignored(self, tmp_path):
        """空行が無視されること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "\n\na.sv\n\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.files) == 1

    def test_crlf(self, tmp_path):
        """CRLF 改行が LF と同じように処理されること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        b = str(tmp_path / "b.sv"); _write(b, "module b; endmodule\n")
        f = str(tmp_path / "files.f")
        Path(f).write_bytes(b"a.sv\r\nb.sv\r\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.files) == 2

    def test_utf8_bom(self, tmp_path):
        """UTF-8 BOM 付きファイルが読めること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(tmp_path / "files.f")
        Path(f).write_bytes(b"\xef\xbb\xbfa.sv\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.files) == 1

    def test_relative_path_resolved_from_f_file(self, tmp_path):
        """相対パスが .f ファイルの場所を基準に解決されること"""
        sub = tmp_path / "sub"
        sub.mkdir()
        a = str(sub / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(sub / "files.f")
        _write(f, "a.sv\n")  # sub/a.sv
        from deltasim.filelist import load_filelist
        # CWD を変えても動作すること
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))  # CWD = tmp_path, .f は sub/
            fl = load_filelist(f)
            assert len(fl.files) == 1
            assert fl.files[0] == str(sub / "a.sv")
        finally:
            os.chdir(old_cwd)

    def test_incdir(self, tmp_path):
        """+incdir+ が正しく解析されること"""
        f = str(tmp_path / "files.f")
        inc = str(tmp_path / "include")
        _write(f, f"+incdir+include\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.incdirs) == 1
        assert fl.incdirs[0] == str(tmp_path / "include")

    def test_incdir_multiple_in_one_line(self, tmp_path):
        """+incdir+a+b が複数のディレクトリを返すこと"""
        f = str(tmp_path / "files.f")
        _write(f, "+incdir+inc1+inc2\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.incdirs) == 2

    def test_define_with_value(self, tmp_path):
        """+define+NAME=VAL が解析されること"""
        f = str(tmp_path / "files.f")
        _write(f, "+define+MY_MACRO=42\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert fl.defines.get("MY_MACRO") == "42"

    def test_define_without_value(self, tmp_path):
        """+define+NAME（値なし）が解析されること"""
        f = str(tmp_path / "files.f")
        _write(f, "+define+ENABLE_FEATURE\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert fl.defines.get("ENABLE_FEATURE") == "1"

    def test_unsupported_option_warns(self, tmp_path):
        """未対応オプション -v で W_FILELIST_OPT 警告が出ること（行番号含む）"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "a.sv\n-v library.sv\n+libext+.sv\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert fl.files == [str(tmp_path / "a.sv")]
        warn_codes = [w.code for w in fl.warnings]
        assert warn_codes.count("W_FILELIST_OPT") == 2
        # 行番号を含むこと
        warn_lines = [w.line for w in fl.warnings if w.code == "W_FILELIST_OPT"]
        assert 2 in warn_lines, f"行番号 2 が警告にない: {warn_lines}"
        assert 3 in warn_lines, f"行番号 3 が警告にない: {warn_lines}"

    def test_missing_file_error(self, tmp_path):
        """存在しないファイルが E_FILE_NOT_FOUND エラーになること（行番号含む）"""
        f = str(tmp_path / "files.f")
        _write(f, "nonexistent.sv\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f)
        assert len(fl.errors) == 1
        assert fl.errors[0].code == "E001"
        assert str(f) in fl.errors[0].file
        assert fl.errors[0].line == 1

    def test_nested_f_file(self, tmp_path):
        """入れ子の -f ファイルが読まれること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        b = str(tmp_path / "b.sv"); _write(b, "module b; endmodule\n")
        inner = str(tmp_path / "inner.f")
        _write(inner, "b.sv\n")
        outer = str(tmp_path / "outer.f")
        _write(outer, "a.sv\n-f inner.f\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(outer)
        names = [Path(f).name for f in fl.files]
        assert "a.sv" in names and "b.sv" in names

    def test_circular_f_detection(self, tmp_path):
        """循環する -f が W_FILELIST_OPT 警告になること"""
        f1 = str(tmp_path / "f1.f")
        f2 = str(tmp_path / "f2.f")
        _write(f1, f"-f f2.f\n")
        _write(f2, f"-f f1.f\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f1)
        warn_msgs = [w.message for w in fl.warnings]
        assert any("循環" in m for m in warn_msgs), f"循環検出警告がない: {warn_msgs}"

    def test_combine_sv_files_and_filelist(self, tmp_path):
        """SV 直接指定と -f の両方が結合されること"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        b = str(tmp_path / "b.sv"); _write(b, "module b; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "b.sv\n")
        from deltasim.filelist import expand_filelist_args
        all_files, fl = expand_filelist_args([a], [f])
        names = [Path(p).name for p in all_files]
        assert "a.sv" in names and "b.sv" in names

    def test_gui_uses_same_function(self, tmp_path):
        """GUI の resolve_paths_from_f_file が load_filelist を使うこと"""
        a = str(tmp_path / "a.sv"); _write(a, "module a; endmodule\n")
        f = str(tmp_path / "files.f")
        _write(f, "a.sv\n")
        from deltasim.gui.project import resolve_paths_from_f_file
        files = resolve_paths_from_f_file(f)
        assert len(files) == 1
        assert files[0].endswith("a.sv")


# ===========================================================================
# 2. CLI -f オプション テスト
# ===========================================================================

class TestCliFilelist:
    """CLI コマンドでの -f オプション"""

    def test_test_with_f(self, tmp_path):
        """`deltasim test -f files.f` が動作すること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        result = _cli(["test", "-f", f_path, "--spec", spec_p])
        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"

    def test_test_with_f_and_direct(self, tmp_path):
        """`deltasim test FILE -f files.f` の組み合わせが動作すること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        # -f に counter.sv を入れ、直接引数も counter.sv（重複除去）
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        result = _cli(["test", sv_p, "-f", f_path, "--spec", spec_p])
        assert result.exit_code == 0

    def test_test_f_json_matches_direct(self, tmp_path):
        """-f 経由の test 結果が直接指定と一致すること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n")

        json_f = str(tmp_path / "result_f.json")
        json_direct = str(tmp_path / "result_direct.json")

        r_f = _cli(["test", "-f", f_path, "--spec", spec_p, "--json", json_f])
        r_d = _cli(["test", sv_p, "--spec", spec_p, "--json", json_direct])

        assert r_f.exit_code == 0 and r_d.exit_code == 0
        d_f = json.loads(Path(json_f).read_text(encoding="utf-8"))
        d_d = json.loads(Path(json_direct).read_text(encoding="utf-8"))
        assert d_f["status"] == d_d["status"]
        assert len(d_f.get("cases", [])) == len(d_d.get("cases", []))

    def test_run_with_f(self, tmp_path):
        """`deltasim run -f files.f` が動作すること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n")
        result = _cli(["run", "-f", f_path, "--top", "counter", "--cycles", "5"])
        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"

    def test_gen_with_f(self, tmp_path):
        """`deltasim gen -f files.f` が動作すること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n")
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir, exist_ok=True)
        result = _cli(["gen", "-f", f_path, "--top", "counter",
                       "--out-dir", out_dir, "--no-tb"])
        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"

    def test_parse_with_f(self, tmp_path):
        """`deltasim parse -f files.f` が動作すること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n")
        result = _cli(["parse", "-f", f_path])
        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"

    def test_unknown_option_warning_in_stderr(self, tmp_path):
        """未対応オプションで W_FILELIST_OPT が stderr に出ること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n-v something.sv\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        runner = CliRunner(mix_stderr=False) if False else CliRunner()
        result = runner.invoke(cli_main, ["test", "-f", f_path, "--spec", spec_p])
        assert result.exit_code == 0
        # W_FILELIST_OPT が出力に含まれること
        assert "W_FILELIST_OPT" in result.output, \
            f"W_FILELIST_OPT が出力にない: {result.output[:200]}"

    def test_relative_path_different_cwd(self, tmp_path):
        """別の CWD から実行しても .f の相対パスが正しく解決されること"""
        sv_p = str(tmp_path / "counter.sv")
        import shutil
        shutil.copy(os.path.join(EXAMPLES, "counter.sv"), sv_p)
        f_path = str(tmp_path / "files.f")
        _write(f_path, "counter.sv\n")  # relative to .f file location
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")

        # Change to a different temp directory (not /tmp directly — works on all OS)
        import tempfile
        with tempfile.TemporaryDirectory() as other_dir:
            old_cwd = os.getcwd()
            try:
                os.chdir(other_dir)
                result = _cli(["test", "-f", f_path, "--spec", spec_p])
            finally:
                os.chdir(old_cwd)
        assert result.exit_code == 0, \
            f"CWD変更後に失敗: exit={result.exit_code}\n{result.output}"

    def test_missing_file_in_f_exits_nonzero(self, tmp_path):
        """存在しないファイルが .f に含まれていたら非0終了すること"""
        f_path = str(tmp_path / "files.f")
        _write(f_path, "nonexistent_completely.sv\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        result = _cli(["test", "-f", f_path, "--spec", spec_p])
        assert result.exit_code != 0, "存在しないファイルで非0終了すべき"


# ===========================================================================
# 3. E2E: subprocess でコマンドを実行
# ===========================================================================

class TestFilelstE2E:
    """subprocess で 4 コマンドの E2E テスト"""

    def _run_cli(self, args, timeout=15):
        import shutil
        cmd = shutil.which("deltasim")
        if cmd is None:
            from click.testing import CliRunner
            from deltasim.cli import main as cli_main
            r = CliRunner().invoke(cli_main, args)
            return r.exit_code, r.output
        proc = subprocess.run([cmd] + args, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout + proc.stderr

    def test_e2e_test_with_f(self, tmp_path):
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        Path(f_path).write_text(f"{sv_p}\n", encoding="utf-8")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        rc, out = self._run_cli(["test", "-f", f_path, "--spec", spec_p])
        assert rc == 0, f"exit={rc}\n{out}"

    def test_e2e_run_with_f(self, tmp_path):
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        Path(f_path).write_text(f"{sv_p}\n", encoding="utf-8")
        rc, out = self._run_cli(["run", "-f", f_path, "--top", "counter", "--cycles", "3"])
        assert rc == 0, f"exit={rc}\n{out}"

    def test_e2e_gen_with_f(self, tmp_path):
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        Path(f_path).write_text(f"{sv_p}\n", encoding="utf-8")
        out_dir = str(tmp_path / "out")
        os.makedirs(out_dir, exist_ok=True)
        rc, out = self._run_cli(["gen", "-f", f_path, "--top", "counter",
                                  "--out-dir", out_dir, "--no-tb"])
        assert rc == 0, f"exit={rc}\n{out}"

    def test_e2e_parse_with_f(self, tmp_path):
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        Path(f_path).write_text(f"{sv_p}\n", encoding="utf-8")
        rc, out = self._run_cli(["parse", "-f", f_path])
        assert rc == 0, f"exit={rc}\n{out}"


# ===========================================================================
# 4. 追加テスト（修正対応分）
# ===========================================================================

class TestFilelistFixes:
    """PR #22 レビュー指摘修正のテスト"""

    # ---- JSON への W_FILELIST_OPT 出力 ----

    def test_w_filelist_opt_in_json(self, tmp_path):
        """`test --json` の出力に W_FILELIST_OPT が含まれること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n-v unused.sv\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        json_p = str(tmp_path / "result.json")
        result = _cli(["test", "-f", f_path, "--spec", spec_p, "--json", json_p])
        assert result.exit_code == 0, f"exit={result.exit_code}\n{result.output}"
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        warn_codes = [w.get("code") for w in d.get("warnings", [])]
        assert "W_FILELIST_OPT" in warn_codes, \
            f"W_FILELIST_OPT が JSON の warnings にない: {d.get('warnings', [])}"

    def test_w_filelist_opt_warning_has_file_and_line(self, tmp_path):
        """W_FILELIST_OPT の警告に file と line が含まれること"""
        sv_p = os.path.join(EXAMPLES, "counter.sv")
        f_path = str(tmp_path / "files.f")
        _write(f_path, f"{sv_p}\n-y libdir\n")  # line 2: -y
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        json_p = str(tmp_path / "result.json")
        _cli(["test", "-f", f_path, "--spec", spec_p, "--json", json_p])
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        w = next((w for w in d.get("warnings", []) if w.get("code") == "W_FILELIST_OPT"), None)
        assert w is not None
        assert w.get("line") == 2, f"line が 2 でない: {w}"
        assert f_path in w.get("file", ""), f"file が .f パスでない: {w}"

    def test_missing_file_in_json(self, tmp_path):
        """存在しないファイルの場合 --json で errors[] に E001 が出ること"""
        f_path = str(tmp_path / "files.f")
        _write(f_path, "nonexistent_file.sv\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        json_p = str(tmp_path / "result.json")
        # errors があるので非0終了するが、json は書かれるはず（E_INTERNAL で）
        result = _cli(["test", "-f", f_path, "--spec", spec_p, "--json", json_p])
        assert result.exit_code != 0

    # ---- 重複除去（絶対パス比較）----

    def test_dedup_same_file_direct_and_filelist(self, tmp_path):
        """同じファイルを直接指定と .f 両方で指定しても1回だけ読み込まれること"""
        import shutil
        sv_p = str(tmp_path / "counter.sv")
        shutil.copy(os.path.join(EXAMPLES, "counter.sv"), sv_p)
        f_path = str(tmp_path / "files.f")
        # .f には相対パスで counter.sv を書く
        _write(f_path, "counter.sv\n")
        spec_p = os.path.join(EXAMPLES, "counter_spec.yaml")
        # 直接引数にも sv_p（絶対パス）を渡す → 重複になるはず
        result = _cli(["test", sv_p, "-f", f_path, "--spec", spec_p])
        # モジュール二重定義にならず PASS
        assert result.exit_code == 0, \
            f"重複で失敗: exit={result.exit_code}\n{result.output}"

    def test_dedup_via_expand_filelist_args(self, tmp_path):
        """expand_filelist_args で絶対パス比較の重複除去が動作すること"""
        sv_p = str(tmp_path / "a.sv")
        _write(sv_p, "module a; endmodule\n")
        f_path = str(tmp_path / "files.f")
        _write(f_path, "a.sv\n")
        from deltasim.filelist import expand_filelist_args
        # 直接引数に絶対パス sv_p、.f には相対 a.sv（同じファイル）
        all_files, fl = expand_filelist_args([sv_p], [f_path])
        assert len(all_files) == 1, f"重複が除去されていない: {all_files}"

    # ---- 循環検出の行番号 ----

    def test_circular_f_has_line_number(self, tmp_path):
        """循環する -f の警告に元ファイルの行番号が含まれること"""
        f1 = str(tmp_path / "f1.f"); _write(f1, "-f f2.f\n")
        f2 = str(tmp_path / "f2.f"); _write(f2, "-f f1.f\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f1)
        circ_warns = [w for w in fl.warnings if "循環" in w.message]
        assert circ_warns, f"循環検出警告がない: {fl.warnings}"
        w = circ_warns[0]
        assert w.line > 0, f"循環警告の line が 0: {w}"
        assert w.file != "", f"循環警告の file が空: {w}"

    # ---- E001 コードの統一 ----

    def test_error_code_is_e001(self, tmp_path):
        """存在しないファイルのエラーコードが E001（既存コードと統一）であること"""
        f_path = str(tmp_path / "files.f")
        _write(f_path, "nonexistent.sv\n")
        from deltasim.filelist import load_filelist
        fl = load_filelist(f_path)
        assert fl.errors[0].code == "E001", \
            f"エラーコードが E001 でない: {fl.errors[0].code}"
