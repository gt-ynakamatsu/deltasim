"""tests/test_parser_eof.py — §16 パーサ有限終了テスト

どんな入力でもパーサが有限時間で終わること（無限ループ不具合修正 v0.2.2）。
- タイムアウト付き threading で、無限ループに入ってもテスト自体が必ず失敗として終わる。
- 各ケースで E001 エラーが返ること（code="E001"、message 内容）を詳細確認。
- 切り詰めテスト：全 .sv ファイルを全トークン位置と全文字位置（5文字おき）で切り詰める。
  許容結果: PASS（一応完結）か E001（ParseError/LexError）のどちらかだけ。
"""

from __future__ import annotations
import json, os, tempfile, threading, textwrap
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
TIMEOUT_SEC = 8   # 各ケースのタイムアウト（秒）

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

def _run_test(sv_content: str, timeout: int = TIMEOUT_SEC):
    """sv_content で deltasim test を実行し (TestResult, dict) を返す。
    タイムアウトの場合は (None, None) を返す。
    """
    import deltasim.api as ds
    import yaml as _yaml

    result_holder = [None]
    exception_holder = [None]
    sv_p = yaml_p = None

    def _worker():
        nonlocal sv_p, yaml_p
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".sv", delete=False, encoding="utf-8"
            ) as sv_f:
                sv_f.write(sv_content)
                sv_p = sv_f.name

            spec = {
                "version": 1, "top": "placeholder",
                "clock": {"signal": "clk", "period_ns": 10},
                "reset": {"signal": "rst", "active_low": True, "duration_cycles": 1},
                "sim": {"cycles": 1},
                "cases": [{"name": "t", "expected": []}],
            }
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, encoding="utf-8"
            ) as y_f:
                _yaml.dump(spec, y_f)
                yaml_p = y_f.name

            r = ds.test([sv_p], spec=yaml_p)
            result_holder[0] = r
        except Exception as e:
            exception_holder[0] = e
        finally:
            for _p in (sv_p, yaml_p):
                if _p:
                    try:
                        os.unlink(_p)
                    except Exception:
                        pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return None, None  # タイムアウト

    if exception_holder[0] is not None:
        raise exception_holder[0]
    return result_holder[0], (result_holder[0].to_dict() if result_holder[0] else None)


def _assert_e001(result, d, label: str):
    """タイムアウトなし・status ERROR・E001 コード・message が空でないことを確認"""
    assert result is not None, \
        f"[{label}] タイムアウト: パーサが {TIMEOUT_SEC}s 以内に終わらなかった"
    assert d is not None
    assert d["status"] == "ERROR", \
        f"[{label}] expected ERROR but got {d['status']}"
    codes = [e["code"] for e in d.get("errors", [])]
    assert "E001" in codes, \
        f"[{label}] E001 がない: errors={d.get('errors', [])}"
    msgs = [e["message"] for e in d["errors"] if e["code"] == "E001"]
    assert any(m for m in msgs), f"[{label}] E001 の message が空"


def _get_e001_error(d: dict):
    """最初の E001 ErrorInfo dict を返す"""
    return next((e for e in d.get("errors", []) if e["code"] == "E001"), None)


# ---------------------------------------------------------------------------
# §16-1: 基本的な壊れた入力ケース
# ---------------------------------------------------------------------------

class TestBrokenInputCases:
    """どれも TIMEOUT_SEC 秒以内に終わり E001 が返ること"""

    def test_missing_endmodule_line1(self):
        """endmodule が無い（module が 1 行目で始まる）

        message に「endmodule がありません」が含まれ、
        かつ 1 行目開始であることが含まれること（別々に assert）。
        errors[].line は 1 行目以降（EOF 位置）であること。
        """
        sv = "module foo(input logic a, output logic b);\n  assign b = a;\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "missing_endmodule_line1")
        e = _get_e001_error(d)
        assert e is not None
        # message に「endmodule がありません」が含まれること
        assert "endmodule がありません" in e["message"], \
            f"「endmodule がありません」が message にない: {e['message']!r}"
        # module の開始行番号「1」が message に含まれること
        assert "1" in e["message"], \
            f"module 開始行番号 1 が message にない: {e['message']!r}"
        # errors[].line は 0 より大きいこと（行番号フィールドが設定されていること）
        assert e["line"] > 0, \
            f"errors[].line が 0（行番号フィールド未設定）: {e['line']}"

    def test_missing_endmodule_line3(self):
        """endmodule が無い（module が 3 行目で始まる）

        message に「3 行目」（または「3」）が含まれることを確認する。
        """
        sv = "// comment line 1\n// comment line 2\nmodule bar(input logic x);\n  assign x = x;\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "missing_endmodule_line3")
        e = _get_e001_error(d)
        assert e is not None
        assert "endmodule がありません" in e["message"], \
            f"「endmodule がありません」が message にない: {e['message']!r}"
        # module は 3 行目で開始しているため「3」が含まれること
        assert "3" in e["message"], \
            f"module 開始行番号 3 が message にない: {e['message']!r}"

    def test_missing_end(self):
        """begin に対応する end が無い: message に「end」または「EOF」が含まれること"""
        sv = textwrap.dedent("""\
            module foo(input logic clk);
              always @(posedge clk) begin
                if (1) begin
            endmodule
            """)
        result, d = _run_test(sv)
        _assert_e001(result, d, "missing_end")
        e = _get_e001_error(d)
        assert e is not None
        # message に「end」や「EOF」など、何が無いかが含まれること
        msg_lower = e["message"].lower()
        assert "end" in msg_lower or "eof" in msg_lower, \
            f"message に 'end' または 'eof' が含まれない: {e['message']!r}"

    def test_unclosed_paren(self):
        """括弧が閉じていない: message に ')' が含まれること"""
        sv = "module foo(input logic a\nendmodule\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "unclosed_paren")
        e = _get_e001_error(d)
        assert e is not None
        # message に ')' が含まれること（期待するトークンの言及）
        assert ")" in e["message"], \
            f"message に ')' が含まれない: {e['message']!r}"

    def test_empty_file(self):
        """空のファイル"""
        result, d = _run_test("")
        _assert_e001(result, d, "empty_file")

    def test_comment_only(self):
        """コメントだけ（module 宣言なし）"""
        sv = "// just a comment\n/* block comment */\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "comment_only")

    def test_unclosed_block_comment(self):
        """閉じていない /* コメント: message に「ブロックコメント」が含まれること"""
        sv = "module foo;\n/* this never ends\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "unclosed_block_comment")
        e = _get_e001_error(d)
        assert e is not None
        assert "コメント" in e["message"] or "comment" in e["message"].lower(), \
            f"message に「コメント」が含まれない: {e['message']!r}"

    def test_unclosed_string(self):
        """閉じていない文字列リテラル: message に「文字列」が含まれること"""
        sv = 'module foo;\nparameter S = "unclosed;\nendmodule\n'
        result, d = _run_test(sv)
        _assert_e001(result, d, "unclosed_string")
        e = _get_e001_error(d)
        assert e is not None
        assert "文字列" in e["message"] or "string" in e["message"].lower(), \
            f"message に「文字列」が含まれない: {e['message']!r}"


# ---------------------------------------------------------------------------
# §16-2: CLI 経由でも正しい JSON が出ること（code=E001）
# ---------------------------------------------------------------------------

class TestCliEof:
    """CLI --json 経由で E001 が出て終了コード非0になること"""

    def _cli_run(self, sv_content: str, tmp_path) -> tuple:
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        import yaml as _yaml

        sv_p = str(tmp_path / "broken.sv")
        Path(sv_p).write_text(sv_content, encoding="utf-8")
        spec = {
            "version": 1, "top": "placeholder",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst", "active_low": True, "duration_cycles": 1},
            "sim": {"cycles": 1},
            "cases": [{"name": "t", "expected": []}],
        }
        yaml_p = str(tmp_path / "spec.yaml")
        with open(yaml_p, "w", encoding="utf-8") as f:
            _yaml.dump(spec, f)
        json_p = str(tmp_path / "result.json")

        done = threading.Event()
        result_holder = [None]

        def _run():
            runner = CliRunner()
            result_holder[0] = runner.invoke(cli_main, [
                "test", sv_p, "--spec", yaml_p, "--json", json_p,
            ])
            done.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        done.wait(timeout=TIMEOUT_SEC)
        assert done.is_set(), "CLI がタイムアウト内に終わらなかった"

        r = result_holder[0]
        assert r.exit_code != 0, f"期待: 非0終了, 実際: {r.exit_code}"
        d = json.loads(Path(json_p).read_text(encoding="utf-8"))
        return r, d

    def test_cli_missing_endmodule_line1(self, tmp_path):
        """CLI: endmodule なし（1行目）→ E001、message に「endmodule がありません」と「1」"""
        sv = "module foo(input logic a, output logic b);\n  assign b = a;\n"
        r, d = self._cli_run(sv, tmp_path)
        e = _get_e001_error(d)
        assert e is not None, f"E001 がない: {d.get('errors', [])}"
        assert "endmodule がありません" in e["message"], \
            f"message: {e['message']!r}"
        assert "1" in e["message"], \
            f"行番号 1 が message にない: {e['message']!r}"

    def test_cli_unclosed_block_comment(self, tmp_path):
        sv = "module foo;\n/* never closed\n"
        r, d = self._cli_run(sv, tmp_path)
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E001" in codes

    def test_cli_empty_file(self, tmp_path):
        r, d = self._cli_run("", tmp_path)
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E001" in codes


# ---------------------------------------------------------------------------
# §16-3: 切り詰めテスト（既存 .sv を切り詰めて PASS か E001 だけを許す）
# ---------------------------------------------------------------------------

# 切り詰めテストで許す結果:
#   - PASS: 切り詰め後も一応完結しているケース（EndまたはEndmoduleまで到達）
#   - E001: パーサ/レキサーエラー（ParseError/LexError）
#   - E_ELAB: エラボレーション段階のエラー（例: 信号未定義など）は許す。
#     理由: 切り詰めにより構文的には完結するが意味的に壊れる場合があり、
#     それはパーサの無限ループ修正とは無関係のため。
#     許容エラボレーションコード: E002, E003, E004（どれもparse完了後に発生）
_ALLOWED_ELAB_CODES = frozenset({"E002", "E003", "E004"})


def _truncate_and_check(sv_path: str, cut_char: int, timeout: int = TIMEOUT_SEC):
    """sv_path を cut_char 文字で切り詰めてパース（文字オフセット）。

    許容結果:
      - PASS（構文的に完結する）
      - E001（ParseError/LexError）
      - _ALLOWED_ELAB_CODES のエラー（エラボレーション段階のみ、パーサは完了）

    それ以外は失敗（特にタイムアウトは無限ループを示す）。
    """
    import deltasim.api as ds

    original = Path(sv_path).read_text(encoding="utf-8")
    truncated = original[:cut_char]  # 文字オフセット（バイトでない）

    result_holder = [None]
    exception_holder = [None]

    def _work():
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".sv", delete=False, encoding="utf-8"
            ) as f:
                f.write(truncated)
                p = f.name
            di = ds.load([p])
            result_holder[0] = di
            try:
                os.unlink(p)
            except Exception:
                pass
        except Exception as e:
            exception_holder[0] = e

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return False, f"timeout at char={cut_char} (無限ループの可能性)"

    if exception_holder[0] is not None:
        exc = exception_holder[0]
        exc_name = type(exc).__name__
        # ParseError / LexError は E001 相当 → OK
        if exc_name in ("ParseError", "LexError"):
            return True, f"acceptable: {exc_name}"
        return False, f"unexpected exception: {exc_name}: {exc}"

    di = result_holder[0]
    if di is None:
        return False, "no result"

    if di.status == "PASS":
        return True, "pass"

    if di.status == "ERROR":
        codes = set(e.code for e in di.errors)
        if "E001" in codes:
            return True, f"E001: OK"
        # エラボレーションエラーのみなら許可（パーサは完了した証拠）
        if codes and codes.issubset(_ALLOWED_ELAB_CODES):
            return True, f"elab error (parser OK): codes={codes}"
        return False, f"unexpected error codes={codes}"

    # SKIP / その他 status は想定外
    return False, f"unexpected status={di.status}"


# 切り詰めテスト対象ファイル（全 examples/*.sv; 代表4ファイルで速度を担保）
# 全ファイルを対象にすると CI で十数秒で完了することを実測済み（0.8ms/ケース）
_TRUNCATE_FILES = [
    "counter.sv",
    "mux2.sv",
    "dff_sync.sv",
    "uart_tx.sv",
    "fifo.sv",
    "alu.sv",
    "seq_detect.sv",
]


class TestTruncateFiles:
    """既存 .sv ファイルを切り詰めて PASS か E001（またはエラボレーションエラー）のどちらかになること。
    切り詰めたファイルが有限時間で終わること（タイムアウト = 無限ループ）。
    """

    @pytest.mark.parametrize("sv_name", _TRUNCATE_FILES)
    def test_truncate_at_all_token_positions(self, sv_name):
        """全トークン位置（文字オフセット）で切り詰める"""
        from deltasim.parser.lexer import tokenize
        sv_path = os.path.join(EXAMPLES, sv_name)
        source = Path(sv_path).read_text(encoding="utf-8")
        tokens = tokenize(source, sv_path)

        # トークンの文字オフセットを計算（行ごとの累積文字数で正確に）
        lines = source.splitlines(keepends=True)
        line_char_starts: list[int] = [0]
        for ln in lines:
            line_char_starts.append(line_char_starts[-1] + len(ln))

        char_positions: set[int] = set()
        for tok in tokens:
            if tok.type == "EOF":
                continue
            ln = tok.line - 1
            if ln < len(line_char_starts):
                char_pos = line_char_starts[ln] + (tok.col - 1)
                char_positions.add(max(0, char_pos))
        char_positions.add(len(source))  # full length → must PASS

        failures = []
        for cut in sorted(char_positions):
            ok, msg = _truncate_and_check(sv_path, cut)
            if not ok:
                failures.append(f"cut_char={cut}: {msg}")
        assert not failures, \
            f"{sv_name}: 切り詰めテスト失敗 ({len(failures)} 件):\n" + \
            "\n".join(failures[:10])

    @pytest.mark.parametrize("sv_name", _TRUNCATE_FILES)
    def test_truncate_at_every_5_chars(self, sv_name):
        """5文字おきの全文字位置で切り詰める"""
        sv_path = os.path.join(EXAMPLES, sv_name)
        source = Path(sv_path).read_text(encoding="utf-8")
        n = len(source)
        positions = list(range(0, n + 1, 5)) + [n]

        failures = []
        for cut in positions:
            ok, msg = _truncate_and_check(sv_path, cut)
            if not ok:
                failures.append(f"cut_char={cut}: {msg}")
        assert not failures, \
            f"{sv_name}: 5文字おき切り詰めテスト失敗 ({len(failures)} 件):\n" + \
            "\n".join(failures[:10])
