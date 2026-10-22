"""deltasim CLI — すべての処理は deltasim.api に委譲する薄いラッパー。

Commands
--------
  deltasim run   — シミュレーション実行
  deltasim gen   — テストベンチ SV + YAML スケルトン生成
  deltasim test  — YAML スペックによるテスト実行・判定
  deltasim parse — AST 表示（デバッグ用）
  deltasim sim   — 対話デバッグシミュレータ起動
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path
from typing import Optional

import click


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="deltasim")
def main():
    """deltasim — SystemVerilog フルスクラッチシミュレータ"""


# ---------------------------------------------------------------------------
# §18.2: -f/--filelist ヘルパー
# ---------------------------------------------------------------------------

def _expand_files(sv_files: tuple, filelist: tuple):
    """sv_files と -f ファイルを結合して (all_files, FileList) を返す。
    警告は stderr に出力する。エラーがあれば sys.exit(1) する。
    """
    from deltasim.filelist import expand_filelist_args
    all_files, fl = expand_filelist_args(sv_files, filelist)
    for w in fl.warnings:
        click.echo(f"W ({w.code}): {w.file}:{w.line}: {w.message}", err=True)
    for e in fl.errors:
        click.echo(f"ERROR ({e.code}): {e.file}:{e.line}: {e.message}", err=True)
    if fl.errors:
        sys.exit(1)
    if not all_files:
        click.echo("ERROR: SV ファイルが指定されていません（-f またはファイル引数を指定してください）",
                   err=True)
        sys.exit(1)
    return all_files, fl


def _filelist_to_result_warnings(fl) -> list:
    """FileList の警告を WarningInfo リストに変換する（test --json 出力用）。"""
    from deltasim.result import WarningInfo
    return [
        WarningInfo(
            code=w.code,
            message=w.message,
            file=w.file,
            line=w.line,
        )
        for w in fl.warnings
    ]


_FILELIST_OPT = click.option(
    "-f", "--filelist", "filelist", multiple=True, metavar="FILE.f",
    help="§18.2: ファイルリスト (.f) を指定（複数回指定可）",
)

# §18.5: --log オプション（run/test/gen/parse 共通）
_LOG_OPT = click.option(
    "--log", "log_path", default="deltasim.log", show_default=True,
    metavar="PATH", help="実行ログの出力先ファイル（既定: deltasim.log）",
)


def _write_log(log_path: str, lines: list) -> None:
    """ログ行を log_path に書き出す（UTF-8・LF 改行）。
    lines が空でもファイルを作成する（実行の記録として）。
    """
    try:
        from pathlib import Path
        Path(log_path).write_text(
            "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n"
        )
    except Exception as exc:
        click.echo(f"WARNING: ログ書き込み失敗 {log_path!r}: {exc}", err=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_params(params) -> dict:
    overrides = {}
    for p in params:
        if "=" not in p:
            click.echo(f"ERROR: --param は NAME=VALUE 形式で指定してください: {p!r}", err=True)
            sys.exit(1)
        k, _, v = p.partition("=")
        try:
            overrides[k.strip()] = int(v.strip(), 0)
        except ValueError:
            click.echo(f"ERROR: パラメータ値が整数ではありません: {p!r}", err=True)
            sys.exit(1)
    return overrides


def _emit_json_event(event: dict, json_events: bool) -> None:
    """JSON Lines として stdout に出力する進捗イベントエミッター。"""
    if json_events:
        click.echo(_json.dumps(event, ensure_ascii=False))


# ---------------------------------------------------------------------------
# `run` subcommand
# ---------------------------------------------------------------------------

@main.command("run")
@click.argument("sv_files", nargs=-1, required=False, metavar="FILE [FILE …]")
@_FILELIST_OPT
@_LOG_OPT
@click.option("--top", "-t", default=None)
@click.option("--cycles", "-n", default=100, show_default=True, type=int)
@click.option("--vcd", "-o", default=None, metavar="PATH")
@click.option("--reset-cycles", default=10, show_default=True, type=int)
@click.option("--half-period", default=5, show_default=True, type=int)
@click.option("--clk", default=None, help="クロック信号名")
@click.option("--rst", default=None, help="リセット信号名")
@click.option("--rst-active-high", is_flag=True, default=False)
@click.option("-i", "--interactive", is_flag=True, default=False,
              help="対話モードで起動（deltasim sim と同じ）")
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.option("--json", "json_out", default=None, metavar="PATH", help="結果を JSON 出力")
@click.option("--json-events", is_flag=True, default=False,
              help="進捗イベントを JSON Lines で stdout に出力")
@click.option("--max-delta", "max_delta", default=None, type=int, metavar="N",
              help="デルタサイクル上限（既定10000）。超えると E_DELTA_LIMIT。")
@click.option("--max-loop", "max_loop", default=None, type=int, metavar="N",
              help="ループ反復上限（既定1000000）。超えると E_LOOP_LIMIT。")
def run_cmd(sv_files, filelist, log_path, top, cycles, vcd, reset_cycles, half_period,
            clk, rst, rst_active_high, params, verbose, json_out, json_events,
            max_delta, max_loop, interactive):
    """SystemVerilog ファイルをシミュレートして VCD を出力する。

    -i / --interactive を指定すると対話モードで起動します（deltasim sim と同じ）。
    -f FILE.f でファイルリストを指定できます（複数回指定可）。
    """
    import deltasim.api as ds

    sv_files, _fl = _expand_files(sv_files, filelist)

    if interactive:
        # 対話モード: deltasim sim と同じ処理に委譲
        _run_interactive(list(sv_files), top=top,
                         params=_parse_params(params),
                         reset_cycles=reset_cycles)
        return

    param_overrides = _parse_params(params)

    # clk/rst は load 後に上書きするため、先に elaborate
    di = ds.load(list(sv_files), top=top, params=param_overrides)
    if di.status != "PASS":
        for e in di.errors:
            click.echo(f"ERROR ({e.code}): {e.file}:{e.line}: {e.message}", err=True)
        sys.exit(1)

    # Override clk/rst if specified
    design = di._design
    if clk:
        design.signals  # just check it exists
        di._design._clk_override = clk
    if rst:
        di._design._rst_override = rst
        di._design._rst_active_low_override = not rst_active_high

    click.echo(f"[deltasim] モジュール: {di.module}  cycles: {cycles}", err=True)

    def _progress_cb(ev: dict):
        _emit_json_event(ev, json_events)
        if verbose and ev.get("type") == "progress" and ev.get("phase") == "sim":
            click.echo(
                f"[sim] cycle={ev.get('cycle')}/{ev.get('total_cycles')}"
                f"  t={ev.get('time_ns')}ns", err=True
            )

    try:
        result = ds.run(
            di,
            cycles=cycles,
            vcd=vcd,
            reset_cycles=reset_cycles,
            half_period=half_period,
            on_progress=_progress_cb,
            max_delta=max_delta,
            max_loop=max_loop,
        )
    except Exception as _unexpected:
        # E_INTERNAL: 想定外例外でも --json があれば出力
        import traceback as _tb
        _tb.print_exc(file=sys.stderr)
        if json_out:
            from deltasim.result import RunResult, ErrorInfo, SCHEMA_VERSION
            _er = RunResult(status="ERROR")
            _er.errors.append(ErrorInfo(
                code="E_INTERNAL",
                message=f"{type(_unexpected).__name__}: {_unexpected}",
                severity="error",
            ))
            Path(json_out).write_text(_er.to_json(), encoding="utf-8")
            click.echo(f"[deltasim] JSON (E_INTERNAL): {json_out}", err=True)
        sys.exit(1)

    if result.status == "ERROR":
        for err in result.errors:
            click.echo(f"ERROR ({err.code}): {err.message}", err=True)
        if json_out:
            Path(json_out).write_text(result.to_json(), encoding="utf-8")
            click.echo(f"[deltasim] JSON: {json_out}", err=True)
        sys.exit(1)

    click.echo(
        f"[deltasim] 完了: {cycles} サイクル (t={result.time_ns} ns)", err=True
    )
    if vcd:
        click.echo(f"[deltasim] VCD: {vcd}", err=True)
    if result.log:
        for line in result.log:
            click.echo(line)
    _write_log(log_path, result.log)
    if result.cancelled:
        click.echo("[deltasim] キャンセルされました", err=True)

    if json_out:
        Path(json_out).write_text(result.to_json(), encoding="utf-8")
        click.echo(f"[deltasim] JSON: {json_out}", err=True)


# ---------------------------------------------------------------------------
# `gen` subcommand
# ---------------------------------------------------------------------------

@main.command("gen")
@click.argument("sv_files", nargs=-1, required=False, metavar="FILE [FILE …]")
@_FILELIST_OPT
@_LOG_OPT
@click.option("--top", "-t", default=None)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--out-dir", "-d", default=".", show_default=True)
@click.option("--spec", "spec_path", default=None, metavar="PATH")
@click.option("--tb", "tb_path", default=None, metavar="PATH")
@click.option("--no-tb", is_flag=True, default=False)
@click.option("--no-spec", is_flag=True, default=False)
@click.option("--strings", is_flag=True, default=False,
              help="ファイル書き込みの代わりに文字列として stdout に出力")
def gen_cmd(sv_files, filelist, log_path, top, params, out_dir, spec_path, tb_path, no_tb, no_spec, strings):
    """テストベンチ SV と YAML スペックスケルトンを生成する。

    --strings: ファイルに書き込まず TB SV / YAML 文字列を stdout に出力する。
    -f FILE.f でファイルリストを指定できます。
    """
    import deltasim.api as ds

    sv_files, _fl = _expand_files(sv_files, filelist)
    param_overrides = _parse_params(params)

    if strings:
        result = ds.gen(list(sv_files), top=top, params=param_overrides)
    else:
        result = ds.gen(
            list(sv_files), top=top, params=param_overrides,
            out_dir=out_dir,
            tb_path=tb_path if not no_tb else None,
            spec_path=spec_path if not no_spec else None,
        )

    if result.get("status") != "PASS":
        for e in result.get("errors", []):
            click.echo(f"ERROR: {e.get('message')}", err=True)
        sys.exit(1)

    if strings:
        # §18.5: 見出しを実際のモジュール名にする
        mod_name = result.get("module", "module")
        if not no_tb:
            click.echo(f"=== {mod_name}_tb.sv ===")
            click.echo(result.get("tb_sv", ""))
        if not no_spec:
            click.echo(f"=== {mod_name}_spec.yaml ===")
            click.echo(result.get("spec_yaml", ""))
    else:
        if "tb_path" in result:
            click.echo(f"[deltasim gen] TB SV: {result['tb_path']}")
        if "spec_path" in result:
            click.echo(f"[deltasim gen] YAML spec: {result['spec_path']}")


# ---------------------------------------------------------------------------
# `test` subcommand
# ---------------------------------------------------------------------------

@main.command("test")
@click.argument("sv_files", nargs=-1, required=False, metavar="FILE [FILE …]")
@_FILELIST_OPT
@_LOG_OPT
@click.option("--top", "-t", default=None)
@click.option("--spec", "-s", required=True, metavar="YAML")
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--junit", default=None, metavar="PATH", help="JUnit XML 出力")
@click.option("--json", "json_out", default=None, metavar="PATH",
              help="結果を JSON ファイルに出力")
@click.option("--vcd", default=None, metavar="PATH")
@click.option("--seed", default=None, type=int)
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.option("--no-color", is_flag=True, default=False)
@click.option("--json-events", is_flag=True, default=False,
              help="進捗イベントを JSON Lines として stdout に出力")
@click.option("--record", "record_mode", default=None, metavar="all",
              help="§12.3: 'all' で全信号を変化 DB に記録（既定: ポートと spec 参照信号のみ）")
@click.option("--record-all", "record_all_deprecated", is_flag=True, default=False,
              hidden=True, help="非推奨: --record all を使ってください")
@click.option("--wave-mem", "wave_mem_mb", default=512, show_default=True, type=int,
              help="§12.3: 信号変化 DB メモリ上限 (MB)")
@click.option("--case", "filter_cases", multiple=True, metavar="CASE_NAME",
              help="§12.1: 実行するケース名を絞り込む（未指定で全ケース）。外れたケースは結果に含まない。")
@click.option("--max-delta", "max_delta", default=None, type=int, metavar="N",
              help="デルタサイクル上限（既定10000）。超えると E_DELTA_LIMIT。")
@click.option("--max-loop", "max_loop", default=None, type=int, metavar="N",
              help="ループ反復上限（既定1000000）。超えると E_LOOP_LIMIT。")
def test_cmd(sv_files, filelist, log_path, top, spec, params, junit, json_out, vcd, seed,
             verbose, no_color, json_events, record_mode, record_all_deprecated,
             wave_mem_mb, filter_cases, max_delta, max_loop):
    """YAML スペックに従ってテストを実行し PASS/FAIL を判定する。
    -f FILE.f でファイルリストを指定できます。

    Examples:\n
      deltasim test counter.sv --spec counter_spec.yaml\n
      deltasim test counter.sv --spec counter_spec.yaml --junit report.xml\n
      deltasim test counter.sv --spec counter_spec.yaml --json result.json\n
      deltasim test counter.sv --spec counter_spec.yaml --json-events\n
      deltasim test counter.sv --spec counter_spec.yaml --record all\n
      deltasim test counter.sv --spec counter_spec.yaml --case counter
    """
    import deltasim.api as ds
    from deltasim.report.terminal import print_results_v2

    sv_files, _fl = _expand_files(sv_files, filelist)
    # §18.2: -f ファイルの W_FILELIST_OPT を warn イベントとして emit
    _fl_warnings = _filelist_to_result_warnings(_fl)
    for w in _fl_warnings:
        _emit_json_event(
            {"type": "warning", "code": w.code, "message": w.message,
             "file": w.file, "line": w.line},
            json_events,
        )

    # --record-all 非推奨エイリアス（stderr のみ、stdout JSON は汚さない）
    if record_all_deprecated:
        click.echo(
            "WARNING: --record-all は非推奨です。'--record all' を使ってください。",
            err=True,
        )

    record_all = record_all_deprecated or (record_mode == "all")

    if not Path(spec).is_file():
        click.echo(f"ERROR: スペックファイルが見つかりません: {spec!r}", err=True)
        sys.exit(1)

    param_overrides = _parse_params(params)

    def _progress_cb(ev: dict):
        _emit_json_event(ev, json_events)

    click.echo(f"[deltasim test] spec: {spec}", err=True)

    try:
        result = ds.test(
            list(sv_files), spec,
            top=top, params=param_overrides, seed=seed, vcd=vcd,
            on_progress=_progress_cb,
            record_all=record_all,
            wave_mem_mb=wave_mem_mb,
            filter_cases=list(filter_cases) if filter_cases else None,
            max_delta=max_delta,
            max_loop=max_loop,
        )
        # §18.2: W_FILELIST_OPT を TestResult.warnings に追加（JSON 出力用）
        if _fl_warnings:
            result.warnings = list(result.warnings) + _fl_warnings
    except Exception as _unexpected:
        # §0.2.1: --json 付きのとき想定外例外でも JSON を出力する（E_INTERNAL）
        import traceback as _tb
        _tb.print_exc(file=sys.stderr)
        if json_out:
            import json as _json
            from deltasim.result import TestResult, ErrorInfo, SCHEMA_VERSION
            _er = TestResult(status="ERROR")
            _er.errors.append(ErrorInfo(
                code="E_INTERNAL",
                message=f"{type(_unexpected).__name__}: {_unexpected}",
                severity="error",
            ))
            Path(json_out).write_text(_er.to_json(), encoding="utf-8")
            click.echo(f"[deltasim test] JSON (E_INTERNAL): {json_out}", err=True)
        sys.exit(1)

    # §12.1: フィルタアウトされたケースは checks が空の TestResult として返る
    # §14.1: E_NO_CASE の場合も JSON を正しく出力してから非 0 終了
    if result is not None and result.status == "ERROR":
        # Write JSON before exit (stdout は汚染しない)
        if json_out:
            Path(json_out).write_text(result.to_json(), encoding="utf-8")
            click.echo(f"[deltasim test] JSON: {json_out}", err=True)
        for err in result.errors:
            click.echo(f"ERROR ({err.code}): {err.message}", err=True)
        print_results_v2(result, verbose=verbose, color=not no_color)
        sys.exit(1)

    # Terminal output
    print_results_v2(result, verbose=verbose, color=not no_color)

    # JUnit XML
    if junit:
        from deltasim.report.junit import write_junit_v2
        write_junit_v2(result, junit)
        click.echo(f"[deltasim test] JUnit XML: {junit}", err=True)

    # JSON
    if json_out:
        Path(json_out).write_text(result.to_json(), encoding="utf-8")
        click.echo(f"[deltasim test] JSON: {json_out}", err=True)

    # ログ出力
    _write_log(log_path, result.log)

    sys.exit(0 if result.status == "PASS" else 1)


# ---------------------------------------------------------------------------
# `parse` subcommand (debug)
# ---------------------------------------------------------------------------

@main.command("parse")
@click.argument("sv_files", nargs=-1, required=False, metavar="FILE [FILE …]")
@_FILELIST_OPT
@_LOG_OPT
def parse_cmd(sv_files, filelist, log_path):
    """SV ファイルをパースして AST を表示する（デバッグ用）。
    -f FILE.f でファイルリストを指定できます。
    """
    import deltasim.api as ds

    sv_files_list, _fl = _expand_files(sv_files, filelist)
    di = ds.load(sv_files_list)
    if di.status != "PASS":
        for e in di.errors:
            click.echo(f"ERROR ({e.code}): {e.file}:{e.line}: {e.message}", err=True)
        sys.exit(1)

    click.echo(f"module: {di.module}")
    click.echo(f"  params ({len(di.params)}):")
    for k, v in di.params.items():
        click.echo(f"    {k} = {v}")
    click.echo(f"  ports ({len(di.ports)}):")
    for p in di.ports:
        click.echo(f"    {p.direction} {p.name} [{p.width}]")
    click.echo(f"  signals: {len(di.signals)}")
    _write_log(log_path, [f"deltasim parse {di.module}: {di.status}"])


# ---------------------------------------------------------------------------
# `sim` subcommand — 対話デバッグ
# ---------------------------------------------------------------------------

@main.command("sim")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--reset-cycles", default=10, show_default=True, type=int)
def sim_cmd(sv_files, top, params, reset_cycles):
    """対話デバッグ Simulator を起動する（REPL モード）。§18 Questa 風コマンド対応。

    コマンド例:\n
      run [N[ns|ps|us]]  — シミュレーションを進める\n
      run -all           — 終了まで実行\n
      examine [-radix bin|hex|dec] SIGNAL — 信号値を表示\n
      force SIGNAL VAL   — 信号を強制固定\n
      noforce SIGNAL     — force を解除\n
      bp FILE:LINE       — 行ブレークポイント\n
      bp -time 100ns     — 時刻ブレークポイント\n
      bp -list           — ブレークポイント一覧\n
      when {COND} {CMDS} — 条件付きアクション\n
      restart            — リセット\n
      do FILE            — スクリプトファイル実行\n
      quit / exit        — 終了\n
      --- 後方互換 ---\n
      step [N]  until T  get SIG  set SIG VAL  signals
    """
    _run_interactive(list(sv_files), top=top,
                     params=_parse_params(params),
                     reset_cycles=reset_cycles)


def _fmt_val(val, width: int) -> str:
    from deltasim.result import val_to_4state
    bs = val_to_4state(val, width)
    if val is None:
        return f"x  ({bs})"
    if width == 1:
        return bs
    try:
        return f"{val}  (0x{val:X})  ({bs})"
    except Exception:
        return bs


def _run_interactive(sv_files, top=None, params=None, reset_cycles=10):
    """§18 対話セッションを起動するヘルパー（run -i / sim コマンドから使用）。"""
    import deltasim.api as ds
    from deltasim.interp.session import SimSession

    click.echo("[deltasim] シミュレータを起動中...", err=True)
    try:
        sim = ds.Simulator(sv_files, top=top, params=params or {},
                           reset_cycles=reset_cycles)
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    click.echo(
        f"[deltasim] モジュール: {sim._di.module}  "
        f"signals: {len(sim.list_signals())}  t=0ns\n"
        "Type 'help' for commands, 'quit' to exit.",
        err=True,
    )
    session = SimSession(sim)
    session.run_repl(prompt="vsim> ")


# ---------------------------------------------------------------------------
# `gui` subcommand
# ---------------------------------------------------------------------------

@main.command("gui")
@click.argument("paths", nargs=-1, metavar="[PATH ...]")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="バインドホスト（デフォルト: 127.0.0.1 のみ）")
@click.option("--port", "-p", default=8765, show_default=True, type=int,
              help="ポート番号")
@click.option("--no-browser", is_flag=True, default=False,
              help="ブラウザを自動起動しない")
@click.option("--reload", is_flag=True, default=False, hidden=True,
              help="開発用オートリロード")
def gui_cmd(paths, host, port, no_browser, reload):
    """ブラウザ GUI を起動する（127.0.0.1 のみで待受）。

    \b
    必要な依存パッケージ:
        pip install "deltasim[gui]"

    PATHS には SV ファイル、フォルダ、.f ファイルリスト、または
    deltasim.project.yaml を指定できます。

    Examples:\n
        deltasim gui\n
        deltasim gui examples/counter.sv examples/counter_spec.yaml\n
        deltasim gui /path/to/project/\n
        deltasim gui files.f\n
        deltasim gui deltasim.project.yaml
    """
    # §17.2: ループバック以外のホストは拒否（終了コード 2）
    _LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
    if host not in _LOOPBACK_HOSTS:
        click.echo("GUIは127.0.0.1でのみ起動できます", err=True)
        sys.exit(2)

    try:
        import uvicorn  # noqa: F401
        from deltasim.gui.server import app  # noqa: F401
    except ImportError:
        click.echo(
            "ERROR: GUI 依存パッケージがインストールされていません。\n"
            "次のコマンドでインストールしてください:\n\n"
            "    pip install \"deltasim[gui]\"\n",
            err=True,
        )
        sys.exit(1)

    initial_project = _resolve_gui_paths(paths) if paths else None

    try:
        from deltasim.gui.filesec import add_extra_root
        for p in (paths or []):
            add_extra_root(os.path.abspath(p))
    except ImportError:
        pass

    url = f"http://{host}:{port}"
    click.echo(f"[deltasim gui] 起動中: {url}", err=True)
    if initial_project:
        click.echo(f"[deltasim gui] プロジェクト: {initial_project}", err=True)
    click.echo("[deltasim gui] 終了するには Ctrl+C を押してください", err=True)

    if initial_project:
        import json as _json
        os.environ["DELTASIM_INITIAL_PROJECT"] = _json.dumps(initial_project)

    if not no_browser:
        import threading
        import webbrowser

        def _open_browser():
            import time
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception as e:
                click.echo(f"[deltasim gui] ブラウザ起動失敗: {e}", err=True)

        threading.Thread(target=_open_browser, daemon=True).start()

    import uvicorn
    uvicorn.run(
        "deltasim.gui.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


def _resolve_gui_paths(paths) -> dict:
    """GUI 起動時のパス引数を解析してプロジェクト辞書に変換する。"""
    from pathlib import Path as _Path

    sv_files, spec, project_file = [], None, None

    for raw in paths:
        p = _Path(raw).resolve()
        if not p.exists():
            click.echo(f"[deltasim gui] 警告: パスが見つかりません: {raw}", err=True)
            continue
        if p.is_dir():
            sv_files.extend(str(f) for f in sorted(p.glob("*.sv")) + sorted(p.glob("*.v")))
            specs = sorted(p.glob("*_spec.yaml"))
            if specs and spec is None:
                spec = str(specs[0])
            proj_f = p / "deltasim.project.yaml"
            if proj_f.exists() and project_file is None:
                project_file = str(proj_f)
        elif p.suffix == ".yaml" and p.stem == "deltasim.project":
            project_file = str(p)
        elif p.suffix in (".yaml", ".yml") and "_spec" in p.stem:
            spec = str(p)
        elif p.suffix == ".f":
            try:
                from deltasim.gui.project import resolve_paths_from_f_file
                sv_files.extend(resolve_paths_from_f_file(p))
            except Exception as e:
                click.echo(f"[deltasim gui] .f ファイル読み込みエラー: {e}", err=True)
        elif p.suffix in (".sv", ".v"):
            sv_files.append(str(p))

    if project_file:
        try:
            from deltasim.gui.project import load_project, project_to_dict
            return project_to_dict(load_project(project_file))
        except Exception as e:
            click.echo(f"[deltasim gui] プロジェクト読み込みエラー: {e}", err=True)

    return {"sv_files": sv_files, "spec": spec, "top": None, "params": {}}


# ---------------------------------------------------------------------------
# `ask` subcommand — §18.3 LLM 自然言語コマンド提案
# ---------------------------------------------------------------------------

@main.command("ask")
@click.argument("text")
@click.option("--yes", "-y", is_flag=True, default=False,
              help="確認なしで実行する")
@click.option("--config", "config_path", default=None, metavar="PATH",
              help="simconfig.yaml のパスを直接指定（テスト用）")
def ask_cmd(text: str, yes: bool, config_path: str):
    """自然言語で指示して deltasim コマンドを実行する（§18.3）。

    LLM がコマンドを提案し、y/N で確認してから実行します。
    --yes で確認を省けます。

    Examples:\\n
      deltasim ask "counter.sv をテストして"\\n
      deltasim ask "カウンタの spec を作って" --yes
    """
    from deltasim.llm import suggest_command, run_suggestion
    from deltasim.llm.config import load_llm_config
    from deltasim.llm.client import LLMConnectError, LLMResponseError

    cfg = load_llm_config(config_path)
    if cfg is None:
        click.echo(
            "ERROR (E_LLM_NOT_CONFIGURED): LLM が未設定です。"
            "simconfig.yaml の llm を設定してください。",
            err=True,
        )
        sys.exit(3)

    try:
        sug = suggest_command(text, cfg=cfg)
    except LLMConnectError as exc:
        click.echo(f"ERROR ({exc.code}): {exc}", err=True)
        sys.exit(4)
    except LLMResponseError as exc:
        click.echo(f"ERROR ({exc.code}): {exc}", err=True)
        sys.exit(4)
    except Exception as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)

    rc = run_suggestion(sug, yes=yes)
    sys.exit(rc)


# ---------------------------------------------------------------------------
# `llm` subcommand group — §18.3 LLM サブコマンド群
# ---------------------------------------------------------------------------

@main.group("llm")
def llm_group():
    """LLM 連携コマンド（§18.3）。simconfig.yaml の設定が必要です。"""


@llm_group.command("draft-spec")
@click.argument("sv_files", nargs=-1, required=False, metavar="FILE [FILE …]")
@_FILELIST_OPT
@click.option("--top", "-t", default=None)
@click.option("-o", "--output", "output_path", default=None, metavar="PATH",
              help="出力先 YAML ファイル")
@click.option("--force", is_flag=True, default=False,
              help="既存ファイルを上書き")
@click.option("--config", "config_path", default=None, metavar="PATH")
def draft_spec_cmd(sv_files, filelist, top, output_path, force, config_path):
    """RTL から YAML テストスペックの下書きを生成する（§18.3）。

    Examples:\\n
      deltasim llm draft-spec counter.sv -o counter_spec_draft.yaml\\n
      deltasim llm draft-spec -f files.f --top counter -o spec.yaml --force
    """
    from deltasim.llm import draft_spec
    from deltasim.llm.config import load_llm_config
    from deltasim.llm.client import LLMConnectError

    sv_files, _ = _expand_files(sv_files, filelist)

    cfg = load_llm_config(config_path)
    if cfg is None:
        click.echo(
            "ERROR (E_LLM_NOT_CONFIGURED): LLM が未設定です。"
            "simconfig.yaml の llm を設定してください。",
            err=True,
        )
        sys.exit(3)

    try:
        result = draft_spec(
            list(sv_files), top=top, cfg=cfg,
            output_path=output_path, force=force,
        )
    except FileExistsError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(5)
    except LLMConnectError as exc:
        click.echo(f"ERROR ({exc.code}): {exc}", err=True)
        sys.exit(4)
    except Exception as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)

    if output_path:
        if result.valid:
            click.echo(f"[deltasim llm draft-spec] 保存: {output_path}")
        else:
            click.echo(
                f"[deltasim llm draft-spec] 保存（検証エラーあり）: {output_path}",
                err=True,
            )
            for e in result.errors:
                click.echo(f"  WARNING: {e}", err=True)
            sys.exit(6)
    else:
        click.echo(result.yaml_text)
    sys.exit(0)


@llm_group.command("explain")
@click.argument("result_json")
@click.option("--rtl", "rtl_files", multiple=True, metavar="FILE")
@_FILELIST_OPT
@click.option("--spec", "spec_file", default=None, metavar="YAML")
@click.option("--config", "config_path", default=None, metavar="PATH")
def explain_cmd(result_json, rtl_files, filelist, spec_file, config_path):
    """テスト失敗を LLM に解析させる（§18.3）。

    Examples:\\n
      deltasim llm explain result.json --rtl counter.sv --spec counter_spec.yaml\\n
      deltasim llm explain result.json -f files.f
    """
    from deltasim.llm import explain_result
    from deltasim.llm.config import load_llm_config
    from deltasim.llm.client import LLMConnectError

    all_rtl = list(rtl_files)
    if filelist:
        from deltasim.filelist import expand_filelist_args
        files_from_f, _ = expand_filelist_args([], filelist)
        all_rtl.extend(files_from_f)

    cfg = load_llm_config(config_path)
    if cfg is None:
        click.echo(
            "ERROR (E_LLM_NOT_CONFIGURED): LLM が未設定です。"
            "simconfig.yaml の llm を設定してください。",
            err=True,
        )
        sys.exit(3)

    try:
        text = explain_result(
            result_json,
            rtl_files=all_rtl or None,
            spec_file=spec_file,
            cfg=cfg,
        )
    except LLMConnectError as exc:
        click.echo(f"ERROR ({exc.code}): {exc}", err=True)
        sys.exit(4)
    except Exception as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(1)

    click.echo(text)
    sys.exit(0)
