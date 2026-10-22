"""tests/test_interactive.py — §18 対話デバッグテスト

- 式パーサ (expr.py): 各演算子・リテラル・壊れた入力
- Tcl インタープリタ (tcl.py): set/if/for/foreach/while/proc/expr
- SimSession (session.py): run/examine/force/noforce/restart/do/bp/when
- eval 廃止確認テスト
- E2E: stdin スクリプト経由（CRLF 含む）
"""
from __future__ import annotations
import os, tempfile
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
COUNTER_SV = os.path.join(EXAMPLES, "counter.sv")


# ===========================================================================
# 1. 式パーサ (expr.py)
# ===========================================================================

class TestExprParser:
    """SV 式サブセットの評価テスト"""

    def _e(self, s, env=None):
        from deltasim.interp.expr import eval_expr
        return eval_expr(s, env or {})

    def test_arithmetic(self):
        assert self._e("3 + 4") == 7
        assert self._e("10 - 3") == 7
        assert self._e("3 * 4") == 12
        assert self._e("10 / 3") == 3
        assert self._e("10 % 3") == 1

    def test_bitwise(self):
        assert self._e("0xF0 & 0xFF") == 0xF0
        assert self._e("0xF0 | 0x0F") == 0xFF
        assert self._e("0xFF ^ 0x0F") == 0xF0
        assert self._e("~0") == -1

    def test_shift(self):
        assert self._e("1 << 3") == 8
        assert self._e("8 >> 2") == 2

    def test_comparison(self):
        assert self._e("3 == 3") == 1
        assert self._e("3 != 4") == 1
        assert self._e("5 > 4")  == 1
        assert self._e("4 >= 4") == 1
        assert self._e("3 < 4")  == 1
        assert self._e("3 <= 3") == 1

    def test_logical(self):
        assert self._e("1 && 1") == 1
        assert self._e("1 && 0") == 0
        assert self._e("0 || 1") == 1
        assert self._e("!0")     == 1

    def test_ternary(self):
        assert self._e("1 ? 10 : 20") == 10
        assert self._e("0 ? 10 : 20") == 20

    def test_parens(self):
        assert self._e("(3 + 4) * 2") == 14

    def test_sv_literals(self):
        from deltasim.interp.expr import eval_expr
        assert eval_expr("4'b1010") == 10
        assert eval_expr("8'hFF")   == 255
        assert eval_expr("4'b0101") == 5
        assert eval_expr("3'd7")    == 7

    def test_x_literal_returns_none(self):
        assert self._e("x") is None

    def test_sv_x_literal(self):
        from deltasim.interp.expr import eval_expr
        result = eval_expr("4'b10x1")
        assert result is None  # x を含む

    def test_bit_select(self):
        assert self._e("a[0]", {"a": 5}) == 1   # 101 → bit0 = 1
        assert self._e("a[1]", {"a": 5}) == 0   # 101 → bit1 = 0
        assert self._e("a[2]", {"a": 5}) == 1   # 101 → bit2 = 1

    def test_part_select(self):
        assert self._e("a[3:0]", {"a": 0xAB}) == 0xB
        assert self._e("a[7:4]", {"a": 0xAB}) == 0xA

    def test_env_lookup(self):
        assert self._e("count + 1", {"count": 9}) == 10

    def test_x_propagation(self):
        """x が含まれると結果は None (x)"""
        assert self._e("x + 5") is None
        assert self._e("5 + x") is None

    def test_strict_equal(self):
        """=== / !== は x/z を含む厳密比較"""
        from deltasim.interp.expr import eval_expr
        assert eval_expr("1 === 1") == 1
        assert eval_expr("1 !== 2") == 1

    def test_broken_input_raises(self):
        """壊れた入力でも有限時間で ExprError を送出すること"""
        from deltasim.interp.expr import eval_expr, ExprError
        with pytest.raises(ExprError):
            eval_expr("")
        with pytest.raises(ExprError):
            eval_expr("(((")
        with pytest.raises(ExprError):
            eval_expr("1 +")
        with pytest.raises(ExprError):
            eval_expr("@@@")

    def test_deeply_nested_does_not_hang(self):
        """深い入れ子でも ExprError を送出すること（無限ループしない）"""
        from deltasim.interp.expr import eval_expr, ExprError, MAX_EXPR_DEPTH
        deep = "(" * (MAX_EXPR_DEPTH + 10) + "1" + ")" * (MAX_EXPR_DEPTH + 10)
        with pytest.raises(ExprError):
            eval_expr(deep)


# ===========================================================================
# 2. Tcl インタープリタ (tcl.py)
# ===========================================================================

class TestTcl:
    """ミニ Tcl インタープリタのテスト"""

    def _tcl(self):
        from deltasim.interp.tcl import MiniTcl
        return MiniTcl()

    def test_set_get(self):
        tcl = self._tcl()
        assert tcl.eval("set x 42") == "42"
        assert tcl.get_var("x") == "42"

    def test_expr(self):
        tcl = self._tcl()
        assert tcl.eval("expr {3 + 4}") == "7"
        assert tcl.eval("expr {10 > 5}") == "1"

    def test_variable_substitution(self):
        tcl = self._tcl()
        tcl.eval("set a 10")
        assert tcl.eval("expr {$a * 2}") == "20"

    def test_if_true(self):
        tcl = self._tcl()
        tcl.eval("if {1} {set r yes} else {set r no}")
        assert tcl.get_var("r") == "yes"

    def test_if_false(self):
        tcl = self._tcl()
        tcl.eval("if {0} {set r yes} else {set r no}")
        assert tcl.get_var("r") == "no"

    def test_for_loop(self):
        tcl = self._tcl()
        tcl.eval("set sum 0; for {set i 1} {expr {$i <= 5}} {incr i} {incr sum $i}")
        assert tcl.get_var("sum") == "15"

    def test_foreach(self):
        tcl = self._tcl()
        tcl.eval("set s 0; foreach v {1 2 3 4 5} {incr s $v}")
        assert tcl.get_var("s") == "15"

    def test_while(self):
        tcl = self._tcl()
        tcl.eval("set n 5; set s 0; while {expr {$n > 0}} {incr s $n; incr n -1}")
        assert tcl.get_var("s") == "15"

    def test_proc(self):
        tcl = self._tcl()
        tcl.eval("proc double {x} {expr {$x * 2}}")
        assert tcl.eval("double 7") == "14"

    def test_command_substitution(self):
        tcl = self._tcl()
        tcl.eval("set result [expr {2 + 3}]")
        assert tcl.get_var("result") == "5"

    def test_brace_quoting_no_substitution(self):
        tcl = self._tcl()
        tcl.eval("set var hello")
        tcl.eval("set raw {$var}")
        assert tcl.get_var("raw") == "$var"  # {} の中は展開しない

    def test_double_quote_substitution(self):
        tcl = self._tcl()
        tcl.eval("set name world")
        tcl.eval('set msg "hello $name"')
        assert tcl.get_var("msg") == "hello world"

    def test_incr(self):
        tcl = self._tcl()
        tcl.eval("set x 5")
        tcl.eval("incr x")
        assert tcl.get_var("x") == "6"
        tcl.eval("incr x 4")
        assert tcl.get_var("x") == "10"

    def test_puts_no_exception(self, capsys):
        tcl = self._tcl()
        tcl.eval('puts "hello world"')
        out = capsys.readouterr().out
        assert "hello world" in out

    def test_loop_limit(self):
        """無限ループで TclError が送出されること"""
        from deltasim.interp.tcl import MiniTcl, TclError
        tcl = MiniTcl(max_loop=10)
        with pytest.raises(TclError, match="ループが"):
            tcl.eval("while {1} {set x 1}")

    def test_semicolon_separator(self):
        tcl = self._tcl()
        result = tcl.eval("set a 1; set b 2; expr {$a + $b}")
        assert result == "3"

    def test_multiline_script(self):
        tcl = self._tcl()
        script = "set x 10\nset y 20\nexpr {$x + $y}"
        assert tcl.eval(script) == "30"

    def test_crlf_script(self):
        """CRLF 改行のスクリプトが正常に動作すること（Windows 対応）"""
        tcl = self._tcl()
        script = "set x 5\r\nset y 3\r\nexpr {$x + $y}"
        assert tcl.eval(script) == "8"

    def test_comment_line(self):
        tcl = self._tcl()
        result = tcl.eval("# this is a comment\nset x 99")
        assert tcl.get_var("x") == "99"


# ===========================================================================
# 3. SimSession コマンドテスト
# ===========================================================================

def _make_session():
    """counter.sv を使ったテスト用 SimSession を作成する。"""
    import deltasim.api as ds
    from deltasim.interp.session import SimSession
    sim = ds.Simulator([COUNTER_SV], top="counter", reset_cycles=2)
    return SimSession(sim), sim


class TestSimSession:
    """SimSession の各コマンドテスト"""

    def test_run_advances_time(self):
        session, sim = _make_session()
        session.eval_script("run 20ns")
        assert sim._engine._time >= 20

    def test_examine_returns_value(self):
        session, sim = _make_session()
        session.eval_script("run 30ns")
        lines = session.eval_script("examine count")
        assert any("count" in l for l in lines)

    def test_examine_radix_bin(self):
        session, sim = _make_session()
        session.eval_script("run 30ns")
        lines = session.eval_script("examine -radix bin count")
        assert any("count" in l for l in lines)
        # binary string should only contain 0, 1
        for l in lines:
            if "count" in l:
                parts = l.split("=")
                if len(parts) > 1:
                    val_str = parts[1].strip()
                    assert all(c in "01" for c in val_str), f"not binary: {val_str!r}"

    def test_examine_radix_hex(self):
        session, sim = _make_session()
        session.eval_script("run 30ns")
        lines = session.eval_script("examine -radix hex count")
        assert any("count" in l for l in lines)

    def test_force_noforce(self):
        session, sim = _make_session()
        session.eval_script("run 20ns; force count 42")
        val = sim.get("count")
        assert val == 42
        session.eval_script("noforce count")
        # after noforce the engine can write count again

    def test_restart_resets_time(self):
        session, sim = _make_session()
        session.eval_script("run 50ns")
        assert sim._engine._time > 0
        session.eval_script("restart")
        assert sim._engine._time == 0

    def test_bp_time(self):
        """bp -time で指定時刻に止まること"""
        session, sim = _make_session()
        session.eval_script("bp -time 30ns")
        session.eval_script("run -all")
        assert sim._engine._time >= 30

    def test_bp_list(self):
        session, sim = _make_session()
        session.eval_script("bp -time 50ns")
        lines = session.eval_script("bp -list")
        assert any("50" in l for l in lines)

    def test_bp_delete(self):
        session, sim = _make_session()
        session.eval_script("bp -time 50ns")
        lines = session.eval_script("bp -list")
        # extract bp ID
        bp_id = None
        for l in lines:
            import re
            m = re.search(r"\[(\d+)\]", l)
            if m:
                bp_id = m.group(1)
        assert bp_id is not None
        session.eval_script(f"bp -delete {bp_id}")
        lines2 = session.eval_script("bp -list")
        assert not any(f"[{bp_id}]" in l for l in lines2)

    def test_when_fires(self):
        """when 条件が満たされたときコマンドが実行されること"""
        session, sim = _make_session()
        # count が 1 になったとき set_when_fired を 1 に
        session.eval_script("when {count == 1} {set was_fired 1}")
        session.eval_script("run 50ns")
        fired = session._tcl.get_var("was_fired")
        assert fired == "1", f"when が発火しなかった: was_fired={fired!r}"

    def test_do_file(self, tmp_path):
        """do ファイルが実行されること"""
        script = "set x 99\n"
        p = tmp_path / "test.do"
        p.write_text(script, encoding="utf-8")
        session, sim = _make_session()
        session.eval_script(f"do {p}")
        assert session._tcl.get_var("x") == "99"

    def test_do_file_crlf(self, tmp_path):
        """CRLF 改行の do ファイルも動作すること"""
        script = "set x 77\r\nset y 88\r\n"
        p = tmp_path / "test_crlf.do"
        p.write_bytes(script.encode("utf-8"))
        session, sim = _make_session()
        session.eval_script(f"do {p}")
        assert session._tcl.get_var("x") == "77"
        assert session._tcl.get_var("y") == "88"

    def test_tcl_loop_in_session(self):
        """Tcl for ループを使って複数サイクル進められること"""
        session, sim = _make_session()
        session.eval_script("for {set i 0} {expr {$i < 3}} {incr i} {run 10ns}")
        assert sim._engine._time >= 30

    def test_compat_step(self):
        """後方互換: step コマンド"""
        session, sim = _make_session()
        session.eval_script("step 2")
        assert sim._engine._time > 0

    def test_compat_signals(self, capsys):
        """後方互換: signals コマンド"""
        session, sim = _make_session()
        session.eval_script("signals")
        # check output contains count
        lines = session._output_lines
        assert any("count" in l for l in lines)


# ===========================================================================
# 4. eval 廃止確認テスト
# ===========================================================================

class TestNoEvalUsed:
    """対話デバッグ関連コードに eval() 呼び出しが無いこと"""

    def test_interp_has_no_builtin_eval(self):
        """deltasim/interp/ に Python 組み込みの eval( / exec( が無いこと。

        MiniTcl.eval() や eval_expr() はメソッド呼び出しなので許可する。
        Python 組み込み eval/exec の直接呼び出し: `eval(`, `exec(` で始まる行が対象。
        """
        import re

        interp_dir = Path(__file__).parent.parent / "deltasim" / "interp"
        violations = []
        for py_file in interp_dir.glob("*.py"):
            src = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(src.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Python 組み込み eval/exec 呼び出し:
                #   eval(...) や exec(...) がインデントの後に来る場合
                #   ただし .eval( や _eval( や eval_expr( は除く
                if re.search(r'(?<![._a-zA-Z0-9])eval\s*\(', stripped) and \
                   not re.search(r'def eval\b', stripped):
                    # MiniTcl.eval や _eval_bool などのメソッドでないことを確認
                    # 呼び出し元が self. / interp. / tcl. / session. であれば除外
                    if not re.search(r'(?:self|interp|tcl|session|_tcl)\s*\.\s*eval\s*\(', stripped):
                        violations.append(f"{py_file.name}:{i}: {stripped!r}")
                if re.search(r'(?<![._a-zA-Z0-9])exec\s*\(', stripped):
                    violations.append(f"{py_file.name}:{i}: {stripped!r}")
        assert not violations, \
            "interp/ に Python 組み込み eval/exec が使用されています:\n" + "\n".join(violations)

    def test_sim_repl_has_no_eval(self):
        """cli.py の sim/run -i 部分に eval( が無いこと（interp 経由を除く）"""
        import re
        cli_path = Path(__file__).parent.parent / "deltasim" / "cli.py"
        src = cli_path.read_text(encoding="utf-8")
        violations = []
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r'\beval\s*\(', stripped):
                violations.append(f"cli.py:{i}: {stripped!r}")
        assert not violations, \
            "cli.py に eval が使用されています:\n" + "\n".join(violations)


# ===========================================================================
# 5. E2E テスト（CLI 経由の stdin スクリプト）
# ===========================================================================

class TestE2ECli:
    """CLI 経由で stdin からスクリプトを流す E2E テスト"""

    def _run_cli_script(self, sv_path: str, script: str):
        """CLI を通じてスクリプトを実行し (exit_code, stdout, stderr) を返す。"""
        import subprocess, sys, shutil
        # deltasim コマンドを探す（PATH から、または pip インストール済み）
        cmd = shutil.which("deltasim")
        if cmd is None:
            # フォールバック: CliRunner を使う（サブプロセス不使用）
            return self._run_cli_runner(sv_path, script)
        proc = subprocess.run(
            [cmd, "sim", sv_path],
            input=script,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _run_cli_runner(self, sv_path: str, script: str):
        """CliRunner 経由（deltasim コマンドが PATH にない場合のフォールバック）。"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        import io
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(cli_main, ["sim", sv_path], input=script)
        return result.exit_code, result.output, result.stderr if hasattr(result, "stderr") else ""

    def test_run_and_examine(self):
        """run + examine が stdout に出力されること"""
        script = "run 30ns\nexamine count\nquit\n"
        rc, out, err = self._run_cli_script(COUNTER_SV, script)
        assert rc == 0, f"exit_code={rc}\nstdout={out}\nstderr={err}"
        assert "count" in out

    def test_crlf_script(self):
        """CRLF 改行のスクリプトも動作すること"""
        script = "run 20ns\r\nexamine count\r\nquit\r\n"
        rc, out, err = self._run_cli_script(COUNTER_SV, script)
        assert rc == 0
        assert "count" in out

    def test_tcl_variable_and_loop(self):
        """Tcl 変数とループを使ったスクリプトが動作すること"""
        script = (
            "set n 0\n"
            "for {set i 0} {expr {$i < 3}} {incr i} {run 10ns; incr n}\n"
            "puts $n\n"
            "quit\n"
        )
        rc, out, err = self._run_cli_script(COUNTER_SV, script)
        assert rc == 0, f"exit_code={rc}\nstdout={out}\nstderr={err}"
        assert "3" in out

    def test_force_and_examine(self):
        """force で値を設定して examine で確認できること"""
        script = "run 30ns\nforce count 42\nexamine count\nquit\n"
        rc, out, err = self._run_cli_script(COUNTER_SV, script)
        assert rc == 0
        # 42 or 0x2A or 00101010 が出力されること
        assert any(x in out for x in ["42", "2A", "00101010"]), \
            f"42 が出力に含まれない: {out!r}"

    def test_restart(self):
        """restart でリセットできること"""
        script = "run 50ns\nrestart\nexamine count\nquit\n"
        rc, out, err = self._run_cli_script(COUNTER_SV, script)
        assert rc == 0

    def test_run_i_flag(self):
        """`deltasim run -i` も同じように動作すること"""
        import subprocess, shutil
        script = "run 20ns\nexamine count\nquit\n"
        cmd = shutil.which("deltasim")
        if cmd is None:
            from click.testing import CliRunner
            from deltasim.cli import main as cli_main
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(cli_main, ["run", "-i", COUNTER_SV], input=script)
            assert result.exit_code == 0, f"exit_code={result.exit_code}\n{result.output}"
            assert "count" in result.output
            return
        proc = subprocess.run(
            [cmd, "run", "-i", COUNTER_SV],
            input=script, capture_output=True, text=True, timeout=15,
        )
        assert proc.returncode == 0
        assert "count" in proc.stdout

    def test_when_in_script(self):
        """when コマンドが発火すること（SimSession.eval_script 経由で確認）"""
        # CliRunner の出力キャプチャと puts の相互作用に依存しないよう
        # eval_script を直接使う
        session, sim = _make_session()
        session.eval_script("when {count != 0} {puts WHEN_FIRED}")
        session.eval_script("run 100ns")
        # WHEN_FIRED が出力されていること
        all_out = "\n".join(session._output_lines)
        assert "WHEN_FIRED" in all_out, \
            f"when が発火しなかった: output_lines={session._output_lines}"

    def test_do_file(self, tmp_path):
        """do ファイルが実行されること"""
        do_script = "run 20ns\nputs DO_DONE\n"
        do_p = tmp_path / "test.do"
        do_p.write_text(do_script, encoding="utf-8")
        script = f"do {do_p}\nquit\n"
        rc, out, err = self._run_cli_script(COUNTER_SV, script)
        assert rc == 0
        assert "DO_DONE" in out
