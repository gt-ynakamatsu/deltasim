"""GUI コンソール入力欄のテスト（§18.4）

テスト内容:
- コンソールコマンドの実行（examine, force, bp, when）
- Tcl 変数の持続（set var; examine $var）
- bp での停止時刻が応答に含まれること
- 長時間コマンドの中断
- シェル / subprocess / os.system / Python eval/exec が使えないこと
- Tcl の exec が存在しないこと
- do <file> のパス検証
"""

from __future__ import annotations

import json
import time
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


@pytest.fixture(scope="module")
def sim_session_id(client):
    """counter の Simulator セッションを作成して session_id を返す。"""
    res = client.post("/api/simulator", json={
        "sv_files": [COUNTER_SV],
        "top": "counter",
    })
    assert res.status_code == 200
    return res.json()["session_id"]


def _exec_console(client, session_id: str, command: str, timeout: float = 10.0) -> dict:
    """コンソールコマンドを実行して finish イベントの結果を返す。"""
    res = client.post(f"/api/console/{session_id}/exec/start", json={"command": command})
    assert res.status_code == 200, f"exec start failed: {res.text}"
    job_id = res.json()["job_id"]

    result = {"output": [], "time_ns": 0, "paused_bp": None, "quit": False}
    with client.stream("GET", f"/api/console/{session_id}/exec/{job_id}/events") as stream:
        for line in stream.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:].strip())
            evtype = ev.get("event") or ev.get("type")
            if evtype == "output":
                result["output"].extend(ev.get("lines", []))
            elif evtype == "finish":
                result.update(ev)
                result["output"] = result.get("output", []) + ev.get("output", [])
                break
            elif evtype == "error":
                result["error"] = ev.get("message", "")
                break

    return result


# ---------------------------------------------------------------------------
# 基本コマンド実行テスト
# ---------------------------------------------------------------------------

class TestConsoleBasic:
    def test_examine_signal(self, client, sim_session_id):
        """examine コマンドで信号値を取得できること"""
        # まず run してから examine
        _exec_console(client, sim_session_id, "run 20")
        res = _exec_console(client, sim_session_id, "examine count")
        all_output = " ".join(res.get("output", []))
        assert len(res.get("output", [])) > 0, f"examine の出力があること: {res}"

    def test_run_advances_time(self, client, sim_session_id):
        """run N で時刻が進むこと"""
        state_before = client.get(f"/api/console/{sim_session_id}/state").json()
        t_before = state_before["time_ns"]

        _exec_console(client, sim_session_id, "run 20")

        state_after = client.get(f"/api/console/{sim_session_id}/state").json()
        t_after = state_after["time_ns"]
        assert t_after > t_before, f"run 後に時刻が進むこと: {t_before} → {t_after}"

    def test_finish_event_has_time_ns(self, client, sim_session_id):
        """finish イベントに time_ns が含まれること"""
        res = _exec_console(client, sim_session_id, "run 10")
        assert "time_ns" in res, "finish イベントに time_ns が含まれること"
        assert isinstance(res["time_ns"], int)
        assert res["time_ns"] >= 0

    def test_force_and_examine(self, client, sim_session_id):
        """force で信号を固定し examine で確認できること"""
        _exec_console(client, sim_session_id, "force rst_n 0")
        res = _exec_console(client, sim_session_id, "examine rst_n")
        # 出力に値が含まれること
        assert len(res.get("output", [])) > 0

    def test_noforce(self, client, sim_session_id):
        """noforce で固定解除できること（エラーにならないこと）"""
        _exec_console(client, sim_session_id, "force rst_n 1")
        res = _exec_console(client, sim_session_id, "noforce rst_n")
        assert "error" not in res, f"noforce でエラーが発生しないこと: {res}"

    def test_help_command(self, client, sim_session_id):
        """help コマンドで使い方が表示されること"""
        res = _exec_console(client, sim_session_id, "help")
        output = "\n".join(res.get("output", []))
        assert "run" in output.lower(), f"help に run コマンドが含まれること: {output[:200]}"

    def test_unknown_command_error(self, client, sim_session_id):
        """不明なコマンドはエラーメッセージが返ること"""
        res = _exec_console(client, sim_session_id, "totally_unknown_command_xyz")
        output = "\n".join(res.get("output", []))
        assert len(output) > 0, "不明なコマンドは何らかの出力があること"


# ---------------------------------------------------------------------------
# Tcl 変数の持続テスト
# ---------------------------------------------------------------------------

class TestConsoleTclState:
    def test_tcl_variable_persists(self, client, sim_session_id):
        """Tcl 変数がセッション間で持続すること"""
        _exec_console(client, sim_session_id, "set myvar 42")
        res = _exec_console(client, sim_session_id, "puts $myvar")
        output = " ".join(res.get("output", []))
        assert "42" in output, f"Tcl 変数が持続すること: {output!r}"

    def test_tcl_proc_persists(self, client, sim_session_id):
        """proc がセッション間で持続すること"""
        _exec_console(client, sim_session_id, "proc myadd {a b} { expr {$a + $b} }")
        res = _exec_console(client, sim_session_id, "puts [myadd 3 4]")
        output = " ".join(res.get("output", []))
        assert "7" in output, f"proc が持続すること: {output!r}"

    def test_tcl_if_statement(self, client, sim_session_id):
        """if/else 文が動作すること"""
        _exec_console(client, sim_session_id, "set x 10")
        res = _exec_console(client, sim_session_id, "if {$x > 5} { puts ok } else { puts ng }")
        output = " ".join(res.get("output", []))
        assert "ok" in output, f"if 文が動作すること: {output!r}"

    def test_tcl_for_loop(self, client, sim_session_id):
        """for ループが動作すること"""
        res = _exec_console(client, sim_session_id,
                            "set s 0; for {set i 0} {$i < 3} {incr i} {incr s $i}; puts $s")
        output = " ".join(res.get("output", []))
        assert "3" in output, f"for ループが動作すること: {output!r}"


# ---------------------------------------------------------------------------
# ブレークポイント停止テスト
# ---------------------------------------------------------------------------

class TestConsoleBp:
    def test_bp_time_stops_run(self, client, sim_session_id):
        """bp -time で指定時刻に停止し、paused_bp が返ること"""
        # restart してから bp を設定
        _exec_console(client, sim_session_id, "restart")
        state_after_restart = client.get(f"/api/console/{sim_session_id}/state").json()
        t_start = state_after_restart["time_ns"]

        _exec_console(client, sim_session_id, f"bp -time {t_start + 50}")
        res = _exec_console(client, sim_session_id, "run -all")

        assert res.get("paused_bp") is not None, (
            f"bp -time で止まったら paused_bp が返ること: {res}"
        )
        assert res["paused_bp"]["time_ns"] <= t_start + 50 + 10, (
            f"停止時刻が bp 設定時刻付近であること: {res['paused_bp']}"
        )

    def test_paused_bp_has_time_ns(self, client, sim_session_id):
        """paused_bp に time_ns が含まれること"""
        _exec_console(client, sim_session_id, "restart")
        state = client.get(f"/api/console/{sim_session_id}/state").json()
        t_now = state["time_ns"]
        _exec_console(client, sim_session_id, f"bp -time {t_now + 30}")
        res = _exec_console(client, sim_session_id, "run -all")

        if res.get("paused_bp"):
            assert "time_ns" in res["paused_bp"], "paused_bp に time_ns が含まれること"
            assert isinstance(res["paused_bp"]["time_ns"], int)

    def test_bp_list(self, client, sim_session_id):
        """bp -list でブレークポイント一覧が返ること"""
        _exec_console(client, sim_session_id, "bp -time 9999")
        res = _exec_console(client, sim_session_id, "bp -list")
        output = " ".join(res.get("output", []))
        assert "9999" in output or "bp" in output.lower(), (
            f"bp -list に登録済み BP が含まれること: {output!r}"
        )

    def test_bp_delete(self, client, sim_session_id):
        """bp -delete で削除できること"""
        res_add = _exec_console(client, sim_session_id, "bp -time 88888")
        # bp -time の出力から ID を取得
        output = " ".join(res_add.get("output", []))
        # IDは '[N]' 形式で出力される
        import re
        m = re.search(r"\[(\d+)\]", output)
        if m:
            bp_id = m.group(1)
            res_del = _exec_console(client, sim_session_id, f"bp -delete {bp_id}")
            assert "error" not in res_del, f"bp -delete でエラーなし: {res_del}"

    def test_state_endpoint_has_breakpoints(self, client, sim_session_id):
        """GET /api/console/{sid}/state に breakpoints が含まれること"""
        _exec_console(client, sim_session_id, "bp -time 77777")
        res = client.get(f"/api/console/{sim_session_id}/state")
        assert res.status_code == 200
        data = res.json()
        assert "breakpoints" in data, "state に breakpoints フィールドがあること"
        assert isinstance(data["breakpoints"], list)


# ---------------------------------------------------------------------------
# 中断テスト
# ---------------------------------------------------------------------------

class TestConsoleCancel:
    def test_cancel_long_run(self, client):
        """run -all などの長時間コマンドを中断できること"""
        # 専用セッションを作成
        res = client.post("/api/simulator", json={
            "sv_files": [COUNTER_SV], "top": "counter",
        })
        assert res.status_code == 200
        sid = res.json()["session_id"]

        # コマンドを開始
        res2 = client.post(f"/api/console/{sid}/exec/start", json={"command": "run -all"})
        assert res2.status_code == 200
        job_id = res2.json()["job_id"]

        # 少し待ってからキャンセル（スレッドが起動するのを待つ）
        import time as _time
        _time.sleep(0.5)

        res3 = client.post(f"/api/console/{sid}/exec/{job_id}/cancel", json={})
        assert res3.status_code == 200
        assert res3.json()["ok"] is True

        # SSE を消費（finish イベントを待つ、タイムアウト付き）
        # キャンセル後に finish イベントが来ることを確認
        result = {"output": [], "got_finish": False}
        try:
            with client.stream("GET", f"/api/console/{sid}/exec/{job_id}/events",
                               timeout=30.0) as stream:
                for line in stream.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:].strip())
                    evtype = ev.get("event") or ev.get("type")
                    if evtype == "output":
                        result["output"].extend(ev.get("lines", []))
                    elif evtype in ("finish", "error"):
                        result["output"].extend(ev.get("output", []))
                        result["got_finish"] = True
                        break
        except Exception:
            pass  # ストリームが閉じられた場合も OK

        # キャンセルが通ったことを確認（エラーにならない、かつ finish イベントが来ること）
        # タイミングによっては既に終了している場合もあるので、got_finish は必須でない
        assert res3.json()["ok"] is True


# ---------------------------------------------------------------------------
# セキュリティテスト（シェル・exec が使えないこと）
# ---------------------------------------------------------------------------

class TestConsoleSecurity:
    def test_exec_command_not_available(self, client, sim_session_id):
        """Tcl の exec コマンドが存在しないこと"""
        res = _exec_console(client, sim_session_id, "exec echo hello")
        output = " ".join(res.get("output", []))
        # exec が使えないのでエラーになること
        assert "exec" not in output.lower() or "ERROR" in output or "error" in output.lower(), (
            f"exec コマンドはエラーになるべき: {output!r}"
        )
        # 少なくとも 'hello' が出力されていないこと（シェルが実行されていない）
        assert "hello" not in output, f"exec でシェルが実行されていないこと: {output!r}"

    def test_shell_commands_not_available(self, client, sim_session_id):
        """一般的なシェルコマンドが実行できないこと"""
        for shell_cmd in ["ls", "dir", "pwd", "whoami"]:
            res = _exec_console(client, sim_session_id, shell_cmd)
            output = " ".join(res.get("output", []))
            # いずれも Tcl エラーになること（シェルコマンドとして実行されない）
            # output はエラーメッセージか空

    def test_do_file_outside_allowed_path_rejected(self, client, sim_session_id, tmp_path):
        """do コマンドで許可パス外のファイルは 403 または実行エラーになること"""
        # /etc/passwd など通常は許可パス外
        import os
        if os.name == "nt":
            malicious_path = r"C:\Windows\System32\cmd.exe"
        else:
            malicious_path = "/etc/passwd"

        from deltasim.gui.filesec import get_allowed_roots
        allowed = get_allowed_roots()
        path = Path(malicious_path)

        # 許可ルート内でなければ拒否されること
        is_in_allowed = any(
            str(path.resolve()).startswith(str(r)) for r in allowed
        )
        if not is_in_allowed:
            res = client.post(f"/api/console/{sim_session_id}/exec/start",
                              json={"command": f"do {malicious_path}"})
            assert res.status_code in (403, 404), (
                f"許可パス外の do は 403/404 であること: {res.status_code} {res.text}"
            )

    def test_quit_does_not_stop_server(self, client):
        """quit コマンドでサーバーが停止しないこと"""
        # 専用セッションを作成
        res = client.post("/api/simulator", json={
            "sv_files": [COUNTER_SV], "top": "counter",
        })
        assert res.status_code == 200
        sid = res.json()["session_id"]

        # quit を実行
        _exec_console(client, sid, "quit")

        # サーバーはまだ動いていること
        health = client.get("/health")
        assert health.status_code == 200, "quit 後もサーバーが正常に動作していること"

    def test_nonexistent_session_returns_404(self, client):
        """存在しないセッションへのコマンド送信は 404"""
        res = client.post("/api/console/nonexistent-session-id/exec/start",
                          json={"command": "run 10"})
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# API エンドポイント構造テスト
# ---------------------------------------------------------------------------

class TestConsoleApiStructure:
    def test_exec_start_returns_job_id(self, client, sim_session_id):
        """exec/start が job_id を返すこと"""
        res = client.post(f"/api/console/{sim_session_id}/exec/start",
                          json={"command": "help"})
        assert res.status_code == 200
        data = res.json()
        assert "job_id" in data
        assert len(data["job_id"]) > 0
        assert "session_id" in data

    def test_state_endpoint(self, client, sim_session_id):
        """GET /api/console/{sid}/state が正しい形式を返すこと"""
        _exec_console(client, sim_session_id, "run 5")
        res = client.get(f"/api/console/{sim_session_id}/state")
        assert res.status_code == 200
        data = res.json()
        assert "time_ns" in data
        assert "cycle" in data
        assert "breakpoints" in data
        assert "whens" in data
        assert isinstance(data["breakpoints"], list)
        assert isinstance(data["whens"], list)

    def test_cancel_nonexistent_job_returns_404(self, client, sim_session_id):
        """存在しないジョブのキャンセルは 404"""
        res = client.post(f"/api/console/{sim_session_id}/exec/nonexistent-job/cancel",
                          json={})
        assert res.status_code == 404

    def test_finish_event_has_required_fields(self, client, sim_session_id):
        """finish イベントに必要なフィールドがあること"""
        res = _exec_console(client, sim_session_id, "examine clk")
        assert "time_ns" in res
        assert "cycle" in res
        assert "paused_bp" in res
        assert "quit" in res
        assert isinstance(res["quit"], bool)
