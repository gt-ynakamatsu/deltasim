"""deltasim CLI entry point.

Commands
--------
  deltasim run   — simulate RTL and optionally write VCD
  deltasim gen   — generate testbench SV + YAML spec skeleton
  deltasim test  — run test from YAML spec with PASS/FAIL judgment
  deltasim parse — show parsed AST (debug)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import click

from deltasim.parser.parser import parse_file, ParseError
from deltasim.elab.elaborator import elaborate, ElabError
from deltasim.sim.engine import SimEngine, SimError
from deltasim.vcd.writer import VCDWriter


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="deltasim")
def main():
    """deltasim — SystemVerilog フルスクラッチシミュレータ"""


# ---------------------------------------------------------------------------
# Helper: parse + elaborate
# ---------------------------------------------------------------------------

def _parse_elaborate(sv_files, top, params):
    modules = []
    for sv_path in sv_files:
        if not os.path.isfile(sv_path):
            click.echo(f"ERROR: ファイルが見つかりません: {sv_path!r}", err=True)
            sys.exit(1)
        try:
            mod = parse_file(sv_path)
            modules.append(mod)
        except ParseError as e:
            click.echo(f"ERROR (parse): {e}", err=True)
            sys.exit(1)
    if not modules:
        click.echo("ERROR: 解析対象のモジュールがありません", err=True)
        sys.exit(1)
    module = modules[0] if top is None else next(
        (m for m in modules if m.name == top), None
    )
    if module is None:
        click.echo(
            f"ERROR: モジュール {top!r} が見つかりません。"
            f"利用可能: {[m.name for m in modules]}", err=True
        )
        sys.exit(1)
    try:
        design = elaborate(module, param_overrides=params)
    except ElabError as e:
        click.echo(f"ERROR (elab): {e}", err=True)
        sys.exit(1)
    return design


# ---------------------------------------------------------------------------
# `run` subcommand
# ---------------------------------------------------------------------------

@main.command("run")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--cycles", "-n", default=100, show_default=True, type=int)
@click.option("--vcd", "-o", default=None, metavar="PATH")
@click.option("--reset-cycles", default=10, show_default=True, type=int)
@click.option("--half-period", default=5, show_default=True, type=int)
@click.option("--clk", default=None)
@click.option("--rst", default=None)
@click.option("--rst-active-high", is_flag=True, default=False)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--verbose", "-v", is_flag=True, default=False)
def run_cmd(sv_files, top, cycles, vcd, reset_cycles, half_period,
            clk, rst, rst_active_high, params, verbose):
    """SystemVerilog ファイルをシミュレートして VCD を出力する。"""
    param_overrides = _parse_params(params)
    design = _parse_elaborate(sv_files, top, param_overrides)

    click.echo(f"[deltasim] モジュール: {design.module_name}  cycles: {cycles}", err=True)

    engine = SimEngine(design, half_period_ns=half_period,
                       reset_cycles=reset_cycles, verbose=verbose)
    if clk:
        engine.set_clk(clk)
    if rst:
        engine.set_rst(rst, active_low=not rst_active_high)

    if engine._clk:
        click.echo(f"[deltasim] クロック: {engine._clk}", err=True)
    else:
        click.echo("[deltasim] クロックなし（組み合わせ回路）", err=True)
    if engine._rst:
        pol = "active-low" if engine._rst_active_low else "active-high"
        click.echo(f"[deltasim] リセット: {engine._rst} ({pol})  {reset_cycles} サイクル", err=True)

    vcd_obj = None
    if vcd:
        vcd_obj = VCDWriter(vcd, design)
        vcd_obj.record_initial(0, engine.signals)
        engine.vcd_writer = vcd_obj

    try:
        result = engine.run(cycles, reset_cycles=reset_cycles)
    except SimError as e:
        click.echo(f"ERROR (sim): {e}", err=True)
        if vcd_obj:
            vcd_obj.close()
        sys.exit(1)
    finally:
        if vcd_obj:
            vcd_obj.close()

    click.echo(f"[deltasim] 完了: {cycles} サイクル (t={engine._time} ns)", err=True)
    if vcd:
        click.echo(f"[deltasim] VCD: {vcd}", err=True)
    if not verbose and result.log:
        for line in result.log:
            click.echo(line)
    if result.finished:
        click.echo("[deltasim] $finish により終了", err=True)


# ---------------------------------------------------------------------------
# `gen` subcommand
# ---------------------------------------------------------------------------

@main.command("gen")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--out-dir", "-d", default=".", show_default=True,
              help="出力先ディレクトリ")
@click.option("--spec", default=None, metavar="PATH",
              help="YAML スペックファイルパス（指定なし → <top>_spec.yaml）")
@click.option("--tb", default=None, metavar="PATH",
              help="TB SV ファイルパス（指定なし → <top>_tb.sv）")
@click.option("--no-tb", is_flag=True, default=False, help="TB SV を生成しない")
@click.option("--no-spec", is_flag=True, default=False, help="YAML スペックを生成しない")
def gen_cmd(sv_files, top, params, out_dir, spec, tb, no_tb, no_spec):
    """テストベンチ SV と YAML スペックスケルトンを生成する。

    Examples:\n
      deltasim gen counter.sv --top counter\n
      deltasim gen counter.sv --top counter --out-dir ./gen
    """
    from deltasim.tb.generator import generate_tb_sv, generate_yaml_spec

    param_overrides = _parse_params(params)
    design = _parse_elaborate(sv_files, top, param_overrides)

    os.makedirs(out_dir, exist_ok=True)
    top_name = design.module_name

    if not no_tb:
        tb_path = tb or os.path.join(out_dir, f"{top_name}_tb.sv")
        generate_tb_sv(design, tb_path)
        click.echo(f"[deltasim gen] TB SV: {tb_path}")

    if not no_spec:
        spec_path = spec or os.path.join(out_dir, f"{top_name}_spec.yaml")
        generate_yaml_spec(design, spec_path)
        click.echo(f"[deltasim gen] YAML spec: {spec_path}")


# ---------------------------------------------------------------------------
# `test` subcommand
# ---------------------------------------------------------------------------

@main.command("test")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--spec", "-s", required=True, metavar="YAML",
              help="YAML スペックファイルパス")
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--junit", default=None, metavar="PATH",
              help="JUnit XML 出力パス")
@click.option("--vcd", default=None, metavar="PATH",
              help="VCD 出力パス（spec の vcd より優先）")
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.option("--no-color", is_flag=True, default=False)
def test_cmd(sv_files, top, spec, params, junit, vcd, verbose, no_color):
    """YAML スペックに従ってテストを実行し PASS/FAIL を判定する。

    Examples:\n
      deltasim test counter.sv --spec counter_spec.yaml\n
      deltasim test counter.sv --spec counter_spec.yaml --junit report.xml
    """
    from deltasim.tb.scenario import load_scenario, ScenarioError
    from deltasim.tb.runner import TestRunner
    from deltasim.report.terminal import print_results
    from deltasim.report.junit import write_junit

    # Load spec
    if not os.path.isfile(spec):
        click.echo(f"ERROR: スペックファイルが見つかりません: {spec!r}", err=True)
        sys.exit(1)
    try:
        scenario = load_scenario(spec)
    except ScenarioError as e:
        click.echo(f"ERROR (spec): {e}", err=True)
        sys.exit(1)

    # CLI params override spec values
    param_overrides = _parse_params(params)

    # Determine top from spec or CLI
    top_name = top or scenario.top
    design = _parse_elaborate(sv_files, top_name, param_overrides)

    # CLI --vcd overrides spec
    if vcd:
        scenario.sim.vcd = vcd

    click.echo(
        f"[deltasim test] モジュール: {design.module_name}  "
        f"spec: {spec}  cycles: {scenario.sim.cycles}", err=True
    )

    runner = TestRunner(design, scenario, verbose=verbose)
    try:
        result = runner.run()
    except SimError as e:
        click.echo(f"ERROR (sim): {e}", err=True)
        sys.exit(1)

    print_results(result, verbose=verbose, color=not no_color)

    if junit:
        write_junit(result, junit)
        click.echo(f"[deltasim test] JUnit XML: {junit}", err=True)

    sys.exit(0 if result.passed else 1)


# ---------------------------------------------------------------------------
# `gui` subcommand
# ---------------------------------------------------------------------------

@main.command("gui")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="バインドホスト（デフォルト: 127.0.0.1 のみ）")
@click.option("--port", "-p", default=8765, show_default=True, type=int,
              help="ポート番号")
@click.option("--no-browser", is_flag=True, default=False,
              help="ブラウザを自動起動しない")
@click.option("--reload", is_flag=True, default=False, hidden=True,
              help="開発用オートリロード")
def gui_cmd(host, port, no_browser, reload):
    """ブラウザ GUI を起動する（127.0.0.1 のみで待受）。

    \b
    必要な依存パッケージ:
        pip install "deltasim[gui]"

    Examples:\n
        deltasim gui\n
        deltasim gui --port 8080\n
        deltasim gui --no-browser
    """
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

    url = f"http://{host}:{port}"
    click.echo(f"[deltasim gui] 起動中: {url}", err=True)
    click.echo("[deltasim gui] 終了するには Ctrl+C を押してください", err=True)

    if not no_browser:
        import threading
        import webbrowser

        def _open_browser():
            import time
            time.sleep(1.2)
            try:
                webbrowser.open(url)
                click.echo(f"[deltasim gui] ブラウザを開きました: {url}", err=True)
            except Exception as e:
                click.echo(f"[deltasim gui] ブラウザを自動起動できませんでした: {e}", err=True)

        t = threading.Thread(target=_open_browser, daemon=True)
        t.start()

    import uvicorn
    uvicorn.run(
        "deltasim.gui.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


# ---------------------------------------------------------------------------
# `parse` subcommand (debug)
# ---------------------------------------------------------------------------

@main.command("parse")
@click.argument("sv_file")
def parse_cmd(sv_file: str):
    """SV ファイルをパースして AST を表示する（デバッグ用）。"""
    try:
        mod = parse_file(sv_file)
    except ParseError as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)
    click.echo(f"module: {mod.name}")
    click.echo(f"  params ({len(mod.params)}):")
    for p in mod.params:
        click.echo(f"    {p.name} = {p.value}")
    click.echo(f"  ports ({len(mod.ports)}):")
    for po in mod.ports:
        click.echo(f"    {po.direction} {po.net_type or ''} {po.name}")
    click.echo(f"  body items: {len(mod.items)}")


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
