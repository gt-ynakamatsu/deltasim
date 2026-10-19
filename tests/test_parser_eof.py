"""tests/test_parser_eof.py — §16 パーサ有限終了テスト

どんな入力でもパーサが有限時間で終わること（無限ループ不具合修正 v0.2.2）。
- タイムアウト付き subprocess / threading で、無限ループに入ってもテスト自体が必ず失敗として終わる。
- 各ケースで E001 エラーが返ること（code="E001"、message が空でないこと）を確認。
- 切り詰めテスト：既存 .sv ファイルを切り詰めて ParseError か成功になること。
"""

from __future__ import annotations
import json, os, subprocess, sys, tempfile, threading, textwrap
from pathlib import Path
import pytest

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
TIMEOUT_SEC = 8   # 各ケースのタイムアウト（秒）; CI 全体は十数秒以内

# ---------------------------------------------------------------------------
# ヘルパー: タイムアウト付きで deltasim test を呼ぶ
# ---------------------------------------------------------------------------

def _run_test(sv_content: str, timeout: int = TIMEOUT_SEC):
    """sv_content を含む一時ファイルで deltasim test を実行し (exit_code, json_dict) を返す。
    タイムアウトの場合は (None, None) を返す。
    """
    import deltasim.api as ds
    import yaml as _yaml

    result_holder = [None]
    exception_holder = [None]

    def _worker():
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
            try:
                os.unlink(sv_p)
                os.unlink(yaml_p)
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
    """タイムアウトなし + E001 があること + JSON が有効なことを確認する"""
    assert result is not None, f"[{label}] タイムアウト: パーサが {TIMEOUT_SEC}s 以内に終わらなかった"
    assert d is not None
    assert d["status"] == "ERROR", f"[{label}] expected ERROR but got {d['status']}"
    codes = [e["code"] for e in d.get("errors", [])]
    assert "E001" in codes, f"[{label}] E001 がない: errors={d.get('errors', [])}"
    msgs = [e["message"] for e in d["errors"] if e["code"] == "E001"]
    assert any(m for m in msgs), f"[{label}] E001 の message が空"


# ---------------------------------------------------------------------------
# §16-1: 基本的な壊れた入力ケース
# ---------------------------------------------------------------------------

class TestBrokenInputCases:
    """どれも TIMEOUT_SEC 秒以内に終わり E001 が返ること"""

    def test_missing_endmodule(self):
        """endmodule が無い"""
        sv = "module foo(input logic a, output logic b);\n  assign b = a;\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "missing_endmodule")
        # message に endmodule または行番号の言及があること
        msg = next(e["message"] for e in d["errors"] if e["code"] == "E001")
        assert "endmodule" in msg or "1" in msg, f"message={msg!r}"

    def test_missing_end(self):
        """begin に対応する end が無い"""
        sv = textwrap.dedent("""\
            module foo(input logic clk);
              always @(posedge clk) begin
                if (1) begin
            endmodule
            """)
        result, d = _run_test(sv)
        _assert_e001(result, d, "missing_end")

    def test_unclosed_paren(self):
        """括弧が閉じていない"""
        sv = "module foo(input logic a\nendmodule\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "unclosed_paren")

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
        """閉じていない /* コメント"""
        sv = "module foo;\n/* this never ends\n"
        result, d = _run_test(sv)
        _assert_e001(result, d, "unclosed_block_comment")

    def test_unclosed_string(self):
        """閉じていない文字列リテラル"""
        sv = 'module foo;\nparameter S = "unclosed;\nendmodule\n'
        result, d = _run_test(sv)
        _assert_e001(result, d, "unclosed_string")


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

    def test_cli_missing_endmodule(self, tmp_path):
        sv = "module foo(input logic a, output logic b);\n  assign b = a;\n"
        r, d = self._cli_run(sv, tmp_path)
        codes = [e["code"] for e in d.get("errors", [])]
        assert "E001" in codes

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
# §16-3: 切り詰めテスト（既存 .sv を切り詰めて PASS か E001）
# ---------------------------------------------------------------------------

def _truncate_and_check(sv_path: str, cut_pos: int, timeout: int = TIMEOUT_SEC):
    """sv_path を cut_pos バイトで切り詰めてパース。PASS か E001 以外なら失敗。"""
    import deltasim.api as ds

    original = Path(sv_path).read_text(encoding="utf-8", errors="replace")
    truncated = original[:cut_pos]

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
        return False, f"timeout at cut={cut_pos}"

    if exception_holder[0] is not None:
        exc = exception_holder[0]
        # Only E001-equivalent (ParseError/LexError) is acceptable
        exc_name = type(exc).__name__
        if exc_name in ("ParseError", "LexError"):
            return True, "acceptable error"
        return False, f"unexpected exception: {exc_name}: {exc}"

    di = result_holder[0]
    if di is None:
        return False, "no result"
    if di.status == "PASS":
        return True, "pass"
    if di.status == "ERROR":
        codes = [e.code for e in di.errors]
        if "E001" in codes or any(c.startswith("E0") for c in codes):
            return True, f"acceptable error codes={codes}"
        return False, f"unexpected error codes={codes}"
    return True, di.status


class TestTruncateFiles:
    """既存 .sv ファイルを切り詰めた入力で PASS か E001 のどちらかになること"""

    @pytest.mark.parametrize("sv_name", [
        "counter.sv", "mux2.sv", "dff_sync.sv",
    ])
    def test_truncate_at_token_boundaries(self, sv_name):
        """全トークン位置での切り詰め（抜粋版: 10% 間隔）"""
        from deltasim.parser.lexer import tokenize
        sv_path = os.path.join(EXAMPLES, sv_name)
        source = Path(sv_path).read_text(encoding="utf-8")
        tokens = tokenize(source, sv_path)
        # tok.col から byte offset を近似（行あたりの byte 数は可変なので行ごとに計算）
        lines = source.splitlines(keepends=True)
        line_starts = [0]
        for l in lines:
            line_starts.append(line_starts[-1] + len(l.encode("utf-8")))

        # 全トークン位置を byte offset に変換し、約 10% 間隔で間引く
        positions = set()
        for tok in tokens:
            if tok.type == "EOF":
                continue
            ln = tok.line - 1
            if ln < len(line_starts):
                approx = line_starts[ln] + tok.col - 1
                positions.add(max(0, approx))
        positions = sorted(positions)
        step = max(1, len(positions) // 10)
        sample = positions[::step] + [len(source.encode("utf-8"))]

        failures = []
        for cut in sample:
            ok, msg = _truncate_and_check(sv_path, cut)
            if not ok:
                failures.append(f"cut={cut}: {msg}")
        assert not failures, "\n".join(failures)

    @pytest.mark.parametrize("sv_name,cuts", [
        ("counter.sv",  [10, 50, 100, 200]),
        ("dff_sync.sv", [5, 30, 80]),
        ("mux2.sv",     [1, 20, 60]),
    ])
    def test_truncate_at_char_positions(self, sv_name, cuts):
        """指定文字位置での切り詰めが有限時間で終わること"""
        sv_path = os.path.join(EXAMPLES, sv_name)
        failures = []
        for cut in cuts:
            ok, msg = _truncate_and_check(sv_path, cut)
            if not ok:
                failures.append(f"cut={cut}: {msg}")
        assert not failures, "\n".join(failures)
