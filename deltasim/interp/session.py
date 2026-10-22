"""deltasim/interp/session.py — 対話デバッグセッション（§18）

Questa 風コマンド:
  run <time>|<n> [ns|ps|us]  run -all  run -continue
  examine [-radix bin|hex|dec|unsigned|signed] <signal>
  force <signal> <value>   noforce <signal>
  restart   do <file>   quit
  bp <file>:<line> | bp -time <t> | bp -list | bp -delete <id>
  when {<cond>} {<cmds>}

時間単位: ns(default), ps, us を受け付けて ns に変換する。
ブレークポイント: 行番号ベース(ソースポジション)、時刻ベース。
"""
from __future__ import annotations
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from deltasim.interp.expr import eval_bool, eval_expr, ExprError
from deltasim.interp.tcl import MiniTcl, TclError


# ---------------------------------------------------------------------------
# 時間変換
# ---------------------------------------------------------------------------

_TIME_UNIT = {"ns": 1, "ps": 1000, "us": 1000_000}   # → ns 分母


def _parse_time_ns(s: str) -> int:
    """'10ns', '5 us', '100 ps', '10' → ns に変換した整数を返す。"""
    s = s.strip()
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ns|ps|us)?", s, re.IGNORECASE)
    if not m:
        raise ValueError(f"時間形式が不正: {s!r}  (例: 10ns / 5us / 100ps)")
    val = float(m.group(1))
    unit = (m.group(2) or "ns").lower()
    divisor = _TIME_UNIT[unit]
    return max(0, int(val / divisor * 1)) if unit == "ns" else max(0, int(val // divisor))


def _to_ns(val: float, unit: str) -> int:
    """単位付き数値を ns 整数に変換。"""
    unit = unit.lower().strip()
    if unit == "ps":
        return max(0, int(val // 1000))
    if unit == "us":
        return int(val * 1000)
    return int(val)  # ns


# ---------------------------------------------------------------------------
# 表示用ユーティリティ
# ---------------------------------------------------------------------------

def _fmt_with_radix(val: Any, width: int, radix: str) -> str:
    from deltasim.result import val_to_4state, Z_VALUE
    if val is None:
        return f"{'x' * max(1, (width + 3) // 4)}" if radix == "hex" else "x"
    if val is Z_VALUE:
        return f"{'z' * max(1, (width + 3) // 4)}" if radix == "hex" else "z"
    radix = radix.lower()
    if radix == "bin":
        return format(val & ((1 << width) - 1), f"0{width}b")
    if radix == "hex":
        nibbles = max(1, (width + 3) // 4)
        return format(val & ((1 << width) - 1), f"0{nibbles}X")
    if radix == "dec" or radix == "unsigned":
        return str(val & ((1 << width) - 1))
    if radix == "signed":
        v = val & ((1 << width) - 1)
        if v >> (width - 1):
            v -= (1 << width)
        return str(v)
    return str(val)


# ---------------------------------------------------------------------------
# ブレークポイント
# ---------------------------------------------------------------------------

class Breakpoint:
    _next_id = 1

    def __init__(self, kind: str, **kwargs):
        self.id   = Breakpoint._next_id
        Breakpoint._next_id += 1
        self.kind = kind   # "time" | "when" | "line"
        self.kwargs = kwargs
        self.hit_count = 0

    def __repr__(self):
        return f"Breakpoint(id={self.id}, kind={self.kind}, {self.kwargs})"

    def label(self) -> str:
        if self.kind == "time":
            return f"@ {self.kwargs['time_ns']}ns"
        if self.kind == "when":
            return f"when {{{self.kwargs['cond']!r}}} {{{self.kwargs['cmds']!r}}}"
        if self.kind == "line":
            return f"{self.kwargs.get('file', '<anon>')}:{self.kwargs.get('line', '?')}"
        return str(self.kwargs)


# ---------------------------------------------------------------------------
# SimSession
# ---------------------------------------------------------------------------

class SimSession:
    """Questa 風コマンドセッション。

    Simulator オブジェクトをラップし、TclInterp に deltasim コマンドを登録する。
    stdin からスクリプトを流す E2E 用途にも対応（eval_script）。
    """

    def __init__(self, sim, max_loop: int = 1_000_000):
        from deltasim.api import Simulator
        self._sim: Simulator    = sim
        self._breakpoints: List[Breakpoint] = []
        self._whens: List[Breakpoint]       = []
        self._running = False
        self._output_lines: List[str]       = []   # テスト用に出力を蓄積

        # Tcl インタープリタ
        self._tcl = MiniTcl(max_loop=max_loop)
        self._register_commands()

    # ----------------------------------------------------------------
    # 出力
    # ----------------------------------------------------------------

    def _out(self, msg: str) -> None:
        """REPL 出力。テスト時は _output_lines にも蓄積する。"""
        import click as _click
        _click.echo(msg)
        self._output_lines.append(msg)

    # ----------------------------------------------------------------
    # Tcl コマンド登録
    # ----------------------------------------------------------------

    def _register_commands(self) -> None:
        for name, fn in {
            "run":     self._cmd_run,
            "examine": self._cmd_examine,
            "force":   self._cmd_force,
            "noforce": self._cmd_noforce,
            "restart": self._cmd_restart,
            "do":      self._cmd_do,
            "quit":    self._cmd_quit,
            "exit":    self._cmd_quit,
            "bp":      self._cmd_bp,
            "when":    self._cmd_when,
            "puts":    self._cmd_puts_session,  # session._out 経由で _output_lines に追加
            # 旧インタフェース（後方互換）
            "step":    self._cmd_step_compat,
            "until":   self._cmd_until_compat,
            "get":     self._cmd_get_compat,
            "set":     self._cmd_set_compat,
            "release": self._cmd_release_compat,
            "signals": self._cmd_signals_compat,
            "reset":   self._cmd_restart,
            "help":    self._cmd_help,
        }.items():
            self._tcl.register_command(name, fn)

    def _cmd_puts_session(self, args: List[str]) -> str:
        """puts コマンドの session オーバーライド。_out() 経由で _output_lines にも記録。"""
        # 引数を Tcl の puts と同様に処理
        text_args = list(args)
        if text_args and text_args[0] == "-nonewline":
            text_args = text_args[1:]
        if text_args and text_args[0] in ("stdout", "stderr"):
            text_args = text_args[1:]
        text = " ".join(text_args)
        self._out(text)
        return ""

    # ----------------------------------------------------------------
    # run
    # ----------------------------------------------------------------

    def _cmd_run(self, args: List[str]) -> str:
        """run <N>[ns|ps|us]  |  run <N>  |  run -all  |  run -continue"""
        if not args:
            self._advance_cycles(1)
            return ""
        a0 = args[0].lower()
        if a0 == "-all":
            self._run_all()
            return ""
        if a0 == "-continue":
            self._run_continue()
            return ""
        # "run 10 ns" or "run 10ns"
        time_str = "".join(args[:2]) if len(args) >= 2 and not args[1].startswith("-") else args[0]
        # try cycles first (plain integer)
        m = re.fullmatch(r"([0-9]+)(ns|ps|us)?", time_str.strip(), re.IGNORECASE)
        if m:
            val = int(m.group(1))
            unit = (m.group(2) or "ns").lower()
            target_ns = self._sim._engine._time + _to_ns(val, unit)
            self._run_to(target_ns)
        else:
            raise TclError(f"run: 不正な引数 {args!r}")
        return ""

    def _run_to(self, target_ns: int) -> None:
        """target_ns までシミュレーションを進める（ブレークポイントで中断）。"""
        while self._sim._engine._time < target_ns:
            snap = self._sim.step(1)
            self._check_whens(snap)
            bp = self._check_time_bps()
            if bp is not None:
                self._out(f"[bp {bp.id} hit @ {self._sim._engine._time}ns]")
                return
            if snap.breakpoint_hit is not None:
                self._out(f"[bp {snap.breakpoint_hit} hit]  t={snap.time_ns}ns")
                return
        self._out(f"# t={self._sim._engine._time}ns  cycle={self._sim.cycle}")

    def _run_all(self) -> None:
        """シミュレーション終了まで実行（デフォルト最大サイクル数 or エラー）。"""
        MAX_CYCLES = 100_000
        for _ in range(MAX_CYCLES):
            snap = self._sim.step(1)
            self._check_whens(snap)
            bp = self._check_time_bps()
            if bp is not None:
                self._out(f"[bp {bp.id} hit @ {self._sim._engine._time}ns]")
                return
            if snap.breakpoint_hit is not None:
                self._out(f"[bp {snap.breakpoint_hit} hit]  t={snap.time_ns}ns")
                return
        self._out(f"# run -all 完了: t={self._sim._engine._time}ns")

    def _run_continue(self) -> None:
        """次のブレークポイントまで実行。"""
        self._run_all()

    def _advance_cycles(self, n: int) -> None:
        snap = self._sim.step(n)
        self._check_whens(snap)
        self._out(f"# t={snap.time_ns}ns  cycle={self._sim.cycle}")

    # ----------------------------------------------------------------
    # examine
    # ----------------------------------------------------------------

    def _cmd_examine(self, args: List[str]) -> str:
        """examine [-radix bin|hex|dec|unsigned|signed] <signal>"""
        radix = "hex"
        sig_args = list(args)
        if sig_args and sig_args[0] == "-radix":
            sig_args.pop(0)
            if sig_args:
                radix = sig_args.pop(0).lower()
        if not sig_args:
            raise TclError("examine: 信号名が必要です")
        sig = sig_args[0]
        val = self._sim.get(sig)
        w_info = self._sim._design.signals.get(sig)
        width = w_info.width if w_info else 1
        formatted = _fmt_with_radix(val, width, radix)
        self._out(f"{sig} = {formatted}")
        return formatted

    # ----------------------------------------------------------------
    # force / noforce
    # ----------------------------------------------------------------

    def _cmd_force(self, args: List[str]) -> str:
        """force <signal> <value>"""
        if len(args) < 2:
            raise TclError("force: 信号名と値が必要です")
        sig, val_str = args[0], args[1]
        try:
            val = int(val_str, 0)
        except ValueError:
            raise TclError(f"force: 不正な値 {val_str!r}")
        self._sim.force(sig, val)
        self._out(f"# force {sig} = {val_str}")
        return ""

    def _cmd_noforce(self, args: List[str]) -> str:
        """noforce <signal>"""
        if not args:
            raise TclError("noforce: 信号名が必要です")
        self._sim.release(args[0])
        self._out(f"# noforce {args[0]}")
        return ""

    # ----------------------------------------------------------------
    # restart
    # ----------------------------------------------------------------

    def _cmd_restart(self, args: List[str]) -> str:
        """restart — シミュレーションをリセットする。"""
        self._sim.reset()
        self._out("# restart: t=0ns")
        return ""

    # ----------------------------------------------------------------
    # do
    # ----------------------------------------------------------------

    def _cmd_do(self, args: List[str]) -> str:
        """do <file> — Tcl スクリプトファイルを実行する。"""
        if not args:
            raise TclError("do: ファイル名が必要です")
        from pathlib import Path
        path = Path(args[0])
        if not path.exists():
            raise TclError(f"do: ファイルが見つかりません: {path}")
        script = path.read_text(encoding="utf-8")
        return self._tcl.eval(script)

    # ----------------------------------------------------------------
    # quit
    # ----------------------------------------------------------------

    class _QuitSignal(Exception):
        pass

    def _cmd_quit(self, args: List[str]) -> str:
        raise SimSession._QuitSignal()

    # ----------------------------------------------------------------
    # bp (breakpoints)
    # ----------------------------------------------------------------

    def _cmd_bp(self, args: List[str]) -> str:
        """bp <file>:<line> | bp -time <t> | bp -list | bp -delete <id>"""
        if not args:
            # list
            return self._list_bps()
        a0 = args[0].lower()
        if a0 == "-list":
            return self._list_bps()
        if a0 == "-delete" or a0 == "-remove":
            if len(args) < 2:
                raise TclError("bp -delete: ID が必要です")
            bp_id = int(args[1])
            before = len(self._breakpoints)
            self._breakpoints = [b for b in self._breakpoints if b.id != bp_id]
            if len(self._breakpoints) < before:
                self._out(f"# bp {bp_id} 削除")
            else:
                self._out(f"# bp {bp_id} が見つかりません")
            return ""
        if a0 == "-time":
            if len(args) < 2:
                raise TclError("bp -time: 時間が必要です")
            time_str = "".join(args[1:3])
            try:
                t_ns = int(_parse_time_ns(time_str))
            except ValueError as e:
                raise TclError(str(e))
            bp = Breakpoint("time", time_ns=t_ns)
            self._breakpoints.append(bp)
            self._out(f"# bp [{bp.id}] @ {t_ns}ns")
            return str(bp.id)
        # file:line 形式
        loc = args[0]
        m = re.match(r"^(.+):([0-9]+)$", loc)
        if m:
            fname, lineno = m.group(1), int(m.group(2))
            bp = Breakpoint("line", file=fname, line=lineno)
            self._breakpoints.append(bp)
            self._out(f"# bp [{bp.id}] {fname}:{lineno}")
            return str(bp.id)
        raise TclError(f"bp: 不明な引数 {args!r}  (例: bp foo.sv:10 | bp -time 100ns | bp -list)")

    def _list_bps(self) -> str:
        if not self._breakpoints and not self._whens:
            self._out("# (ブレークポイントなし)")
            return ""
        for b in self._breakpoints + self._whens:
            self._out(f"  [{b.id}] {b.label()}")
        return ""

    # ----------------------------------------------------------------
    # when
    # ----------------------------------------------------------------

    def _cmd_when(self, args: List[str]) -> str:
        """when {<cond>} {<cmds>}"""
        if len(args) < 2:
            raise TclError("when: 条件とコマンドが必要です")
        cond, cmds = args[0], args[1]
        bp = Breakpoint("when", cond=cond, cmds=cmds)
        self._whens.append(bp)
        self._out(f"# when [{bp.id}] 登録: {cond!r}")
        return str(bp.id)

    def _check_whens(self, snap) -> None:
        """when 条件をチェックして満たされたコマンドを実行する。"""
        env = {k: v for k, v in snap.signals.items() if v is not None}
        for bp in list(self._whens):
            try:
                if eval_bool(bp.kwargs["cond"], env):
                    bp.hit_count += 1
                    self._out(f"# when [{bp.id}] 発火: {bp.kwargs['cond']!r}")
                    self._tcl.eval(bp.kwargs["cmds"])
            except (ExprError, TclError, Exception):
                pass

    def _check_time_bps(self) -> Optional[Breakpoint]:
        """現在時刻と一致する時刻ブレークポイントを返す（最初の1つ）。"""
        t = self._sim._engine._time
        for bp in self._breakpoints:
            if bp.kind == "time" and bp.kwargs.get("time_ns") == t:
                bp.hit_count += 1
                return bp
        return None

    # ----------------------------------------------------------------
    # 後方互換コマンド（旧 sim サブコマンド）
    # ----------------------------------------------------------------

    def _cmd_step_compat(self, args: List[str]) -> str:
        n = int(args[0]) if args else 1
        self._advance_cycles(n)
        return ""

    def _cmd_until_compat(self, args: List[str]) -> str:
        if not args:
            raise TclError("until: 時間が必要です")
        t = int(args[0])
        self._run_to(t)
        return ""

    def _cmd_get_compat(self, args: List[str]) -> str:
        if not args:
            raise TclError("get: 信号名が必要です")
        return self._cmd_examine([args[0]])

    def _cmd_set_compat(self, args: List[str]) -> str:
        # set SIGNAL VALUE (sim互換) vs Tcl set varname value
        # 引数が2つかつ最初の引数が信号名と一致する場合は force 相当
        if len(args) >= 2:
            try:
                sig_dict = self._sim.list_signals_dict()
                if args[0] in sig_dict:
                    return self._cmd_force(args)
            except Exception:
                pass
        # Tcl の組み込み set を直接呼ぶ（_tcl._cmd_set を呼ぶのは安全）
        return self._tcl._cmd_set(args)

    def _cmd_release_compat(self, args: List[str]) -> str:
        return self._cmd_noforce(args)

    def _cmd_signals_compat(self, args: List[str]) -> str:
        for s in self._sim.list_signals():
            direction = f"({s['direction']})" if s["direction"] else ""
            self._out(f"  {s['name']:<30} {s['width']:3}b {direction:10} = {s['value']}")
        return ""

    def _cmd_help(self, args: List[str]) -> str:
        msg = (
            "--- deltasim 対話デバッグコマンド ---\n"
            "run [N[ns|ps|us]] | run -all | run -continue\n"
            "examine [-radix bin|hex|dec|unsigned|signed] SIGNAL\n"
            "force SIGNAL VALUE   noforce SIGNAL\n"
            "restart\n"
            "bp FILE:LINE | bp -time T[ns] | bp -list | bp -delete ID\n"
            "when {COND} {CMDS}\n"
            "do FILE\n"
            "quit / exit\n"
            "--- Tcl ---\n"
            "set VAR VALUE   if {COND} {BODY}   for {INIT} {COND} {INCR} {BODY}\n"
            "foreach VAR LIST BODY   while {COND} {BODY}\n"
            "expr EXPR   puts STRING   proc NAME {PARAMS} {BODY}\n"
            "--- 後方互換 ---\n"
            "step [N]   until TIME_NS   get SIGNAL   signals"
        )
        self._out(msg)
        return ""

    # ----------------------------------------------------------------
    # REPL / スクリプト実行
    # ----------------------------------------------------------------

    def run_repl(self, prompt: str = "vsim> ") -> None:
        """対話ループを起動する（stdin から読む）。"""
        import sys
        while True:
            try:
                if sys.stdin.isatty():
                    line = input(prompt)
                else:
                    line = input()
            except EOFError:
                break
            except KeyboardInterrupt:
                print("\n[中断]")
                break
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                self._tcl.eval(line)
            except SimSession._QuitSignal:
                break
            except TclError as e:
                print(f"ERROR: {e}", flush=True)
            except Exception as e:
                print(f"ERROR: {type(e).__name__}: {e}", flush=True)

    def eval_script(self, script: str) -> List[str]:
        """スクリプト文字列を評価して出力行リストを返す（E2E テスト用）。"""
        self._output_lines.clear()
        self._tcl.eval(script)
        return list(self._output_lines)
