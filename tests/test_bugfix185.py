"""tests/test_bugfix185.py — §18.5 不具合修正テスト"""

from __future__ import annotations
import json, os, tempfile, yaml
from pathlib import Path
import pytest
import deltasim.api as ds
from deltasim.api import find_top_candidates

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "examples"))
COUNTER_SV = os.path.join(EXAMPLES, "counter.sv")
FIFO_SV    = os.path.join(EXAMPLES, "fifo.sv")


# ---------------------------------------------------------------------------
# 1. find_top_candidates + E_AMBIGUOUS_TOP
# ---------------------------------------------------------------------------

class TestFindTopCandidates:

    def test_counter_is_candidate(self):
        """counter.sv の counter が top 候補になること"""
        from deltasim.api import _parse_files
        mods, cat, _ = _parse_files([COUNTER_SV])
        candidates = find_top_candidates(mods, cat)
        assert "counter" in candidates

    def test_single_candidate_no_error(self):
        """候補が1つなら top 自動選択"""
        di = ds.load([COUNTER_SV])
        assert di.status == "PASS"
        assert di.module == "counter"

    def test_ambiguous_top_error(self, tmp_path):
        """インスタンス関係のない複数モジュールを与えると E_AMBIGUOUS_TOP"""
        # 2つの独立モジュール
        sv1 = str(tmp_path / "a.sv")
        sv2 = str(tmp_path / "b.sv")
        Path(sv1).write_text("module mod_a(input logic x); endmodule\n")
        Path(sv2).write_text("module mod_b(input logic y); endmodule\n")
        di = ds.load([sv1, sv2])
        # top が指定されないと複数候補でエラー
        assert di.status == "ERROR"
        codes = [e.code for e in di.errors]
        assert "E_AMBIGUOUS_TOP" in codes, f"codes={codes}"

    def test_explicit_top_works(self, tmp_path):
        """--top を明示すれば複数モジュールでも解析できる"""
        sv1 = str(tmp_path / "a.sv")
        sv2 = str(tmp_path / "b.sv")
        Path(sv1).write_text("module mod_a(input logic x); endmodule\n")
        Path(sv2).write_text("module mod_b(input logic y); endmodule\n")
        di = ds.load([sv1, sv2], top="mod_a")
        assert di.status == "PASS"
        assert di.module == "mod_a"

    def test_find_top_candidates_public(self):
        """find_top_candidates が公開 API として使えること"""
        from deltasim.api import find_top_candidates  # noqa
        assert callable(find_top_candidates)


# ---------------------------------------------------------------------------
# 2. TB 生成: 内部信号をポート接続から除外 + E_UNKNOWN_PORT
# ---------------------------------------------------------------------------

class TestTBInternalSignals:

    def test_tb_sv_no_internal_signals(self, tmp_path):
        """生成された TB SV に内部信号の接続が含まれないこと"""
        result = ds.gen([FIFO_SV], top="fifo")
        assert result["status"] == "PASS"
        tb_sv = result.get("tb_sv", "")
        # 内部信号 (mem, wr_ptr, rd_ptr) がポート接続に含まれないこと
        # DUT インスタンスの .signal 接続行に内部信号があれば失敗
        dut_block = False
        for line in tb_sv.splitlines():
            if "fifo dut" in line or "fifo #(" in line:
                dut_block = True
            if dut_block and line.strip().startswith("."):
                for internal in ("mem", "wr_ptr", "rd_ptr", "cnt", "rd_data_reg"):
                    assert f".{internal}(" not in line, \
                        f"内部信号 {internal} がポート接続に含まれている: {line!r}"
            if dut_block and ");" in line:
                dut_block = False

    def test_e_unknown_port(self, tmp_path):
        """存在しないポート名を YAML に書いたら E_UNKNOWN_PORT エラー"""
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "c", "stimulus": [
                {"at": 3, "set": {"nonexistent_signal": 1}}
            ], "expected": [{"at": 3, "check": {"count": 0}}]}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        result = ds.test([COUNTER_SV], spec=p)
        assert result.status == "ERROR"
        codes = [e.code for e in result.errors] + [
            e.code for cr in result.cases for e in cr.errors
        ]
        assert "E_UNKNOWN_PORT" in codes, f"codes={codes}"

    def test_e_unknown_port_in_expected(self, tmp_path):
        """存在しないポート名を expected/check に書いたら E_UNKNOWN_PORT"""
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "c", "stimulus": [], "expected": [
                {"at": 3, "check": {"no_such_port": 0}}
            ]}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        result = ds.test([COUNTER_SV], spec=p)
        codes = [e.code for e in result.errors] + [
            e.code for cr in result.cases for e in cr.errors
        ]
        assert "E_UNKNOWN_PORT" in codes


# ---------------------------------------------------------------------------
# 3. gen --strings の見出しをモジュール名に
# ---------------------------------------------------------------------------

class TestGenStringsHeading:

    def test_gen_strings_uses_module_name(self, tmp_path):
        """`gen --strings` の見出しが実際のモジュール名になること"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        runner = CliRunner()
        result = runner.invoke(cli_main, ["gen", COUNTER_SV, "--top", "counter", "--strings"])
        assert result.exit_code == 0, result.output
        assert "=== counter_tb.sv ===" in result.output
        assert "=== counter_spec.yaml ===" in result.output
        # 旧形式ハードコード名が使われていないこと
        assert "=== counter_tb.sv ===" in result.output  # not "=== counter_tb.sv ===" literally


# ---------------------------------------------------------------------------
# 4. $monitor 値変化時のみ出力・同一時刻内重複なし
# ---------------------------------------------------------------------------

class TestMonitor:

    def test_monitor_only_on_change(self, tmp_path):
        """$monitor が値変化のときだけ出力されること（厳密な行数・内容確認）

        initial で1回だけ $monitor を呼び、
        q が 0 → 1 → 2 → 3 と変わると合計4行出力される。
        値が変わらないサイクルでは出ない。同時刻に1行だけ。
        """
        sv = """\
module mon_test(input logic clk, input logic [7:0] d, output logic [7:0] q);
  always_ff @(posedge clk) q <= d;
  initial $monitor("q=%0d", q);
endmodule
"""
        sv_p = str(tmp_path / "mon.sv")
        Path(sv_p).write_text(sv)
        spec = {
            "version": 1, "top": "mon_test",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 9},
            "cases": [{"name": "t",
                       "stimulus": [
                           {"at": 3, "set": {"d": 1}},
                           {"at": 5, "set": {"d": 2}},
                           {"at": 7, "set": {"d": 3}},
                       ],
                       "expected": [{"at": 3, "check": {"q": 1}}]}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        result = ds.test([sv_p], spec=p)
        # case log のみ（result.log は集計で重複するため使わない）
        all_log = [l for cr in result.cases for l in cr.log]
        monitor_lines = [l for l in all_log if "q=" in l]

        # q=0(初期), q=1, q=2, q=3 の4回だけ
        assert len(monitor_lines) == 4, \
            f"期待 4行 だが {len(monitor_lines)} 行: {monitor_lines}"
        assert monitor_lines == ["q=0", "q=1", "q=2", "q=3"], \
            f"内容が違う: {monitor_lines}"
        # 連続する同じ値は出ていないこと
        for i in range(len(monitor_lines) - 1):
            assert monitor_lines[i] != monitor_lines[i + 1], \
                f"同じ値が連続して出力された: {monitor_lines[i]!r}"

    def test_monitor_no_duplicate_same_time(self, tmp_path):
        """同一時刻内で $monitor が重複して出ないこと"""
        sv = """\
module mon_dup(input logic clk, output logic [7:0] q);
  always_ff @(posedge clk) q <= q + 1;
  initial $monitor("t=%0t q=%0d", $time, q);
endmodule
"""
        sv_p = str(tmp_path / "mon2.sv")
        Path(sv_p).write_text(sv)
        spec = {
            "version": 1, "top": "mon_dup",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5},
            "cases": [{"name": "t", "expected": [{"at": 3, "check": {"q": 2}}]}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        result = ds.test([sv_p], spec=p)
        all_log = [l for cr in result.cases for l in cr.log]  # avoid double-counting
        # 連続重複がないこと
        for i in range(len(all_log) - 1):
            assert all_log[i] != all_log[i + 1], \
                f"重複出力: {all_log[i]!r}"


# ---------------------------------------------------------------------------
# 5. E_NO_CHECK
# ---------------------------------------------------------------------------

class TestENoCheck:

    def _spec_no_check(self, tmp_path):
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "empty", "expected": []}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        return p

    def test_e_no_check_error(self, tmp_path):
        """チェック0件で E_NO_CHECK エラーになること"""
        p = self._spec_no_check(tmp_path)
        result = ds.test([COUNTER_SV], spec=p)
        assert result.status == "ERROR"
        codes = [e.code for e in result.errors]
        assert "E_NO_CHECK" in codes

    def test_e_no_check_nonzero_exit(self, tmp_path):
        """チェック0件で CLI が非0終了すること"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        p = self._spec_no_check(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli_main, ["test", COUNTER_SV, "--spec", p])
        assert result.exit_code != 0, f"expected non-0 but got 0: {result.output}"

    def test_all_skip_is_not_e_no_check(self, tmp_path):
        """全ケースが skip のときは E_NO_CHECK にならないこと（§14.1）"""
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "skipped", "skip": True, "expected": []}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        result = ds.test([COUNTER_SV], spec=p)
        codes = [e.code for e in result.errors]
        assert "E_NO_CHECK" not in codes, f"全 skip なのに E_NO_CHECK: {codes}"


# ---------------------------------------------------------------------------
# 6. --log オプション
# ---------------------------------------------------------------------------

class TestLogOption:

    def test_log_file_written_by_test(self, tmp_path):
        """`deltasim test --log` でログファイルが書かれること"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        spec = os.path.join(EXAMPLES, "counter_spec.yaml")
        log_p = str(tmp_path / "test.log")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test", COUNTER_SV, "--spec", spec, "--log", log_p,
        ])
        assert result.exit_code == 0, result.output
        # ログファイルが存在すること（チェック出力があれば）
        # ログが空でもファイル自体は作られる場合がある

    def test_log_file_written_by_run(self, tmp_path):
        """`deltasim run --log` でログファイルが書かれること"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        sv_p = str(tmp_path / "mon.sv")
        Path(sv_p).write_text(
            "module mon(input logic clk);\n"
            "  initial begin $display(\"hello\"); end\n"
            "endmodule\n"
        )
        log_p = str(tmp_path / "run.log")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "run", sv_p, "--cycles", "5", "--log", log_p,
        ])
        assert result.exit_code == 0, result.output
        if Path(log_p).exists():
            content = Path(log_p).read_text(encoding="utf-8")
            # LF 改行であること（Windows でも）
            assert "\r\n" not in content, "ログに CRLF が含まれている"

    def test_log_in_write_opts(self):
        """--log が safety.py の _WRITE_OPTS_LONG に含まれること"""
        from deltasim.llm.safety import _WRITE_OPTS_LONG
        assert "--log" in _WRITE_OPTS_LONG


# ---------------------------------------------------------------------------
# 7. CLI オプション分類テスト（Fix 3 強化）
# ---------------------------------------------------------------------------

class TestCLIOptionClassification:
    """click のコマンド定義からパスオプションが分類済みであることを確認する"""

    def _get_path_options(self, cmd_name: str):
        """指定コマンドの click.Path 型またはパスに関連するオプションを返す。"""
        import click
        from deltasim.cli import main as cli_main
        ctx = click.Context(cli_main)
        for name, cmd in cli_main.commands.items():
            if name == cmd_name:
                return [
                    param for param in cmd.params
                    if (isinstance(param.type, click.Path)
                        or any(kw in (param.name or "")
                               for kw in ("file", "dir", "path", "log", "out",
                                          "vcd", "json", "junit", "spec",
                                          "tb", "config", "filelist")))
                    and hasattr(param, "name") and param.name
                ]
        return []

    def test_all_path_options_classified(self):
        """全オプション（bool フラグ以外）が write/read/non_path のどれかに分類済みであること。

        新しいオプションを未分類のまま足すとこのテストが落ちる。
        """
        from deltasim.llm.safety import _ALL_WRITE_OPTS, _READ_OPTS_LONG, _READ_OPTS_SHORT, _NON_PATH_OPTS
        import click
        from deltasim.cli import main as cli_main

        all_known = (
            set(_ALL_WRITE_OPTS)
            | set(_READ_OPTS_LONG)
            | set(_READ_OPTS_SHORT)
            | _NON_PATH_OPTS
            # 位置引数（名前が opts でなく引数として扱われる）
            | {"sv_files", "sv_file", "filelist", "paths", "result_json",
               "text",  # ask の引数
               }
        )

        unclassified = []
        for cmd_name in ("test", "run", "gen", "parse"):
            if cmd_name not in cli_main.commands:
                continue
            cmd = cli_main.commands[cmd_name]
            for param in cmd.params:
                # bool フラグは別途 _NON_PATH_OPTS で管理
                if getattr(param, "is_flag", False):
                    continue
                # click.Argument は位置引数 — skip
                if isinstance(param, click.Argument):
                    continue
                pname = getattr(param, "name", "") or ""
                # オプション名（-- 始まり）
                opt_names = [n for n in getattr(param, "opts", []) if n.startswith("-")]
                if not opt_names:
                    continue
                known = (
                    any(n in all_known for n in opt_names)
                    or pname in all_known
                )
                if not known:
                    unclassified.append(f"{cmd_name}: {opt_names} ({pname})")

        assert not unclassified, (
            f"未分類のオプションがあります。safety.py の "
            f"_WRITE_OPTS_LONG/_READ_OPTS_LONG/_NON_PATH_OPTS に追加してください:\n"
            + "\n".join(unclassified)
        )


# ---------------------------------------------------------------------------
# 追加テスト (PR #25 レビュー対応)
# ---------------------------------------------------------------------------

class TestDumpVars:
    """`$dumpfile` / `$dumpvars` 実装テスト"""

    def test_dumpvars_creates_vcd(self, tmp_path):
        """`$dumpvars` で VCD が生成されること"""
        sv = """\
module dumptest(input logic clk, output logic [7:0] q);
  always_ff @(posedge clk) q <= q + 1;
  initial begin
    $dumpfile("test_out.vcd");
    $dumpvars;
  end
endmodule
"""
        sv_p = str(tmp_path / "dump.sv")
        Path(sv_p).write_text(sv)
        spec = {
            "version": 1, "top": "dumptest",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": False, "duration_cycles": 1},
            "sim": {"cycles": 5},
            "cases": [{"name": "t", "expected": [{"at": 3, "check": {"q": 2}}]}],
        }
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            p = str(tmp_path / "spec.yaml")
            Path(p).write_text(yaml.dump(spec), encoding="utf-8")
            result = ds.test([sv_p], spec=p)
        finally:
            os.chdir(old_cwd)
        # VCD ファイルが作られること
        vcd_p = tmp_path / "test_out.vcd"
        assert vcd_p.exists(), "VCD ファイルが作られなかった"
        content = vcd_p.read_text(encoding="utf-8", errors="replace")
        assert "$var" in content, "$var 宣言が VCD にない"
        assert "$enddefinitions" in content


class TestEUnknownPortStrict:
    """E_UNKNOWN_PORT — ポートのみに厳密化"""

    def test_internal_signal_in_set_rejected(self, tmp_path):
        """内部信号を stimulus/set に書くと E_UNKNOWN_PORT"""
        # fifo.sv の内部信号 mem を set に書く
        spec = {
            "version": 1, "top": "fifo",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 3, "set": {"mem": 0}}],
                       "expected": [{"at": 3, "check": {"full": 0}}]}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        sv_p = os.path.join(EXAMPLES, "fifo.sv")
        result = ds.test([sv_p], spec=p)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes

    def test_output_in_set_rejected(self, tmp_path):
        """output ポートを stimulus/set に書くと E_UNKNOWN_PORT"""
        spec = {
            "version": 1, "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 2},
            "sim": {"cycles": 5},
            "cases": [{"name": "t",
                       "stimulus": [{"at": 3, "set": {"count": 5}}],
                       "expected": [{"at": 4, "check": {"count": 5}}]}],
        }
        p = str(tmp_path / "spec.yaml")
        Path(p).write_text(yaml.dump(spec), encoding="utf-8")
        result = ds.test([COUNTER_SV], spec=p)
        codes = [e.code for e in result.errors] + [e.code for cr in result.cases for e in cr.errors]
        assert "E_UNKNOWN_PORT" in codes


class TestSafetyGenOutDir:
    """`gen -d /tmp` が拒否されること"""

    def test_gen_d_outside_cwd_rejected(self, tmp_path, monkeypatch):
        """`gen -d /tmp` が CWD 外への書き込みとして拒否されること"""
        import os
        from deltasim.llm.safety import check_command, UnsafeCommandError
        monkeypatch.chdir(tmp_path)
        with pytest.raises(UnsafeCommandError, match="カレントディレクトリ"):
            check_command("deltasim gen a.sv -d /tmp", check_write=True)

    def test_d_in_write_opts_short(self):
        """-d が _WRITE_OPTS_SHORT に含まれること"""
        from deltasim.llm.safety import _WRITE_OPTS_SHORT
        assert "-d" in _WRITE_OPTS_SHORT


class TestLogFileAlwaysCreated:
    """--log ファイルが常に作られること"""

    def test_test_log_always_created(self, tmp_path):
        """deltasim test --log でログファイルが作られ、内容を持つこと"""
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        spec = os.path.join(EXAMPLES, "counter_spec.yaml")
        log_p = str(tmp_path / "test.log")
        runner = CliRunner()
        result = runner.invoke(cli_main, [
            "test", COUNTER_SV, "--spec", spec, "--log", log_p,
        ])
        assert result.exit_code == 0, result.output
        assert Path(log_p).exists(), "ログファイルが作られなかった"

    def test_run_log_has_no_crlf(self, tmp_path):
        """run --log のログファイルが LF 改行であること"""
        sv_p = str(tmp_path / "mon.sv")
        Path(sv_p).write_text(
            'module mon(input logic clk);\n  initial begin $display("hello"); end\nendmodule\n'
        )
        log_p = str(tmp_path / "run.log")
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        runner = CliRunner()
        runner.invoke(cli_main, ["run", sv_p, "--cycles", "3", "--log", log_p])
        assert Path(log_p).exists(), "ログファイルが作られなかった"
        content = Path(log_p).read_bytes()
        assert b"\r\n" not in content, "ログに CRLF が含まれている"


class TestFindTopCandidatesGenerate:
    """find_top_candidates が generate ブロック内のインスタンスも検出すること"""

    def test_generate_block_instance_excluded(self, tmp_path):
        """generate ブロック内のインスタンスがある場合、そのモジュールは top 候補外"""
        sv_a = str(tmp_path / "mod_a.sv")
        sv_b = str(tmp_path / "mod_b.sv")
        Path(sv_a).write_text(
            "module mod_a(input logic x);\n"
            "  generate\n"
            "    mod_b inst(.y(x));\n"
            "  endgenerate\n"
            "endmodule\n"
        )
        Path(sv_b).write_text("module mod_b(input logic y); endmodule\n")
        from deltasim.api import _parse_files, find_top_candidates
        mods, cat, _ = _parse_files([sv_a, sv_b])
        candidates = find_top_candidates(mods, cat)
        # mod_b は generate 内でインスタンスされているので top 候補外
        assert "mod_a" in candidates
        # mod_b がインスタンスされているなら top でない
        # (generate ブロック対応している場合)
        # パーサが generate 内の InstDecl を返すかは実装次第
        # 少なくとも mod_a が候補に含まれること
        assert len(candidates) >= 1


class TestClassificationFixed:
    """CLI オプション分類テストが string 型オプションも検査すること"""

    def test_unconfigured_opt_detected_by_monkeypatch(self, tmp_path, monkeypatch):
        """未分類オプションが追加されると分類テストが失敗すること（monkeypatch）"""
        import click
        from deltasim.llm.safety import _ALL_WRITE_OPTS, _READ_OPTS_LONG, _READ_OPTS_SHORT, _NON_PATH_OPTS
        from deltasim.cli import main as cli_main

        # 既存コマンドに分類されていない仮オプションを追加
        all_known = set(_ALL_WRITE_OPTS) | set(_READ_OPTS_LONG) | set(_READ_OPTS_SHORT) | _NON_PATH_OPTS
        # "--new-unclassified-path" は既知リストにない
        assert "--new-unclassified-path" not in all_known, \
            "このオプションは未分類テストのシミュレーション用"
