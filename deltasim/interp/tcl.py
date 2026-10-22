"""deltasim/interp/tcl.py — ミニ Tcl インタープリタ

§18.3: Questa の do スクリプトで使われる Tcl サブセットを実装する。
Python の eval/exec は使わない。

対応コマンド: set, if, for, foreach, while, expr, puts, proc
対応構文: {} 引用, "" 引用, [] コマンド置換, $var 変数展開, ; と改行による区切り
ループ上限: MAX_LOOP_ITER（無限ループ防止）
"""
from __future__ import annotations
import re
from typing import Any, Callable, Dict, List, Optional

MAX_LOOP_ITER = 1_000_000  # ループ反復上限（既存エンジンと統一）


class TclError(Exception):
    """Tcl 実行エラー"""


class TclReturn(Exception):
    """return 文で値を伝播する"""
    def __init__(self, value: str = ""):
        self.value = value


class TclBreak(Exception):
    pass


class TclContinue(Exception):
    pass


# ---------------------------------------------------------------------------
# トークナイザ（Tcl の字句解析）
# ---------------------------------------------------------------------------

def _parse_word(src: str, pos: int, interp: "MiniTcl") -> tuple[str, int]:
    """1つの Tcl ワードを解析して (値, 新pos) を返す。"""
    n = len(src)
    if pos >= n:
        raise TclError("ワードが期待されましたが入力が終わっています")
    c = src[pos]

    # ブレース引用 { } — 変数展開なし
    if c == "{":
        depth = 1
        pos += 1
        start = pos
        while pos < n and depth > 0:
            if src[pos] == "{":
                depth += 1
            elif src[pos] == "}":
                depth -= 1
            pos += 1
        if depth != 0:
            raise TclError("閉じられていない { があります")
        return (src[start:pos - 1], pos)

    # ダブルクォート — 変数展開あり、コマンド置換あり
    if c == '"':
        pos += 1
        parts = []
        while pos < n and src[pos] != '"':
            if src[pos] == "\\":
                pos += 1
                if pos < n:
                    esc = {"n": "\n", "t": "\t", "r": "\r"}.get(src[pos], src[pos])
                    parts.append(esc)
                    pos += 1
            elif src[pos] == "$":
                val, pos = _expand_var(src, pos, interp)
                parts.append(str(val))
            elif src[pos] == "[":
                val, pos = _expand_cmd(src, pos, interp)
                parts.append(str(val))
            else:
                parts.append(src[pos])
                pos += 1
        if pos >= n:
            raise TclError("閉じられていない \" があります")
        pos += 1  # skip closing "
        return ("".join(parts), pos)

    # ブラケット置換 [ ]
    if c == "[":
        val, pos = _expand_cmd(src, pos, interp)
        return (str(val), pos)

    # 変数展開 $
    if c == "$":
        val, pos = _expand_var(src, pos, interp)
        return (str(val), pos)

    # 通常ワード（スペース/;/\n まで）
    parts = []
    while pos < n and src[pos] not in (" ", "\t", "\n", ";", "}"):
        if src[pos] == "\\":
            pos += 1
            if pos < n:
                esc = {"n": "\n", "t": "\t", "r": "\r"}.get(src[pos], src[pos])
                parts.append(esc)
                pos += 1
        elif src[pos] == "$":
            val, pos = _expand_var(src, pos, interp)
            parts.append(str(val))
        elif src[pos] == "[":
            val, pos = _expand_cmd(src, pos, interp)
            parts.append(str(val))
        else:
            parts.append(src[pos])
            pos += 1
    return ("".join(parts), pos)


def _expand_var(src: str, pos: int, interp: "MiniTcl") -> tuple[str, int]:
    """$varname または ${varname} を展開する。"""
    pos += 1  # skip $
    n = len(src)
    if pos < n and src[pos] == "{":
        pos += 1
        start = pos
        while pos < n and src[pos] != "}":
            pos += 1
        name = src[start:pos]
        if pos < n:
            pos += 1
    else:
        start = pos
        while pos < n and (src[pos].isalnum() or src[pos] in "_"):
            pos += 1
        name = src[start:pos]
    val = interp._vars.get(name, "")
    return (val, pos)


def _expand_cmd(src: str, pos: int, interp: "MiniTcl") -> tuple[str, int]:
    """[ ... ] コマンド置換を実行して (結果文字列, 新pos) を返す。"""
    pos += 1  # skip [
    depth = 1
    start = pos
    n = len(src)
    while pos < n and depth > 0:
        if src[pos] == "[":
            depth += 1
        elif src[pos] == "]":
            depth -= 1
        elif src[pos] == '"':
            pos += 1
            while pos < n and src[pos] != '"':
                if src[pos] == "\\":
                    pos += 1
                pos += 1
        pos += 1
    inner = src[start: pos - 1]
    result = interp.eval(inner)
    return (result or "", pos)


def _skip_ws(src: str, pos: int) -> int:
    n = len(src)
    while pos < n and src[pos] in (" ", "\t"):
        pos += 1
    return pos


def _parse_command(src: str, pos: int, interp: "MiniTcl") -> tuple[list, int]:
    """1つのコマンド（改行か;まで）をパースして (words, 新pos) を返す。"""
    n = len(src)
    pos = _skip_ws(src, pos)
    words = []
    while pos < n and src[pos] not in ("\n", ";"):
        pos = _skip_ws(src, pos)
        if pos >= n or src[pos] in ("\n", ";"):
            break
        if src[pos] == "#" and not words:
            # コメント行
            while pos < n and src[pos] != "\n":
                pos += 1
            break
        word, pos = _parse_word(src, pos, interp)
        words.append(word)
        pos = _skip_ws(src, pos)
    # skip separator
    if pos < n and src[pos] in ("\n", ";"):
        pos += 1
    return (words, pos)


# ---------------------------------------------------------------------------
# ミニ Tcl インタープリタ
# ---------------------------------------------------------------------------

class MiniTcl:
    """§18.3 対応の最小限 Tcl インタープリタ。

    コマンドハンドラは register_command() で登録する（deltasim コマンドを含む）。
    """

    def __init__(self, max_loop: int = MAX_LOOP_ITER):
        self._vars: Dict[str, str]             = {}
        self._procs: Dict[str, tuple]          = {}
        self._cmds: Dict[str, Callable]        = {}
        self._max_loop = max_loop
        self._register_builtins()

    def register_command(self, name: str, fn: Callable) -> None:
        """外部コマンド（deltasim 固有コマンド）を登録する。"""
        self._cmds[name] = fn

    def set_var(self, name: str, value: Any) -> None:
        self._vars[name] = str(value)

    def get_var(self, name: str, default: str = "") -> str:
        return self._vars.get(name, default)

    def eval(self, script: str) -> str:
        """スクリプトを評価して最後のコマンドの結果を返す。"""
        # CRLF → LF 正規化
        script = script.replace("\r\n", "\n").replace("\r", "\n")
        pos = 0
        n = len(script)
        result = ""
        while pos < n:
            pos = _skip_ws(script, pos)
            # 空行/コメント
            if pos < n and script[pos] == "\n":
                pos += 1
                continue
            if pos < n and script[pos] == "#":
                while pos < n and script[pos] != "\n":
                    pos += 1
                continue
            if pos >= n:
                break
            words, pos = _parse_command(script, pos, self)
            if not words:
                continue
            result = self._dispatch(words) or ""
        return result

    def _dispatch(self, words: List[str]) -> str:
        if not words:
            return ""
        cmd = words[0]
        args = words[1:]
        # ユーザー定義 proc
        if cmd in self._procs:
            return self._call_proc(cmd, args)
        # 組み込み/登録コマンド
        fn = self._cmds.get(cmd)
        if fn is None:
            raise TclError(f"不明なコマンド: {cmd!r}")
        return fn(args) or ""

    # ----------------------------------------------------------------
    # 組み込みコマンド
    # ----------------------------------------------------------------

    def _register_builtins(self) -> None:
        for name in ("set", "unset", "if", "for", "foreach", "while",
                     "expr", "puts", "proc", "return", "break", "continue",
                     "incr", "append", "string", "list", "lindex", "llength",
                     "lappend", "concat", "format", "error"):
            self._cmds[name] = getattr(self, f"_cmd_{name}", None) or \
                                self._make_noop(name)

    def _make_noop(self, name: str):
        def _noop(args):
            raise TclError(f"未実装の組み込みコマンド: {name}")
        return _noop

    def _cmd_set(self, args: List[str]) -> str:
        if len(args) == 1:
            name = args[0]
            return self._vars.get(name, "")
        if len(args) >= 2:
            self._vars[args[0]] = args[1]
            return args[1]
        raise TclError("set: 引数が足りません")

    def _cmd_unset(self, args: List[str]) -> str:
        for name in args:
            self._vars.pop(name, None)
        return ""

    def _cmd_incr(self, args: List[str]) -> str:
        if not args:
            raise TclError("incr: 変数名が必要です")
        name = args[0]
        delta = int(args[1]) if len(args) > 1 else 1
        val = int(self._vars.get(name, "0")) + delta
        self._vars[name] = str(val)
        return str(val)

    def _cmd_append(self, args: List[str]) -> str:
        if not args:
            raise TclError("append: 変数名が必要です")
        name = args[0]
        current = self._vars.get(name, "")
        self._vars[name] = current + "".join(args[1:])
        return self._vars[name]

    def _cmd_expr(self, args: List[str]) -> str:
        from deltasim.interp.expr import eval_expr, ExprError
        expr_str = " ".join(args)
        # $var 展開（expr 内の変数参照）
        for k, v in self._vars.items():
            expr_str = expr_str.replace(f"${k}", v)
        try:
            val = eval_expr(expr_str, {k: _try_int(v) for k, v in self._vars.items()})
        except ExprError as e:
            raise TclError(f"expr エラー: {e}") from e
        if val is None:
            return "x"
        return str(int(val))

    def _cmd_puts(self, args: List[str]) -> str:
        if not args:
            return ""
        # puts ?-nonewline? ?channel? string
        nonewline = False
        text_args = list(args)
        if text_args and text_args[0] == "-nonewline":
            nonewline = True
            text_args = text_args[1:]
        if text_args and text_args[0] in ("stdout", "stderr"):
            text_args = text_args[1:]
        text = " ".join(text_args)
        try:
            import click as _click
            _click.echo(text, nl=not nonewline)
        except ImportError:
            print(text, end=("" if nonewline else "\n"))
        return ""

    def _cmd_if(self, args: List[str]) -> str:
        """if {cond} {body} ?elseif {cond} {body}? ?else {body}?"""
        i = 0
        while i < len(args):
            if args[i] in ("else",):
                i += 1
                if i < len(args):
                    return self.eval(args[i])
                break
            if args[i] in ("elseif",):
                i += 1
                if i >= len(args):
                    raise TclError("if: elseif の後に条件が必要です")
            # 条件評価
            cond_str = args[i]; i += 1
            cond = self._eval_bool(cond_str)
            if i >= len(args):
                raise TclError("if: 本体がありません")
            # 'then' キーワードを skip
            if args[i] == "then":
                i += 1
            body = args[i]; i += 1
            if cond:
                return self.eval(body)
        return ""

    def _cmd_for(self, args: List[str]) -> str:
        """for {init} {cond} {incr} {body}"""
        if len(args) < 4:
            raise TclError("for: 引数が足りません")
        init, cond, incr_script, body = args[0], args[1], args[2], args[3]
        self.eval(init)
        count = 0
        while self._eval_bool(cond):
            if count > self._max_loop:
                raise TclError(f"for: ループが {self._max_loop} 回を超えました")
            try:
                self.eval(body)
            except TclBreak:
                break
            except TclContinue:
                pass
            self.eval(incr_script)
            count += 1
        return ""

    def _cmd_foreach(self, args: List[str]) -> str:
        """foreach varname list body"""
        if len(args) < 3:
            raise TclError("foreach: 引数が足りません")
        varname, lst_str, body = args[0], args[1], args[2]
        items = lst_str.split() if lst_str.strip() else []
        count = 0
        for item in items:
            if count > self._max_loop:
                raise TclError(f"foreach: ループが {self._max_loop} 回を超えました")
            self._vars[varname] = item
            try:
                self.eval(body)
            except TclBreak:
                break
            except TclContinue:
                pass
            count += 1
        return ""

    def _cmd_while(self, args: List[str]) -> str:
        """while {cond} {body}"""
        if len(args) < 2:
            raise TclError("while: 引数が足りません")
        cond, body = args[0], args[1]
        count = 0
        while self._eval_bool(cond):
            if count > self._max_loop:
                raise TclError(f"while: ループが {self._max_loop} 回を超えました")
            try:
                self.eval(body)
            except TclBreak:
                break
            except TclContinue:
                pass
            count += 1
        return ""

    def _cmd_proc(self, args: List[str]) -> str:
        """proc name {params} {body}"""
        if len(args) < 3:
            raise TclError("proc: 引数が足りません")
        name, params_str, body = args[0], args[1], args[2]
        params = params_str.split() if params_str.strip() else []
        self._procs[name] = (params, body)
        return ""

    def _cmd_return(self, args: List[str]) -> str:
        value = args[0] if args else ""
        raise TclReturn(value)

    def _cmd_break(self, args: List[str]) -> str:
        raise TclBreak()

    def _cmd_continue(self, args: List[str]) -> str:
        raise TclContinue()

    def _cmd_string(self, args: List[str]) -> str:
        if not args:
            raise TclError("string: サブコマンドが必要です")
        sub = args[0]
        if sub == "length":
            return str(len(args[1]) if len(args) > 1 else 0)
        if sub == "index":
            if len(args) < 3:
                raise TclError("string index: 引数不足")
            s, i = args[1], int(args[2])
            return s[i] if 0 <= i < len(s) else ""
        if sub == "range":
            if len(args) < 4:
                raise TclError("string range: 引数不足")
            s, f, l = args[1], int(args[2]), int(args[3])
            return s[f:l+1]
        if sub == "toupper":
            return (args[1] if len(args) > 1 else "").upper()
        if sub == "tolower":
            return (args[1] if len(args) > 1 else "").lower()
        if sub in ("equal", "compare"):
            if len(args) < 3:
                raise TclError(f"string {sub}: 引数不足")
            return "1" if args[1] == args[2] else "0"
        raise TclError(f"string {sub}: 未実装")

    def _cmd_list(self, args: List[str]) -> str:
        return " ".join(args)

    def _cmd_lindex(self, args: List[str]) -> str:
        if len(args) < 2:
            return ""
        items = args[0].split()
        idx = int(args[1])
        return items[idx] if 0 <= idx < len(items) else ""

    def _cmd_llength(self, args: List[str]) -> str:
        return str(len(args[0].split()) if args else 0)

    def _cmd_lappend(self, args: List[str]) -> str:
        if not args:
            raise TclError("lappend: 変数名が必要です")
        name = args[0]
        current = self._vars.get(name, "")
        new_items = " ".join(args[1:])
        self._vars[name] = (current + " " + new_items).strip()
        return self._vars[name]

    def _cmd_concat(self, args: List[str]) -> str:
        return " ".join(args)

    def _cmd_format(self, args: List[str]) -> str:
        if not args:
            return ""
        fmt = args[0]
        vals = args[1:]
        # 最低限の %d/%s/%x/%o 対応
        try:
            result = fmt % tuple(_try_int(v) if "%" in fmt and "d" in fmt else v
                                 for v in vals)
            return result
        except Exception:
            return fmt

    def _cmd_error(self, args: List[str]) -> str:
        msg = args[0] if args else "エラー"
        raise TclError(msg)

    # ----------------------------------------------------------------
    # プロシージャ呼び出し
    # ----------------------------------------------------------------

    def _call_proc(self, name: str, call_args: List[str]) -> str:
        params, body = self._procs[name]
        saved = dict(self._vars)
        for i, pname in enumerate(params):
            self._vars[pname] = call_args[i] if i < len(call_args) else ""
        try:
            result = self.eval(body)
        except TclReturn as r:
            result = r.value
        finally:
            # ローカル変数を削除（グローバルは保持）
            for pname in params:
                self._vars.pop(pname, None)
        return result or ""

    # ----------------------------------------------------------------
    # 式 bool 評価
    # ----------------------------------------------------------------

    def _eval_bool(self, cond_str: str) -> bool:
        from deltasim.interp.expr import eval_expr, ExprError
        cond_str = cond_str.strip()

        # 「expr {...}」形式は Tcl の expr コマンドとして実行して数値を取得する
        if cond_str.startswith("expr") and len(cond_str) > 4 and cond_str[4] in (" ", "\t"):
            result = self.eval(cond_str)
            return result.strip() not in ("", "0", "false", "no")

        # $var 展開してから式評価
        expanded = cond_str
        for k, v in self._vars.items():
            expanded = expanded.replace(f"${k}", v)
        try:
            val = eval_expr(expanded, {k: _try_int(v) for k, v in self._vars.items()})
        except ExprError:
            # 非数値文字列として比較
            return bool(expanded) and expanded not in ("0", "false", "no", "")
        if val is None:
            return False
        return bool(val)


def _try_int(v: str) -> Any:
    """文字列を int に変換できれば変換、できなければそのまま返す。"""
    try:
        return int(v, 0)
    except (ValueError, TypeError):
        return v
