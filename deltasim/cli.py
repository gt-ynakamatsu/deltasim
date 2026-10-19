"""deltasim CLI entry point.

Usage
-----
  deltasim run counter.sv --top counter --cycles 100 --vcd out.vcd

Clock / reset auto-detection
-----------------------------
Port names are inspected for well-known patterns:

  Clock   : clk, clock, sys_clk, *clk, *clock
  Reset   : rst_n, resetn, reset_n, … (active-low, suffix _n / _b)
            rst, reset, arst, …       (active-high)

Override with ``--clk`` / ``--rst`` / ``--rst-active-high``.
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
# `run` subcommand
# ---------------------------------------------------------------------------

@main.command("run")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", required=False, default=None,
              help="トップモジュール名（省略時は最初のモジュール）")
@click.option("--cycles", "-n", default=100, show_default=True, type=int,
              help="シミュレーションサイクル数")
@click.option("--vcd", "-o", default=None, metavar="PATH",
              help="VCD 出力ファイルパス")
@click.option("--reset-cycles", default=10, show_default=True, type=int,
              help="リセット保持サイクル数")
@click.option("--half-period", default=5, show_default=True, type=int,
              help="クロック半周期 (ns)。デフォルト 5ns → 100 MHz")
@click.option("--clk", default=None, help="クロック信号名（省略時は自動検出）")
@click.option("--rst", default=None, help="リセット信号名（省略時は自動検出）")
@click.option("--rst-active-high", is_flag=True, default=False,
              help="リセットをアクティブ High として扱う")
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE",
              help="パラメータ上書き（例: WIDTH=4）。複数回指定可")
@click.option("--verbose", "-v", is_flag=True, default=False,
              help="$display 出力を表示")
def run_cmd(
    sv_files,
    top,
    cycles,
    vcd,
    reset_cycles,
    half_period,
    clk,
    rst,
    rst_active_high,
    params,
    verbose,
):
    """SystemVerilog ファイルをシミュレートして VCD を出力する。

    Examples:\n
      deltasim run counter.sv --top counter --cycles 100 --vcd out.vcd\n
      deltasim run mux2.sv --top mux2 --cycles 1 --param WIDTH=4
    """
    # Parse parameters
    param_overrides = {}
    for p in params:
        if "=" not in p:
            click.echo(f"ERROR: --param 引数は NAME=VALUE 形式で指定してください: {p!r}", err=True)
            sys.exit(1)
        k, _, v = p.partition("=")
        try:
            param_overrides[k.strip()] = int(v.strip(), 0)
        except ValueError:
            click.echo(f"ERROR: パラメータ値が整数ではありません: {p!r}", err=True)
            sys.exit(1)

    # Parse source files
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

    # Select top module
    if top is None:
        module = modules[0]
        top = module.name
    else:
        module = next((m for m in modules if m.name == top), None)
        if module is None:
            click.echo(
                f"ERROR: モジュール {top!r} が見つかりません。"
                f"利用可能: {[m.name for m in modules]}", err=True
            )
            sys.exit(1)

    click.echo(f"[deltasim] モジュール: {module.name}  cycles: {cycles}", err=True)

    # Elaborate
    try:
        design = elaborate(module, param_overrides=param_overrides)
    except ElabError as e:
        click.echo(f"ERROR (elab): {e}", err=True)
        sys.exit(1)

    if verbose:
        click.echo(
            f"[deltasim] シグナル: {list(design.signals.keys())}",
            err=True
        )

    # Create engine
    engine = SimEngine(
        design,
        half_period_ns=half_period,
        reset_cycles=reset_cycles,
        verbose=verbose,
    )

    # Override clock/reset if specified
    if clk:
        if clk not in design.signals:
            click.echo(f"WARNING: クロック信号 {clk!r} が見つかりません", err=True)
        else:
            engine.set_clk(clk)

    if rst:
        if rst not in design.signals:
            click.echo(f"WARNING: リセット信号 {rst!r} が見つかりません", err=True)
        else:
            engine.set_rst(rst, active_low=not rst_active_high)

    # Print detected clock/reset
    if engine._clk:
        click.echo(f"[deltasim] クロック: {engine._clk}", err=True)
    else:
        click.echo("[deltasim] クロックが検出されませんでした（組み合わせ回路として実行）", err=True)

    if engine._rst:
        polarity = "active-low" if engine._rst_active_low else "active-high"
        click.echo(
            f"[deltasim] リセット: {engine._rst} ({polarity})  "
            f"保持: {reset_cycles} サイクル",
            err=True
        )

    # Attach VCD writer
    vcd_writer_obj = None
    if vcd:
        vcd_writer_obj = VCDWriter(vcd, design)
        engine.vcd_writer = vcd_writer_obj
        # Record initial state
        vcd_writer_obj.record_initial(0, engine.signals)

    # Run simulation
    try:
        result = engine.run(cycles, reset_cycles=reset_cycles)
    except SimError as e:
        click.echo(f"ERROR (sim): {e}", err=True)
        if vcd_writer_obj:
            vcd_writer_obj.close()
        sys.exit(1)
    finally:
        if vcd_writer_obj:
            vcd_writer_obj.close()

    # Report
    click.echo(
        f"[deltasim] 完了: {cycles} サイクル "
        f"(シミュレーション時刻 = {engine._time} ns)",
        err=True
    )
    if vcd:
        click.echo(f"[deltasim] VCD 出力: {vcd}", err=True)

    # Print $display log if not verbose (verbose already printed inline)
    if not verbose and result.log:
        for line in result.log:
            click.echo(line)

    if result.finished:
        click.echo("[deltasim] $finish/$stop によりシミュレーション終了", err=True)


# ---------------------------------------------------------------------------
# `parse` subcommand (debug helper)
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
