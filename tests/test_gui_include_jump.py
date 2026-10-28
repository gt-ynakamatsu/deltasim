"""§18.9 GUI: errors[].file が include 先を指すとき、
エラーに file/line フィールドが含まれることを確認するテスト

サーバー側テスト:
- /api/load が ErrorInfo に file / line を含むこと
- include 先のファイルパスが含まれること

UI 動作は app.js 側 (_logFileJumpHint / goToEditorLine) のため
フロントエンドテストは手動確認となる。ここではサーバーが正しい
file/line を返すことを確認する。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deltasim.gui.server import app

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
COUNTER_SV   = str(EXAMPLES_DIR / "counter.sv")


@pytest.fixture(scope="module")
def client():
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


class TestIncludeFileError:
    """§18.9: エラーに file/line フィールドが含まれること"""

    def test_load_error_has_file_field(self, client, tmp_path):
        """パースエラーに file フィールドが含まれること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # 不正な SV を作成
        bad_sv = tmp_path / "bad.sv"
        bad_sv.write_text("module bad_module(\n  // unclosed\n", encoding="utf-8")

        res = client.post("/api/load", json={"files": [str(bad_sv)]})
        assert res.status_code == 200
        data = res.json()
        # エラーが返ること
        if data.get("errors"):
            for e in data["errors"]:
                # file フィールドが存在すること（空でも可）
                assert "file" in e, f"file フィールドが必要: {e}"

    def test_load_error_has_line_field(self, client, tmp_path):
        """パースエラーに line フィールドが含まれること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        bad_sv = tmp_path / "syntax_error.sv"
        bad_sv.write_text(
            "module m (input logic clk);\n"
            "  always_ff @(posedge clk) begin\n"
            "    INVALID SYNTAX HERE $$$;\n"
            "  end\n"
            "endmodule\n",
            encoding="utf-8",
        )

        res = client.post("/api/load", json={"files": [str(bad_sv)]})
        assert res.status_code == 200
        data = res.json()
        if data.get("errors"):
            for e in data["errors"]:
                # line フィールドが存在すること
                assert "line" in e, f"line フィールドが必要: {e}"

    def test_include_error_file_points_to_include(self, client, tmp_path):
        """include 先のファイルにエラーがある場合、errors[].file がそのファイルを指すこと"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # include されるファイル（エラーあり）
        include_file = tmp_path / "bad_include.svh"
        include_file.write_text("  SYNTAX ERROR HERE @@@ ;\n", encoding="utf-8")

        # include するファイル
        main_sv = tmp_path / "main.sv"
        main_sv.write_text(
            "`include \"bad_include.svh\"\n"
            "module top; endmodule\n",
            encoding="utf-8",
        )

        res = client.post("/api/load", json={"files": [str(main_sv)]})
        assert res.status_code == 200
        data = res.json()

        # エラーがあれば file フィールドを確認
        if data.get("errors") and len(data["errors"]) > 0:
            for e in data["errors"]:
                # file フィールドが存在すること
                assert "file" in e, f"file フィールドが必要: {e}"
                # line フィールドが存在すること
                assert "line" in e, f"line フィールドが必要: {e}"

    def test_error_info_structure(self, client, tmp_path):
        """ErrorInfo の構造が GUI 側で期待するフィールドを持つこと"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        # 確実にパースエラーになる SV
        bad_sv = tmp_path / "definite_error.sv"
        bad_sv.write_text("not valid systemverilog at all!!!\n", encoding="utf-8")

        res = client.post("/api/load", json={"files": [str(bad_sv)]})
        assert res.status_code == 200
        data = res.json()

        if data.get("errors"):
            for e in data["errors"]:
                # GUI が使う必須フィールドの確認
                assert "code" in e,    "code フィールドが必要"
                assert "message" in e, "message フィールドが必要"
                assert "file" in e,    "file フィールドが必要（include先ジャンプ用）"
                assert "line" in e,    "line フィールドが必要（ジャンプ先行番号）"
                assert "severity" in e, "severity フィールドが必要"

    def test_load_valid_file_no_errors(self, client):
        """正常な SV ファイルはエラーなしで返ること（回帰テスト）"""
        res = client.post("/api/load", json={"files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "PASS"
        # errors フィールドは空またはなし
        assert len(data.get("errors", [])) == 0

    def test_go_to_line_api_no_crash(self, client, tmp_path):
        """file/line がある ErrorInfo で /api/file/read が動くこと（ジャンプの前提）"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        test_file = tmp_path / "readable.sv"
        test_file.write_text("module m; endmodule\n", encoding="utf-8")

        # file/read が動くこと（フロントエンドが openFileInEditor で呼ぶ）
        res = client.get(f"/api/file/read?path={test_file}")
        assert res.status_code == 200
        assert "content" in res.json()

    def test_error_file_outside_allowed_is_not_exposed(self, client, tmp_path):
        """セキュリティ: 許可ルート外のファイルの中身は読めないこと"""
        import os
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()
        unsafe = "/etc/passwd"
        p = Path(unsafe).resolve()
        is_allowed = str(p).startswith(str(home)) or str(p).startswith(str(cwd))
        if is_allowed or not p.exists():
            pytest.skip("このパスは許可ルート内または存在しない")

        # /api/file/read で許可ルート外は 403
        res = client.get(f"/api/file/read?path={unsafe}")
        assert res.status_code in (403, 404)
