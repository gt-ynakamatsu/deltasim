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
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--cycles", "-n", default=100, show_default=True, type=int)
@click.option("--vcd", "-o", default=None, metavar="PATH")
@click.option("--reset-cycles", default=10, show_default=True, type=int)
@click.option("--half-period", default=5, show_default=True, type=int)
@click.option("--clk", default=None, help="クロック信号名")
@click.option("--rst", default=None, help="リセット信号名")
@click.option("--rst-active-high", is_flag=True, default=False)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.option("--json", "json_out", default=None, metavar="PATH", help="結果を JSON 出力")
@click.option("--json-events", is_flag=True, default=False,
              help="進捗イベントを JSON Lines で stdout に出力")
def run_cmd(sv_files, top, cycles, vcd, reset_cycles, half_period,
            clk, rst, rst_active_high, params, verbose, json_out, json_events):
    """SystemVerilog ファイルをシミュレートして VCD を出力する。"""
    import deltasim.api as ds

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

    result = ds.run(
        di,
        cycles=cycles,
        vcd=vcd,
        reset_cycles=reset_cycles,
        half_period=half_period,
        on_progress=_progress_cb,
    )

    click.echo(
        f"[deltasim] 完了: {cycles} サイクル (t={result.time_ns} ns)", err=True
    )
    if vcd:
        click.echo(f"[deltasim] VCD: {vcd}", err=True)
    if result.log:
        for line in result.log:
            click.echo(line)
    if result.cancelled:
        click.echo("[deltasim] キャンセルされました", err=True)

    if json_out:
        Path(json_out).write_text(result.to_json(), encoding="utf-8")
        click.echo(f"[deltasim] JSON: {json_out}", err=True)


# ---------------------------------------------------------------------------
# `gen` subcommand
# ---------------------------------------------------------------------------

@main.command("gen")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--out-dir", "-d", default=".", show_default=True)
@click.option("--spec", "spec_path", default=None, metavar="PATH")
@click.option("--tb", "tb_path", default=None, metavar="PATH")
@click.option("--no-tb", is_flag=True, default=False)
@click.option("--no-spec", is_flag=True, default=False)
@click.option("--strings", is_flag=True, default=False,
              help="ファイル書き込みの代わりに文字列として stdout に出力")
def gen_cmd(sv_files, top, params, out_dir, spec_path, tb_path, no_tb, no_spec, strings):
    """テストベンチ SV と YAML スペックスケルトンを生成する。

    --strings: ファイルに書き込まず TB SV / YAML 文字列を stdout に出力する。
    """
    import deltasim.api as ds

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
        if not no_tb:
            click.echo("=== counter_tb.sv ===")
            click.echo(result.get("tb_sv", ""))
        if not no_spec:
            click.echo("=== counter_spec.yaml ===")
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
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
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
@click.option("--wave-mem", "wave_mem_mb", default=512, show_default=True, type=int,
              help="§12.3: 信号変化 DB メモリ上限 (MB)")
@click.option("--case", "filter_cases", multiple=True, metavar="CASE_NAME",
              help="§12.1: 実行するケース名を絞り込む（未指定で全ケース）")
def test_cmd(sv_files, top, spec, params, junit, json_out, vcd, seed,
             verbose, no_color, json_events, record_mode, wave_mem_mb, filter_cases):
    """YAML スペックに従ってテストを実行し PASS/FAIL を判定する。

    Examples:\n
      deltasim test counter.sv --spec counter_spec.yaml\n
      deltasim test counter.sv --spec counter_spec.yaml --junit report.xml\n
      deltasim test counter.sv --spec counter_spec.yaml --json result.json\n
      deltasim test counter.sv --spec counter_spec.yaml --json-events\n
      deltasim test counter.sv --spec counter_spec.yaml --record all
    """
    """YAML スペックに従ってテストを実行し PASS/FAIL を判定する。

    Examples:\n
      deltasim test counter.sv --spec counter_spec.yaml\n
      deltasim test counter.sv --spec counter_spec.yaml --junit report.xml\n
      deltasim test counter.sv --spec counter_spec.yaml --json result.json\n
      deltasim test counter.sv --spec counter_spec.yaml --json-events
    """
    import deltasim.api as ds
    from deltasim.report.terminal import print_results_v2

    if not Path(spec).is_file():
        click.echo(f"ERROR: スペックファイルが見つかりません: {spec!r}", err=True)
        sys.exit(1)

    param_overrides = _parse_params(params)

    def _progress_cb(ev: dict):
        _emit_json_event(ev, json_events)

    click.echo(
        f"[deltasim test] spec: {spec}", err=True
    )

    result = ds.test(
        list(sv_files), spec,
        top=top, params=param_overrides, seed=seed, vcd=vcd,
        on_progress=_progress_cb,
        record_all=(record_mode == "all"),
        wave_mem_mb=wave_mem_mb,
    )

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

    sys.exit(0 if result.status == "PASS" else 1)


# ---------------------------------------------------------------------------
# `parse` subcommand (debug)
# ---------------------------------------------------------------------------

@main.command("parse")
@click.argument("sv_file")
def parse_cmd(sv_file: str):
    """SV ファイルをパースして AST を表示する（デバッグ用）。"""
    import deltasim.api as ds

    di = ds.load([sv_file])
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


# ---------------------------------------------------------------------------
# `sim` subcommand — 対話デバッグ
# ---------------------------------------------------------------------------

@main.command("sim")
@click.argument("sv_files", nargs=-1, required=True, metavar="FILE [FILE …]")
@click.option("--top", "-t", default=None)
@click.option("--param", "-p", "params", multiple=True, metavar="NAME=VALUE")
@click.option("--reset-cycles", default=10, show_default=True, type=int)
def sim_cmd(sv_files, top, params, reset_cycles):
    """対話デバッグ Simulator を起動する（REPL モード）。

    コマンド例:\n
      step [N]         — N サイクル進める\n
      until TIME_NS    — 指定時刻まで実行\n
      get SIGNAL       — 信号値を表示\n
      set SIGNAL VALUE — 信号を設定\n
      force SIGNAL VAL — 信号を強制固定\n
      release SIGNAL   — force を解除\n
      signals          — 信号一覧\n
      bp EXPR          — ブレークポイント追加\n
      bp list          — ブレークポイント一覧\n
      bp del ID        — ブレークポイント削除\n
      reset            — シミュレーションをリセット\n
      quit / exit      — 終了
    """
    import deltasim.api as ds

    param_overrides = _parse_params(params)

    click.echo("[deltasim sim] シミュレータを起動中...", err=True)
    try:
        sim = ds.Simulator(list(sv_files), top=top, params=param_overrides,
                           reset_cycles=reset_cycles)
    except Exception as e:
        click.echo(f"ERROR: {e}", err=True)
        sys.exit(1)

    click.echo(f"[deltasim sim] モジュール: {sim._di.module}  "
               f"signals: {len(sim.list_signals())}  t=0ns\n"
               "Type 'help' for commands, 'quit' to exit.", err=True)

    while True:
        try:
            line = click.prompt("sim> ", prompt_suffix="", default="", show_default=False)
        except (EOFError, KeyboardInterrupt):
            click.echo("\n[終了]")
            break

        line = line.strip()
        if not line:
            continue
        tokens = line.split()
        cmd = tokens[0].lower()

        if cmd in ("quit", "exit", "q"):
            break

        elif cmd == "help":
            click.echo(
                "step [N] | until TIME_NS | get SIG | set SIG VAL | "
                "force SIG VAL | release SIG | signals | "
                "bp EXPR | bp list | bp del ID | reset"
            )

        elif cmd == "step":
            n = int(tokens[1]) if len(tokens) > 1 else 1
            snap = sim.step(n)
            if snap.breakpoint_hit is not None:
                click.echo(f"[bp {snap.breakpoint_hit} hit]  t={snap.time_ns}ns")
            else:
                click.echo(f"t={snap.time_ns}ns  cycle={sim.cycle}")

        elif cmd == "until":
            if len(tokens) < 2:
                click.echo("使い方: until TIME_NS")
                continue
            snap = sim.run_until(int(tokens[1]))
            click.echo(f"t={snap.time_ns}ns  cycle={sim.cycle}")

        elif cmd == "get":
            if len(tokens) < 2:
                click.echo("使い方: get SIGNAL_NAME")
                continue
            sig = tokens[1]
            val = sim.get(sig)
            w = sim._design.signals.get(sig)
            w_bits = w.width if w else 1
            click.echo(f"{sig} = {_fmt_val(val, w_bits)}")

        elif cmd == "set":
            if len(tokens) < 3:
                click.echo("使い方: set SIGNAL VALUE")
                continue
            sim.set(tokens[1], int(tokens[2], 0))
            click.echo(f"{tokens[1]} = {tokens[2]}")

        elif cmd == "force":
            if len(tokens) < 3:
                click.echo("使い方: force SIGNAL VALUE")
                continue
            sim.force(tokens[1], int(tokens[2], 0))
            click.echo(f"force {tokens[1]} = {tokens[2]}")

        elif cmd == "release":
            if len(tokens) < 2:
                click.echo("使い方: release SIGNAL")
                continue
            sim.release(tokens[1])
            click.echo(f"release {tokens[1]}")

        elif cmd == "signals":
            for s in sim.list_signals():
                direction = f"({s['direction']})" if s["direction"] else ""
                click.echo(f"  {s['name']:<30} {s['width']:3}b {direction:10} = {s['value']}")

        elif cmd == "bp":
            if len(tokens) == 1:
                click.echo("使い方: bp EXPR | bp list | bp del ID")
            elif tokens[1].lower() == "list":
                bps = sim.list_breakpoints()
                if not bps:
                    click.echo("(ブレークポイントなし)")
                for bp in bps:
                    click.echo(f"  [{bp['id']}] {bp['condition']}")
            elif tokens[1].lower() == "del":
                if len(tokens) < 3:
                    click.echo("使い方: bp del ID")
                else:
                    sim.remove_breakpoint(int(tokens[2]))
                    click.echo(f"bp {tokens[2]} 削除")
            else:
                expr = " ".join(tokens[1:])
                bp_id = sim.add_breakpoint(expr)
                click.echo(f"bp [{bp_id}] 追加: {expr}")

        elif cmd == "reset":
            sim.reset()
            click.echo("シミュレーションをリセットしました")

        else:
            click.echo(f"不明なコマンド: {cmd!r}  (help で一覧表示)")


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
