"""deltasim/interp/session.py — 対話デバッグセッション（§18 改訂版）

Questa 風コマンド:
  run <time>|<n> [ns|ps|us]  run -all  run -continue
  examine [-radix bin|hex|dec|unsigned|signed] <signal>
  force <signal> <value>   noforce <signal>
  restart   do <file>   quit   stop
  bp <file>:<line> | bp -time <t> | bp -list | bp -delete <id>
  when {<cond>} {<cmds>}

§18.1 対応:
- bp <file>:<line>: ブレークポイント登録 → 実行前にチェック（BreakpointHit）
- run <time>: 正確な ns 単位で停止（step_to_ns 使用）
- when: 信号変化ごとに評価、False→True 遷移で発火
         信号名のみの条件は変化ごとに発火
- run -continue: 次のブレークポイントまたは終了まで
- run -all: 終了まで実行（ブレークポイントで停止）
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from deltasim.interp.expr import eval_bool, eval_expr, ExprError
from deltasim.interp.tcl import MiniTcl, TclError


# ---------------------------------------------------------------------------
# 時間変換
# ---------------------------------------------------------------------------

def _to_ns(val: float, unit: str) -> int:
    unit = unit.lower().strip()
    if unit == "ps":
        return max(0, int(val // 1000))
    if unit == "us":
        return int(val * 1000)
    return int(val)  # ns


def _parse_time_ns(s: str) -> int:
    s = s.strip()
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ns|ps|us)?", s, re.IGNORECASE)
    if not m:
        raise ValueError(f"時間形式が不正: {s!r}")
    return _to_ns(float(m.group(1)), m.group(2) or "ns")


# ---------------------------------------------------------------------------
# 表示ユーティリティ
# ---------------------------------------------------------------------------

def _fmt_with_radix(val: Any, width: int, radix: str) -> str:
    from deltasim.result import Z_VALUE
    if val is None:
        return "x"
    if val is Z_VALUE:
        return "z"
    radix = radix.lower()
    if radix == "bin":
        return format(val & ((1 << width) - 1), f"0{width}b")
    if radix == "hex":
        nibbles = max(1, (width + 3) // 4)
        return format(val & ((1 << width) - 1), f"0{nibbles}X")
    if radix in ("dec", "unsigned"):
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

_bp_counter = 0


def _next_bp_id() -> int:
    global _bp_counter
    _bp_counter += 1
    return _bp_counter


class Breakpoint:
    def __init__(self, kind: str, **kwargs):
        self.id   = _next_bp_id()
        self.kind = kind
        self.kwargs = kwargs
        self.hit_count = 0

    def label(self) -> str:
        if self.kind == "time":
            return f"@ {self.kwargs['time_ns']}ns"
        if self.kind == "when":
            return f"when {{{self.kwargs['cond']!r}}} ..."
        if self.kind == "line":
            return f"{self.kwargs.get('file', '<anon>')}:{self.kwargs.get('line', '?')}"
        return str(self.kwargs)


# ---------------------------------------------------------------------------
# SimSession
# ---------------------------------------------------------------------------

class SimSession:
    """§18 Questa 風コマンドセッション"""

    def __init__(self, sim, max_loop: int = 1_000_000):
        from deltasim.api import Simulator
        self._sim: Simulator = sim
        self._breakpoints: List[Breakpoint] = []  # time / line BPs
        self._whens: List[Breakpoint]       = []  # when BPs
        self._output_lines: List[str]       = []
        self._set_signal_warned = False           # §18.7 one-time set→force warning
        self._paused_bp: Optional[Breakpoint] = None  # BP that caused pause

        # Signal change tracking for when
        self._prev_sig_snap: Dict[str, Any]  = {}
        self._when_prev_bool: Dict[int, bool] = {}  # when id → previous condition result

        self._tcl = MiniTcl(max_loop=max_loop)
        self._register_commands()

    # ----------------------------------------------------------------
    # Output
    # ----------------------------------------------------------------

    def _out(self, msg: str) -> None:
        import click as _click
        _click.echo(msg)
        self._output_lines.append(msg)

    # ----------------------------------------------------------------
    # Command registration
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
            "stop":    self._cmd_stop,
            "bp":      self._cmd_bp,
            "when":    self._cmd_when,
            "puts":    self._cmd_puts_session,
            # backward compat
            "step":    self._cmd_step_compat,
            "until":   self._cmd_until_compat,
            "get":     self._cmd_get_compat,
            "set":     self._cmd_set_compat,
            "release": self._cmd_release_compat,
            "signals": self._cmd_signals_compat,
            "reset":   self._cmd_restart,
            "help":    self._cmd_help,
            "ask":     self._cmd_ask,       # §18.3 LLM
        }.items():
            self._tcl.register_command(name, fn)

    def _cmd_puts_session(self, args: List[str]) -> str:
        text_args = list(args)
        if text_args and text_args[0] == "-nonewline":
            text_args = text_args[1:]
        if text_args and text_args[0] in ("stdout", "stderr"):
            text_args = text_args[1:]
        self._out(" ".join(text_args))
        return ""

    # ----------------------------------------------------------------
    # run
    # ----------------------------------------------------------------

    def _cmd_run(self, args: List[str]) -> str:
        if not args:
            self._advance_to_ns(self._sim._engine._time + 2 * self._sim._engine.half_period)
            return ""
        a0 = args[0].lower()
        if a0 == "-all":
            self._run_until_end()
            return ""
        if a0 == "-continue":
            self._run_until_bp_or_end()
            return ""
        # parse time
        time_str = "".join(args[:2]) if len(args) >= 2 and not args[1].startswith("-") else args[0]
        m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ns|ps|us)?", time_str.strip(), re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = (m.group(2) or "ns").lower()
            target_ns = self._sim._engine._time + _to_ns(val, unit)
            self._advance_to_ns(target_ns)
        else:
            raise TclError(f"run: 不正な引数 {args!r}")
        return ""

    def _advance_to_ns(self, target_ns: int) -> bool:
        """target_ns まで進め、各ハーフサイクルで when/bp をチェックする。

        time BP の時刻も考慮してそこで必ず止まる。
        """
        from deltasim.sim.engine import BreakpointHit

        hp = self._sim._engine.half_period
        bp_check = self._make_bp_check()

        while self._sim._engine._time < target_ns:
            t_now = self._sim._engine._time
            # 次のハーフサイクル境界
            half_step = hp
            next_half = t_now + half_step

            # time BP があればその時刻でも止まる
            time_bp_times = [b.kwargs["time_ns"] for b in self._breakpoints
                             if b.kind == "time" and b.kwargs["time_ns"] > t_now]
            if time_bp_times:
                nearest_bp = min(time_bp_times)
                next_half = min(next_half, nearest_bp)

            next_ns = min(target_ns, next_half)
            try:
                snap = self._sim.step_to_ns(next_ns, bp_check=bp_check)
            except BreakpointHit as bh:
                bl = bh.source_file.rsplit("/", 1)[-1] if bh.source_file else "?"
                self._out(f"# bp [{bh.bp_id}] {bl}:{bh.source_line} t={bh.time_ns}ns")
                self._paused_bp = next((b for b in self._breakpoints if b.id == bh.bp_id), None)
                return True

            self._check_all_whens(snap)
            if self._check_time_bps_after(snap):
                return True

        self._out(f"# t={self._sim._engine._time}ns  cycle={self._sim.cycle}")
        return False

    def _run_until_end(self) -> None:
        """run -all: 終了まで実行（ブレークポイントで停止）。"""
        hp = self._sim._engine.half_period
        MAX = 100_000
        for _ in range(MAX):
            target = self._sim._engine._time + 2 * hp
            stopped = self._advance_to_ns(target)
            if stopped:
                return
        self._out(f"# run -all 完了: t={self._sim._engine._time}ns")

    def _run_until_bp_or_end(self) -> None:
        """run -continue: 次のブレークポイントまたは終了まで。"""
        self._run_until_end()

    def _make_bp_check(self):
        """行ブレークポイントチェック用コールバックを返す（deferred 方式）。

        §18.1.1: 即時 raise せず engine._deferred_bp_hit に記録する。
        これにより NBA・negedge・VCD の処理が完了してから bp を通知できる。
        """
        engine = self._sim._engine

        def _check(file: str, line: int):
            if engine._deferred_bp_hit is not None:
                return  # already hit in this time step
            t = engine._time
            for bp in self._breakpoints:
                if bp.kind != "line":
                    continue
                bp_file = bp.kwargs.get("file", "")
                bp_line = bp.kwargs.get("line", 0)
                if bp_line == line and (
                    not bp_file or bp_file in file or file.endswith(bp_file)
                    or (bp_file and file.endswith(bp_file.replace("\\", "/")))
                ):
                    bp.hit_count += 1
                    engine._deferred_bp_hit = (bp.id, file, line, t)
                    return
        return _check

    # ----------------------------------------------------------------
    # when
    # ----------------------------------------------------------------

    def _cmd_when(self, args: List[str]) -> str:
        """when {<cond>} {<cmds>}"""
        if len(args) < 2:
            raise TclError("when: 条件とコマンドが必要です")
        cond, cmds = args[0], args[1]
        bp = Breakpoint("when", cond=cond, cmds=cmds)
        self._when_prev_bool[bp.id] = False  # 初期値: 偽
        self._whens.append(bp)
        self._out(f"# when [{bp.id}] 登録: {cond!r}")
        return str(bp.id)

    def _check_all_whens(self, snap) -> None:
        """signal変化ごとに when 条件を評価し、False→True 遷移で発火。"""
        env = {k: v for k, v in snap.signals.items() if v is not None}
        prev_env = self._prev_sig_snap
        changed_sigs = {k for k in snap.signals
                        if snap.signals.get(k) != prev_env.get(k)}

        for bp in list(self._whens):
            cond = bp.kwargs["cond"].strip()
            try:
                # 信号名だけの条件は「変化したら発火」
                if re.fullmatch(r'\w+', cond) and cond in self._sim._design.signals:
                    if cond in changed_sigs:
                        bp.hit_count += 1
                        self._out(f"# when [{bp.id}] 発火 (変化): {cond}")
                        self._tcl.eval(bp.kwargs["cmds"])
                    continue

                # 通常条件: False→True 遷移で発火
                cur_val = eval_bool(cond, env)
                prev_val = self._when_prev_bool.get(bp.id, False)
                self._when_prev_bool[bp.id] = cur_val
                if cur_val and not prev_val:
                    bp.hit_count += 1
                    self._out(f"# when [{bp.id}] 発火: {cond!r}")
                    self._tcl.eval(bp.kwargs["cmds"])
            except _StopSignal:
                self._prev_sig_snap = dict(snap.signals)
                raise  # propagate to caller so stop_count can be tracked
            except (TclError, ExprError, Exception):
                pass

        self._prev_sig_snap = dict(snap.signals)

    def _check_time_bps_after(self, snap) -> bool:
        """時刻ブレークポイントをチェック。ヒットしたら True。"""
        t = self._sim._engine._time
        for bp in self._breakpoints:
            if bp.kind == "time" and bp.kwargs.get("time_ns") == t:
                bp.hit_count += 1
                self._out(f"# bp [{bp.id}] t={t}ns")
                self._paused_bp = bp
                return True
        return False

    # ----------------------------------------------------------------
    # examine
    # ----------------------------------------------------------------

    def _cmd_examine(self, args: List[str]) -> str:
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
        if not args:
            raise TclError("noforce: 信号名が必要です")
        self._sim.release(args[0])
        self._out(f"# noforce {args[0]}")
        return ""

    # ----------------------------------------------------------------
    # restart
    # ----------------------------------------------------------------

    def _cmd_restart(self, args: List[str]) -> str:
        self._sim.reset()
        self._prev_sig_snap.clear()
        self._when_prev_bool.clear()
        self._paused_bp = None
        self._out("# restart: t=0ns")
        return ""

    # ----------------------------------------------------------------
    # do
    # ----------------------------------------------------------------

    def _cmd_do(self, args: List[str]) -> str:
        if not args:
            raise TclError("do: ファイル名が必要です")
        path = Path(args[0])
        if not path.exists():
            raise TclError(f"do: ファイルが見つかりません: {path}")
        script = path.read_text(encoding="utf-8")
        return self._tcl.eval(script)

    # ----------------------------------------------------------------
    # quit / stop
    # ----------------------------------------------------------------

    class _QuitSignal(Exception):
        pass

    def _cmd_quit(self, args: List[str]) -> str:
        raise SimSession._QuitSignal()

    def _cmd_stop(self, args: List[str]) -> str:
        """stop: 現在位置で停止（when 本体内で使用）"""
        raise _StopSignal()

    # ----------------------------------------------------------------
    # bp
    # ----------------------------------------------------------------

    def _cmd_bp(self, args: List[str]) -> str:
        if not args:
            return self._list_bps()
        a0 = args[0].lower()
        if a0 == "-list":
            return self._list_bps()
        if a0 in ("-delete", "-remove"):
            if len(args) < 2:
                raise TclError("bp -delete: ID が必要です")
            bp_id = int(args[1])
            before = len(self._breakpoints)
            self._breakpoints = [b for b in self._breakpoints if b.id != bp_id]
            self._whens = [b for b in self._whens if b.id != bp_id]
            if len(self._breakpoints) + len(self._whens) < before:
                self._out(f"# bp {bp_id} 削除")
            return ""
        if a0 == "-time":
            if len(args) < 2:
                raise TclError("bp -time: 時間が必要です")
            try:
                t_ns = _parse_time_ns(" ".join(args[1:3]))
            except ValueError as e:
                raise TclError(str(e))
            bp = Breakpoint("time", time_ns=t_ns)
            self._breakpoints.append(bp)
            self._out(f"# bp [{bp.id}] @ {t_ns}ns")
            return str(bp.id)
        # file:line
        m = re.match(r"^(.+):([0-9]+)$", args[0])
        if m:
            fname, lineno = m.group(1), int(m.group(2))
            actual_line = self._snap_to_statement_line(fname, lineno)
            if actual_line is None:
                # 後ろに文が一つもない → エラー、登録しない
                self._out(
                    f"# Error: {fname}:{lineno} より後ろに文がありません。"
                    f"ブレークポイントを登録しませんでした。"
                )
                return ""
            if actual_line != lineno:
                self._out(
                    f"# Warning: {fname}:{lineno} には文がありません。"
                    f"{fname}:{actual_line} に設定しました。"
                )
            bp = Breakpoint("line", file=fname, line=actual_line)
            self._breakpoints.append(bp)
            self._out(f"# bp [{bp.id}] {fname}:{actual_line}")
            return str(bp.id)
        raise TclError(f"bp: 不明な引数 {args!r}")

    def _collect_stmt_lines(self, fname: str) -> list:
        """設計の AST から、指定ファイルに含まれる文の行番号リストを返す。"""
        stmt_lines: list[int] = []

        def _collect(node):
            if node is None:
                return
            sl = getattr(node, "source_line", 0)
            sf = getattr(node, "source_file", "")
            if sl and (not fname or sf.endswith(fname) or fname in sf
                       or (fname and fname in sf.replace("\\", "/"))):
                stmt_lines.append(sl)
            for attr in ("stmts", "then_body", "else_body", "body", "init", "update"):
                child = getattr(node, attr, None)
                if child is not None:
                    if isinstance(child, list):
                        for c in child:
                            _collect(c)
                    else:
                        _collect(child)
            if hasattr(node, "items"):
                for item in node.items:
                    _collect(getattr(item, "body", None))

        for proc in (self._sim._design.seq_procs +
                     self._sim._design.comb_procs):
            _collect(getattr(proc, "body", None))

        # assign_procs: source_line is on the proc itself (no body to recurse into)
        for proc in getattr(self._sim._design, "assign_procs", []):
            sl = getattr(proc, "source_line", 0)
            sf = getattr(proc, "source_file", "")
            if sl and (not fname or sf.endswith(fname) or fname in sf
                       or (fname and fname in sf.replace("\\", "/"))):
                stmt_lines.append(sl)

        return sorted(set(stmt_lines))

    def _snap_to_statement_line(self, fname: str, line: int) -> Optional[int]:
        """指定行に文がなければ後ろの最近傍行に寄せる。

        Returns:
            行番号 (int): 実際のブレークポイント行（正確な行か、寄せた行）
            None: 後ろに文が1つもない → 登録不可（エラー）
        """
        stmt_lines = self._collect_stmt_lines(fname)
        if not stmt_lines:
            return None  # ファイルに文がない → エラー

        if line in stmt_lines:
            return line  # 完全一致 → そのまま

        # 指定行より後ろ（>= line）に文があれば最も近い行に寄せる
        after = [l for l in stmt_lines if l >= line]
        if after:
            return min(after)

        # 指定行より後ろに文が1つもない → エラー（登録しない）
        return None

    def _list_bps(self) -> str:
        if not self._breakpoints and not self._whens:
            self._out("# (ブレークポイントなし)")
            return ""
        for b in self._breakpoints + self._whens:
            self._out(f"  [{b.id}] {b.label()}")
        return ""

    # ----------------------------------------------------------------
    # 後方互換コマンド
    # ----------------------------------------------------------------

    def _cmd_step_compat(self, args: List[str]) -> str:
        n = int(args[0]) if args else 1
        hp = self._sim._engine.half_period
        target = self._sim._engine._time + n * 2 * hp
        self._advance_to_ns(target)
        return ""

    def _cmd_until_compat(self, args: List[str]) -> str:
        if not args:
            raise TclError("until: 時間が必要です")
        self._advance_to_ns(int(args[0]))
        return ""

    def _cmd_get_compat(self, args: List[str]) -> str:
        if not args:
            raise TclError("get: 信号名が必要です")
        return self._cmd_examine([args[0]])

    def _cmd_set_compat(self, args: List[str]) -> str:
        """§18.1.1: set は常に Tcl 変数に代入する。
        信号名と一致しても信号には触らない（force を使うこと）。
        初回のみ警告を出す。
        """
        if len(args) >= 2:
            try:
                if args[0] in self._sim.list_signals_dict():
                    if not self._set_signal_warned:
                        self._out(
                            f"# Warning: 信号に値を入れるには "
                            f"'force {args[0]} {args[1]}' を使ってください。"
                            f" 'set' は Tcl 変数への代入です（信号は変わりません）。"
                        )
                        self._set_signal_warned = True
            except Exception:
                pass
        # 常に Tcl 変数に代入（信号には触らない）
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
            "restart   stop\n"
            "bp FILE:LINE | bp -time T[ns] | bp -list | bp -delete ID\n"
            "when {COND} {CMDS}\n"
            "do FILE\n"
            "quit / exit\n"
            "--- Tcl ---\n"
            "set VAR VALUE   if {COND} {BODY}   for {INIT} {COND} {INCR} {BODY}\n"
            "foreach VAR LIST BODY   while {COND} {BODY}\n"
            "expr EXPR   puts STRING   proc NAME {PARAMS} {BODY}\n"
            "--- 後方互換 ---\n"
            "step [N]   until TIME_NS   get SIGNAL   signals\n"
            "--- LLM ---\n"
            "ask \"<文>\"   — 自然言語で対話コマンドを提案する（simconfig.yaml 要）"
        )
        self._out(msg)
        return ""

    def _cmd_ask(self, args: List[str]) -> str:
        """§18.3 ask コマンド: 対話セッション内で LLM にコマンドを提案させる。"""
        if not args:
            self._out("使い方: ask \"<指示文>\"")
            return ""
        text = " ".join(args)
        try:
            from deltasim.llm.config import load_llm_config
            from deltasim.llm.client import call_llm, LLMConnectError
            from deltasim.llm.safety import check_session_command, UnsafeCommandError

            cfg = load_llm_config()
            if cfg is None:
                self._out(
                    "ERROR (E_LLM_NOT_CONFIGURED): LLM が未設定です。"
                    "simconfig.yaml の llm を設定してください。"
                )
                return ""

            # 対話セッションで許可するコマンド
            SESSION_CMDS = frozenset({
                "run", "examine", "force", "noforce", "restart", "bp",
                "when", "step", "until", "get", "signals",
            })

            prompt = (
                f"deltasim 対話セッションのコマンドを1行だけ提案してください。\n"
                f"使えるコマンド: {sorted(SESSION_CMDS)}\n"
                f"シェル記号は使わないこと。\n"
                f"指示: {text}"
            )
            raw = call_llm(prompt, cfg)
            try:
                cmd, rest = check_session_command(raw, SESSION_CMDS)
            except UnsafeCommandError as exc:
                self._out(f"ERROR (E_LLM_UNSAFE_CMD): {exc}")
                return ""

            proposal = f"{cmd} {rest}".strip()
            self._out(f"提案: {proposal}")
            import sys
            if sys.stdin.isatty():
                try:
                    ans = input("実行しますか？ [y/N] ").strip().lower()
                except EOFError:
                    ans = "n"
            else:
                ans = "y"  # non-interactive: auto-accept

            if ans in ("y", "yes"):
                self._tcl.eval(proposal)
            else:
                self._out("実行しませんでした")
        except Exception as exc:
            self._out(f"ERROR: {exc}")
        return ""

    # ----------------------------------------------------------------
    # REPL / スクリプト実行
    # ----------------------------------------------------------------

    def run_repl(self, prompt: str = "vsim> ") -> None:
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
        self._output_lines.clear()
        self._tcl.eval(script)
        return list(self._output_lines)


# ---------------------------------------------------------------------------
# stop シグナル（when 内の stop コマンド用）
# ---------------------------------------------------------------------------

class _StopSignal(Exception):
    pass
