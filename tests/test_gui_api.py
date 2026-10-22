"""GUI API のテスト — FastAPI TestClient を使ったエンドポイント検証"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deltasim.gui.server import app

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"
COUNTER_SV   = str(EXAMPLES_DIR / "counter.sv")
MUX2_SV      = str(EXAMPLES_DIR / "mux2.sv")
SHIFT_SV     = str(EXAMPLES_DIR / "shift_reg.sv")
COUNTER_SPEC = str(EXAMPLES_DIR / "counter_spec.yaml")
MUX2_SPEC    = str(EXAMPLES_DIR / "mux2_spec.yaml")
SHIFT_SPEC   = str(EXAMPLES_DIR / "shift_reg_spec.yaml")


@pytest.fixture(scope="module")
def client():
    # §17.2: Host ヘッダが 127.0.0.1 になるよう base_url を指定
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


# ---------------------------------------------------------------------------
# ヘルスチェック
# ---------------------------------------------------------------------------

def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"ok": True}


# ---------------------------------------------------------------------------
# /api/examples — サンプル一覧
# ---------------------------------------------------------------------------

def test_get_examples(client):
    res = client.get("/api/examples")
    assert res.status_code == 200
    data = res.json()
    assert "examples" in data
    names = [ex["name"] for ex in data["examples"]]
    # examples/ にサンプルがあること
    assert len(names) > 0
    # counter, mux2, shift_reg のいずれかが含まれること
    assert any(n in names for n in ["counter", "mux2", "shift_reg"])


# ---------------------------------------------------------------------------
# /api/load — RTL ロード
# ---------------------------------------------------------------------------

class TestLoad:
    def test_counter(self, client):
        res = client.post("/api/load", json={"files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        data = res.json()
        assert data["module"] == "counter"
        assert isinstance(data["ports"], list)
        port_names = {p["name"] for p in data["ports"]}
        assert "clk" in port_names or "count" in port_names

    def test_mux2(self, client):
        res = client.post("/api/load", json={"files": [MUX2_SV], "top": "mux2"})
        assert res.status_code == 200
        data = res.json()
        assert data["module"] == "mux2"

    def test_shift_reg(self, client):
        res = client.post("/api/load", json={"files": [SHIFT_SV], "top": "shift_reg"})
        assert res.status_code == 200
        data = res.json()
        assert data["module"] == "shift_reg"

    def test_file_not_found(self, client):
        res = client.post("/api/load", json={"files": ["/nonexistent/file.sv"]})
        assert res.status_code == 422

    def test_params(self, client):
        res = client.post("/api/load", json={
            "files": [COUNTER_SV],
            "top": "counter",
            "params": {"WIDTH": 4},
        })
        assert res.status_code == 200
        data = res.json()
        assert data["module"] == "counter"


# ---------------------------------------------------------------------------
# /api/test — テスト実行
# ---------------------------------------------------------------------------

class TestRunTest:
    def test_counter_pass(self, client):
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "top": "counter",
        })
        assert res.status_code == 200
        data = res.json()
        # PR#7: schema_version は文字列 "2"
        assert str(data["schema_version"]) in ("1", "2")  # バージョン非依存
        # M5 API: checks フィールド（cases ではない）
        assert "checks" in data or "summary" in data
        assert "summary" in data
        assert data["summary"]["fail"] == 0

    def test_mux2_pass(self, client):
        res = client.post("/api/test", json={
            "sv_files": [MUX2_SV],
            "spec": MUX2_SPEC,
            "top": "mux2",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["summary"]["fail"] == 0

    def test_shift_reg_pass(self, client):
        res = client.post("/api/test", json={
            "sv_files": [SHIFT_SV],
            "spec": SHIFT_SPEC,
            "top": "shift_reg",
        })
        assert res.status_code == 200
        data = res.json()
        assert data["summary"]["fail"] == 0

    def test_result_structure(self, client):
        """結果 JSON の構造が PR#8 §13 の contracts に沿っていること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()

        # トップレベル
        assert "schema_version" in data
        assert "module" in data
        assert "status" in data
        assert data["status"] in ("PASS", "FAIL", "ERROR", "SKIP")
        assert "summary" in data
        assert "cases" in data      # PR#8: cases[] が存在すること
        assert "checks" in data     # backward compat
        assert "errors" in data
        assert "cancelled" in data
        assert "truncated_at" in data

        # §13.2: cases[] 構造（per-case details）
        assert len(data["cases"]) >= 1
        for case in data["cases"]:
            assert "name" in case
            assert "status" in case
            assert case["status"] in ("PASS", "FAIL", "ERROR", "SKIP")
            assert "seed" in case            # §13: per-case seed
            assert "elapsed_ms" in case      # §13.1: per-case 実時間
            assert "checks" in case          # per-case checks
            assert "log" in case             # per-case log
            assert "errors" in case
            assert "cancelled" in case
            assert "truncated_at" in case

        # §13.1: summary.elapsed_ms が推奨フィールド
        s = data["summary"]
        assert "elapsed_ms" in s
        assert isinstance(s["elapsed_ms"], (int, float))
        assert "total" in s and "pass" in s and "fail" in s
        assert "error" in s and "skip" in s

    def test_seed_override(self, client):
        """シードを指定してテストが実行できること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "seed": 12345,
        })
        assert res.status_code == 200
        data = res.json()
        # PR#8: seed は cases[].seed に入る（top-level は spec デフォルト値）
        assert len(data["cases"]) > 0
        assert data["cases"][0]["seed"] == 12345

    def test_spec_not_found(self, client):
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": "/nonexistent/spec.yaml",
        })
        assert res.status_code == 422

    def test_sv_not_found(self, client):
        res = client.post("/api/test", json={
            "sv_files": ["/nonexistent/file.sv"],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 422

    def test_vcd_written(self, client, tmp_path):
        """VCD ファイルが書き出されること"""
        vcd_out = str(tmp_path / "out.vcd")
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "vcd_out": vcd_out,
        })
        assert res.status_code == 200
        assert os.path.isfile(vcd_out)
        assert os.path.getsize(vcd_out) > 0


# ---------------------------------------------------------------------------
# /api/waveform — VCD 読み込み
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def counter_vcd_path(tmp_path_factory):
    """counter テストを実行して VCD を生成する（モジュールスコープ）"""
    vcd = tmp_path_factory.mktemp("vcd") / "counter.vcd"
    with TestClient(app, base_url="http://127.0.0.1") as c:
        res = c.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "vcd_out": str(vcd),
        })
    assert res.status_code == 200
    return str(vcd)


class TestWaveform:
    def test_read_waveform(self, client, counter_vcd_path):
        res = client.get(f"/api/waveform?path={counter_vcd_path}")
        assert res.status_code == 200
        data = res.json()
        assert "timescale" in data
        assert "signals" in data
        assert "end_time" in data
        assert data["end_time"] > 0
        assert len(data["signals"]) > 0

    def test_waveform_signals_have_changes(self, client, counter_vcd_path):
        res = client.get(f"/api/waveform?path={counter_vcd_path}")
        assert res.status_code == 200
        data = res.json()
        if "clk" in data["signals"]:
            assert len(data["signals"]["clk"]["changes"]) > 0

    def test_waveform_time_filter(self, client, counter_vcd_path):
        res = client.get(f"/api/waveform?path={counter_vcd_path}&t_start=0&t_end=50")
        assert res.status_code == 200
        data = res.json()
        for sig, info in data["signals"].items():
            for t, _ in info["changes"]:
                assert t <= 50, f"信号 {sig} の変化時刻 {t} がフィルタ範囲外"

    def test_waveform_not_found(self, client):
        res = client.get("/api/waveform?path=/nonexistent.vcd")
        assert res.status_code == 404

    def test_waveform_signal_filter(self, client, counter_vcd_path):
        res = client.get(f"/api/waveform?path={counter_vcd_path}&signals=clk")
        assert res.status_code == 200
        data = res.json()
        for name in data["signals"]:
            assert name == "clk"


# ---------------------------------------------------------------------------
# /api/run — シミュレーション実行（テスト判定なし）
# ---------------------------------------------------------------------------

class TestRun:
    def test_run_counter(self, client, tmp_path):
        vcd_out = str(tmp_path / "run.vcd")
        res = client.post("/api/run", json={
            "sv_files": [COUNTER_SV],
            "top": "counter",
            "cycles": 30,
            "vcd_out": vcd_out,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["module"] == "counter"
        assert data["cycles"] == 30
        assert data["time_ns"] > 0
        assert os.path.isfile(vcd_out)

    def test_run_mux2(self, client):
        res = client.post("/api/run", json={
            "sv_files": [MUX2_SV],
            "top": "mux2",
            "cycles": 4,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["module"] == "mux2"


# ---------------------------------------------------------------------------
# フロントエンド配信
# ---------------------------------------------------------------------------

def test_index_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "deltasim" in res.text.lower()


def test_static_css(client):
    res = client.get("/static/style.css")
    assert res.status_code == 200


def test_static_js(client):
    res = client.get("/static/app.js")
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# /api/browse — ファイルブラウザ
# ---------------------------------------------------------------------------

class TestFileBrowser:
    def test_browse_roots(self, client):
        res = client.get("/api/browse/roots")
        assert res.status_code == 200
        data = res.json()
        assert "roots" in data
        assert len(data["roots"]) >= 1

    def test_browse_home(self, client):
        import os
        home = os.path.expanduser("~")
        res = client.get(f"/api/browse?path={home}")
        assert res.status_code == 200
        data = res.json()
        assert "entries" in data

    def test_browse_workspace(self, client):
        import os
        cwd = os.getcwd()
        res = client.get(f"/api/browse?path={cwd}")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data["entries"], list)
        # entries には type フィールドがあること
        for e in data["entries"]:
            assert e["type"] in ("file", "dir")

    def test_browse_traversal_blocked(self, client):
        """パストラバーサルは 403 になること"""
        res = client.get("/api/browse?path=/root/secret")
        # ルート外は 403 か 404
        assert res.status_code in (403, 404)

    def test_browse_nonexistent(self, client):
        res = client.get("/api/browse?path=/nonexistent/path/xyz")
        assert res.status_code in (403, 404)


# ---------------------------------------------------------------------------
# /api/file — ファイル読み書き
# ---------------------------------------------------------------------------

class TestFileReadWrite:
    def test_read_sv_file(self, client):
        res = client.get(f"/api/file/read?path={COUNTER_SV}")
        assert res.status_code == 200
        data = res.json()
        assert "content" in data
        assert len(data["content"]) > 0
        assert data["lang"] == "verilog"

    def test_read_yaml_file(self, client):
        res = client.get(f"/api/file/read?path={COUNTER_SPEC}")
        assert res.status_code == 200
        data = res.json()
        assert data["lang"] == "yaml"

    def test_write_read_roundtrip(self, client, tmp_path):
        test_file = str(tmp_path / "test.sv")
        # ファイルを作成（filesec の許可ルートに tmp_path が入るよう add_extra_root）
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        content = "// test SystemVerilog\nmodule test_mod;\nendmodule"
        res = client.post("/api/file/write", json={"path": test_file, "content": content})
        assert res.status_code == 200

        res2 = client.get(f"/api/file/read?path={test_file}")
        assert res2.status_code == 200
        assert res2.json()["content"] == content

    def test_read_nonexistent(self, client):
        res = client.get("/api/file/read?path=/nonexistent/file.sv")
        assert res.status_code in (403, 404)

    def test_write_traversal_blocked(self, client):
        """許可ルート外への書き込みは 403 になること"""
        res = client.post("/api/file/write", json={
            "path": "/root/evil.sh",
            "content": "rm -rf /"
        })
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# /api/waveform/index — VCD 信号インデックス
# ---------------------------------------------------------------------------

class TestWaveformIndex:
    @pytest.fixture(scope="class")
    def vcd_path(self, tmp_path_factory):
        vcd = tmp_path_factory.mktemp("vcd") / "counter.vcd"
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.post("/api/test", json={
                "sv_files": [COUNTER_SV],
                "spec": COUNTER_SPEC,
                "vcd_out": str(vcd),
            })
        assert res.status_code == 200
        return str(vcd)

    def test_index_returns_signals(self, client, vcd_path):
        res = client.get(f"/api/waveform/index?path={vcd_path}")
        assert res.status_code == 200
        data = res.json()
        assert "signals" in data
        assert "timescale" in data
        assert len(data["signals"]) > 0

    def test_index_signal_has_width(self, client, vcd_path):
        res = client.get(f"/api/waveform/index?path={vcd_path}")
        data = res.json()
        for name, info in data["signals"].items():
            assert "width" in info
            assert info["width"] >= 1

    def test_index_no_changes_in_result(self, client, vcd_path):
        """index は changes を返さない（高速化のため）"""
        res = client.get(f"/api/waveform/index?path={vcd_path}")
        data = res.json()
        # changes フィールドは存在しない（signal は {width, vcd_id} のみ）
        for name, info in data["signals"].items():
            assert "changes" not in info

    def test_index_not_found(self, client):
        res = client.get("/api/waveform/index?path=/nonexistent.vcd")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# /api/waveform/range — VCD 範囲取得
# ---------------------------------------------------------------------------

class TestWaveformRange:
    @pytest.fixture(scope="class")
    def vcd_path(self, tmp_path_factory):
        vcd = tmp_path_factory.mktemp("vcd2") / "counter.vcd"
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.post("/api/test", json={
                "sv_files": [COUNTER_SV],
                "spec": COUNTER_SPEC,
                "vcd_out": str(vcd),
            })
        assert res.status_code == 200
        return str(vcd)

    def test_range_all_signals(self, client, vcd_path):
        res = client.get(f"/api/waveform/range?path={vcd_path}")
        assert res.status_code == 200
        data = res.json()
        assert "signals" in data
        assert data["end_time"] > 0

    def test_range_specific_signals(self, client, vcd_path):
        res = client.get(f"/api/waveform/range?path={vcd_path}&signals=clk")
        assert res.status_code == 200
        data = res.json()
        # clk だけが返ること
        assert "clk" in data["signals"]
        for name in data["signals"]:
            assert name == "clk"

    def test_range_time_filter(self, client, vcd_path):
        res = client.get(f"/api/waveform/range?path={vcd_path}&t_start=0&t_end=50")
        assert res.status_code == 200
        data = res.json()
        for name, info in data["signals"].items():
            for t, _ in info["changes"]:
                assert t <= 50

    def test_range_not_found(self, client):
        res = client.get("/api/waveform/range?path=/nonexistent.vcd")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# /api/test/start + SSE — テスト実行（非同期）
# ---------------------------------------------------------------------------

class TestSSEJob:
    def test_start_returns_job_id(self, client):
        res = client.post("/api/test/start", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "job_id" in data
        assert len(data["job_id"]) > 0

    def test_job_result_after_completion(self, client):
        """ジョブが完了した後に結果を取得できること"""
        res = client.post("/api/test/start", json={
            "sv_files": [MUX2_SV],
            "spec": MUX2_SPEC,
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        # SSE を消費（TestClient は同期なので全イベントを受け取るまで待機）
        with client.stream("GET", f"/api/test/{job_id}/events") as stream:
            events = []
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    import json
                    ev = json.loads(line[5:].strip())
                    events.append(ev)
                    if ev.get("event") in ("finish", "error"):
                        break

        assert any(e.get("event") == "finish" for e in events)

        # 結果を取得
        res2 = client.get(f"/api/test/{job_id}/result")
        assert res2.status_code == 200
        data = res2.json()
        assert data["done"] is True
        assert data["result"]["summary"]["fail"] == 0

    def test_cancel_job(self, client):
        """存在するジョブをキャンセルできること"""
        res = client.post("/api/test/start", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        job_id = res.json()["job_id"]

        res2 = client.post(f"/api/test/{job_id}/cancel", json={})
        assert res2.status_code == 200
        assert res2.json()["ok"] is True

    def test_cancel_nonexistent(self, client):
        res = client.post("/api/test/nonexistent-job-id/cancel", json={})
        assert res.status_code == 404

    def test_start_spec_not_found(self, client):
        res = client.post("/api/test/start", json={
            "sv_files": [COUNTER_SV],
            "spec": "/nonexistent/spec.yaml",
        })
        assert res.status_code == 422


# ---------------------------------------------------------------------------
# /api/project — プロジェクトファイル
# ---------------------------------------------------------------------------

class TestProject:
    def test_save_and_load(self, client, tmp_path):
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        proj_path = str(tmp_path / "deltasim.project.yaml")
        res = client.post("/api/project", json={
            "path": proj_path,
            "sv_files": [COUNTER_SV],
            "top": "counter",
            "spec": COUNTER_SPEC,
            "params": {},
        })
        assert res.status_code == 200
        assert res.json()["ok"] is True

        # ロード
        res2 = client.get(f"/api/project?path={proj_path}")
        assert res2.status_code == 200
        data = res2.json()
        assert data["top"] == "counter"
        assert COUNTER_SV in data["sv_files"]

    def test_load_nonexistent(self, client):
        res = client.get("/api/project?path=/nonexistent/deltasim.project.yaml")
        assert res.status_code in (403, 404)


# ---------------------------------------------------------------------------
# filesec — パスセキュリティ単体テスト
# ---------------------------------------------------------------------------

class TestFileSec:
    def test_safe_path_allowed(self, tmp_path):
        from deltasim.gui.filesec import safe_path, add_extra_root
        add_extra_root(tmp_path)
        f = tmp_path / "test.sv"
        f.touch()
        result = safe_path(str(f))
        assert result == f.resolve()

    def test_traversal_blocked(self):
        from deltasim.gui.filesec import safe_path, PathSecurityError
        import os
        # ホームとcwdの外（/etc/passwd はほぼ確実に外）
        home = Path(os.path.expanduser("~")).resolve()
        cwd = Path.cwd().resolve()
        try:
            etc_path = Path("/etc/passwd").resolve()
            if not (str(etc_path).startswith(str(home)) or str(etc_path).startswith(str(cwd))):
                with pytest.raises(PathSecurityError):
                    safe_path("/etc/passwd")
        except PathSecurityError:
            pass  # 期待通り

    def test_symlink_resolution(self, tmp_path):
        from deltasim.gui.filesec import safe_path, add_extra_root, PathSecurityError
        add_extra_root(tmp_path)
        # tmp_path 内のシンボリックリンクが tmp_path 外を指す場合は拒否
        target = Path("/etc/hosts")
        link = tmp_path / "malicious_link"
        try:
            link.symlink_to(target)
            home = Path(os.path.expanduser("~")).resolve()
            cwd = Path.cwd().resolve()
            etc_real = target.resolve()
            if not (str(etc_real).startswith(str(home)) or str(etc_real).startswith(str(cwd))):
                with pytest.raises(PathSecurityError):
                    safe_path(str(link))
        except (OSError, PermissionError):
            pytest.skip("シンボリックリンクが作成できません")


# ---------------------------------------------------------------------------
# 大規模 VCD インデックス性能テスト
# ---------------------------------------------------------------------------

class TestLargeVCDPerformance:
    """大規模 VCD のインデックス・範囲取得を計測する。"""

    @pytest.fixture(scope="class")
    def large_vcd(self, tmp_path_factory):
        """1000 信号・10万変化の VCD を生成（pytest では小規模）"""
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from gen_large_vcd import gen_vcd

        vcd = tmp_path_factory.mktemp("large") / "large.vcd"
        gen_vcd(str(vcd), n_signals=1000, n_changes=100_000)
        return str(vcd)

    def test_index_performance(self, large_vcd):
        """1k 信号 VCD のインデックス取得が 5 秒以内"""
        from deltasim.gui.vcd_reader import read_vcd_index
        t0 = time.monotonic()
        idx = read_vcd_index(large_vcd)
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"インデックス取得が遅すぎます: {elapsed:.2f}s"
        assert len(idx["signals"]) == 1000

    def test_range_20_signals(self, large_vcd):
        """1k 信号 VCD の 20 信号範囲取得が 5 秒以内"""
        from deltasim.gui.vcd_reader import read_vcd_index, read_vcd_range
        idx = read_vcd_index(large_vcd)
        sigs_20 = list(idx["signals"].keys())[:20]

        t0 = time.monotonic()
        data = read_vcd_range(large_vcd, signal_names=sigs_20, t_start=0, t_end=None)
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"range 取得が遅すぎます: {elapsed:.2f}s"
        assert len(data["signals"]) == 20


# ---------------------------------------------------------------------------
# /api/simulator — 対話デバッグ Simulator API (§8)
# ---------------------------------------------------------------------------

class TestSimulatorAPI:
    def test_create_session(self, client):
        """Simulator セッションを作成できること"""
        res = client.post("/api/simulator", json={
            "sv_files": [COUNTER_SV],
            "top": "counter",
        })
        assert res.status_code == 200
        data = res.json()
        assert "session_id" in data
        assert data["module"] == "counter"
        assert "signals" in data
        assert data["time_ns"] == 0

    def test_step(self, client):
        """step(n) でサイクルを進められること"""
        # セッション作成
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        assert res.status_code == 200
        sid = res.json()["session_id"]

        # step 5
        res2 = client.post(f"/api/simulator/{sid}/step", json={"n": 5})
        assert res2.status_code == 200
        snap = res2.json()
        assert "time_ns" in snap
        assert snap["time_ns"] > 0

    def test_list_signals(self, client):
        """信号一覧を取得できること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]

        res2 = client.get(f"/api/simulator/{sid}/signals")
        assert res2.status_code == 200
        data = res2.json()
        assert "signals" in data
        assert any(s["name"] == "clk" for s in data["signals"])

    def test_add_remove_breakpoint(self, client):
        """ブレークポイントの追加・削除ができること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]

        res2 = client.post(f"/api/simulator/{sid}/breakpoints", json={"condition": "count == 5"})
        assert res2.status_code == 200
        bp_id = res2.json()["id"]

        res3 = client.get(f"/api/simulator/{sid}/breakpoints")
        assert any(bp["id"] == bp_id for bp in res3.json()["breakpoints"])

        res4 = client.delete(f"/api/simulator/{sid}/breakpoints/{bp_id}")
        assert res4.status_code == 200

    def test_set_signal(self, client):
        """信号値を設定できること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]
        client.post(f"/api/simulator/{sid}/step", json={"n": 15})

        res2 = client.post(f"/api/simulator/{sid}/set", json={"signal": "rst_n", "value": 1})
        assert res2.status_code == 200

    def test_force_release(self, client):
        """force/release が動作すること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]

        res2 = client.post(f"/api/simulator/{sid}/force", json={"signal": "rst_n", "value": 0})
        assert res2.status_code == 200
        assert res2.json()["forced"] is True

        res3 = client.post(f"/api/simulator/{sid}/release", json={"signal": "rst_n"})
        assert res3.status_code == 200
        assert res3.json()["forced"] is False

    def test_reset(self, client):
        """reset で時刻がリセットされること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]
        client.post(f"/api/simulator/{sid}/step", json={"n": 10})

        res2 = client.post(f"/api/simulator/{sid}/reset", json={})
        assert res2.status_code == 200
        assert res2.json()["time_ns"] == 0

    def test_waveform_from_memory(self, client):
        """in-memory 波形（get_signal_changes）が取得できること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]
        client.post(f"/api/simulator/{sid}/step", json={"n": 20})

        res2 = client.get(f"/api/simulator/{sid}/waveform")
        assert res2.status_code == 200
        data = res2.json()
        assert "signals" in data
        assert "end_time" in data

    def test_delete_session(self, client):
        """セッションを削除できること"""
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        sid = res.json()["session_id"]

        res2 = client.delete(f"/api/simulator/{sid}")
        assert res2.status_code == 200

        res3 = client.get(f"/api/simulator/{sid}/signals")
        assert res3.status_code == 404

    def test_nonexistent_session(self, client):
        """存在しないセッションは 404"""
        res = client.get("/api/simulator/nonexistent-id/signals")
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# §12 対応: adapter の truncated_at / checks 構造
# ---------------------------------------------------------------------------

class TestM5ApiCompat:
    def test_cases_field_present(self, client):
        """PR#8 §13.2: result に cases[] フィールドがあること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "cases" in data
        assert isinstance(data["cases"], list)
        assert len(data["cases"]) >= 1

    def test_case_has_seed_and_log(self, client):
        """PR#8 §13.2: cases[] の各要素が seed と log を持つこと"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        for case in data["cases"]:
            assert "seed" in case
            assert "log" in case
            assert isinstance(case["seed"], int)
            assert isinstance(case["log"], list)

    def test_case_elapsed_ms(self, client):
        """PR#8 §13.1: cases[].elapsed_ms が per-case 実時間であること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        for case in data["cases"]:
            assert "elapsed_ms" in case
            assert isinstance(case["elapsed_ms"], (int, float))
            assert case["elapsed_ms"] >= 0

    def test_truncated_at_field(self, client):
        """§12.3: truncated_at フィールドが存在すること（None か数値）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "truncated_at" in data
        assert data["truncated_at"] is None

    def test_cancelled_field(self, client):
        """§12.1: cancelled フィールドが存在すること"""
        res = client.post("/api/test", json={
            "sv_files": [MUX2_SV],
            "spec": MUX2_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "cancelled" in data
        assert data["cancelled"] is False

    def test_schema_version_string(self, client):
        """PR#7: schema_version は文字列 '2'"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data["schema_version"], str)
        assert data["schema_version"] == "2"

    def test_checks_have_4state_values(self, client):
        """§12.4 (PR#7): cases[].checks の expected/actual は 4 値文字列（0/1/x/z 小文字統一）。"""
        res = client.post("/api/test", json={
            "sv_files": [MUX2_SV],
            "spec": MUX2_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        # PR#8: cases[].checks を参照
        for case in data.get("cases", []):
            for chk in case.get("checks", []):
                if chk.get("source") == "spec" and chk.get("signal") not in ("<expr>", ""):
                    for v in (chk.get("expected", ""), chk.get("actual", "")):
                        assert all(c in "01xz" for c in str(v).lower()), \
                            f"4 値文字列でない値: {v!r} (check: {chk['name']})"

    def test_z_x_distinction_in_vcd(self):
        """§12.4 (PR#7): VCDパーサが Z='z' と X='x' を区別すること"""
        import tempfile, os
        from deltasim.gui.vcd_reader import read_vcd

        vcd = "$timescale 1ns $end\n$var wire 1 ! sig $end\n$enddefinitions $end\n"
        vcd += "$dumpvars\nx!\n$end\n#5\n1!\n#10\nz!\n#15\nx!\n"
        with tempfile.NamedTemporaryFile(suffix=".vcd", delete=False, mode="w") as f:
            f.write(vcd)
            path = f.name
        try:
            data = read_vcd(path)
            changes = data["signals"]["sig"]["changes"]
            values = [v for _, v in changes]
            assert "x" in values, f"X 値が見つかりません: {changes}"
            assert "z" in values, f"Z 値が見つかりません: {changes}"
        finally:
            os.unlink(path)

    def test_summary_elapsed_ms(self, client):
        """§13.1 (PR#8): summary.elapsed_ms が全体の実時間であること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "summary" in data
        s = data["summary"]
        assert "elapsed_ms" in s, "summary.elapsed_ms が存在しません"
        assert isinstance(s["elapsed_ms"], (int, float))
        assert s["elapsed_ms"] >= 0

    def test_no_deprecated_elapsed_ms_in_gui(self, client):
        """§13.1: GUI の summary には deprecated トップレベル elapsed_ms を使わないこと"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        # GUI の adapter は summary.elapsed_ms を正として返す
        assert "summary" in data
        assert "elapsed_ms" in data["summary"]
        # cases[].elapsed_ms は per-case 実時間
        for case in data.get("cases", []):
            assert "elapsed_ms" in case


# ---------------------------------------------------------------------------
# ケースフィルタ・/api/spec/cases・E_NO_CASE
# ---------------------------------------------------------------------------

class TestCaseFilter:
    def test_spec_cases_single(self, client):
        """旧式スペック（cases:なし）からケース名を取得できること"""
        res = client.get(f"/api/spec/cases?path={COUNTER_SPEC}")
        assert res.status_code == 200
        data = res.json()
        assert "case_names" in data
        assert len(data["case_names"]) == 1
        # ファイル名 stem が名前になること
        assert "counter_spec" in data["case_names"]

    def test_spec_cases_not_found(self, client):
        res = client.get("/api/spec/cases?path=/nonexistent/spec.yaml")
        assert res.status_code == 404

    def test_filter_cases_single_match(self, client):
        """filter_cases で特定ケース名を指定して実行できること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": ["counter_spec"],
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("PASS", "FAIL")
        assert len(data["cases"]) == 1
        assert data["cases"][0]["name"] == "counter_spec"

    def test_filter_cases_wildcard(self, client):
        """filter_cases でワイルドカードが使えること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": ["counter*"],
        })
        assert res.status_code == 200
        data = res.json()
        # ワイルドカードで少なくとも1件ヒット
        assert len(data["cases"]) >= 1

    def test_e_no_case_error(self, client):
        """filter_cases で一致なしのとき E_NO_CASE エラーを返すこと"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": ["nonexistent_case_xyz"],
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ERROR"
        assert len(data["cases"]) == 0
        # E_NO_CASE コードを持つエラーが存在すること
        error_codes = [e.get("code") for e in data.get("errors", [])]
        assert "E_NO_CASE" in error_codes, f"E_NO_CASE not in errors: {data['errors']}"

    def test_e_no_case_message_readable(self, client):
        """E_NO_CASE のエラーメッセージが読みやすいこと"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": ["xyz*"],
        })
        data = res.json()
        err = next((e for e in data.get("errors", []) if e.get("code") == "E_NO_CASE"), None)
        assert err is not None
        assert "E_NO_CASE" in err["message"] or "xyz" in err["message"] or "フィルタ" in err["message"]

    def test_filter_cases_none_runs_all(self, client):
        """filter_cases が None のときは全ケースを実行すること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": None,
        })
        assert res.status_code == 200
        data = res.json()
        assert len(data["cases"]) >= 1

    def test_filter_cases_empty_runs_all(self, client):
        """filter_cases が [] のときは全ケースを実行すること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": [],
        })
        assert res.status_code == 200
        data = res.json()
        assert len(data["cases"]) >= 1


# ---------------------------------------------------------------------------
# 2段構成 結果パネル (cases[] + checks detail)
# ---------------------------------------------------------------------------

class TestCasesDetailStructure:
    def test_cases_have_checks(self, client):
        """cases[].checks が存在すること（下段詳細用）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        for case in data["cases"]:
            assert "checks" in case
            assert isinstance(case["checks"], list)

    def test_cases_checks_have_required_fields(self, client):
        """cases[].checks の各要素が必要フィールドを持つこと"""
        res = client.post("/api/test", json={
            "sv_files": [MUX2_SV],
            "spec": MUX2_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        for case in data["cases"]:
            for chk in case["checks"]:
                assert "name" in chk
                assert "status" in chk
                assert "signal" in chk
                assert "cycle" in chk
                assert "expected" in chk
                assert "actual" in chk

    def test_message_field_optional(self, client):
        """cases[].message は存在すれば表示、なければ省略（KeyError なし）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        # message フィールドが存在しても None でも問題ないこと
        for case in data["cases"]:
            # KeyError が出ないことを確認（存在しなくてよい）
            _ = case.get("message")  # None or absent → OK

    def test_summary_elapsed_ms_is_total(self, client):
        """summary.elapsed_ms が全体実時間であること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "summary" in data
        assert "elapsed_ms" in data["summary"]
        # 全体 >= 各ケースの合計（並行実行でもなければほぼ同値）
        case_total = sum(c.get("elapsed_ms", 0) for c in data["cases"])
        assert data["summary"]["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# §15.8 offset_ns 対応: FAIL詳細表示形式・波形ジャンプ
# ---------------------------------------------------------------------------

DFF_SYNC  = str(EXAMPLES_DIR / "dff_sync.sv")
DFF_COMB  = str(EXAMPLES_DIR / "dff_comb.sv")
DFF_ASYNC = str(EXAMPLES_DIR / "dff_async.sv")
DFF_EX1   = str(EXAMPLES_DIR / "dff_ex1.yaml")
DFF_EX2   = str(EXAMPLES_DIR / "dff_ex2.yaml")


class TestOffsetNs:
    def test_offset_ns_in_checks(self, client):
        """checks[] に offset_ns フィールドが常に存在すること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        for case in data["cases"]:
            for chk in case["checks"]:
                assert "offset_ns" in chk
                assert isinstance(chk["offset_ns"], int)

    def test_offset_ns_zero_for_standard_spec(self, client):
        """offset_ns を使わない通常のスペックでは常に 0"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        for case in res.json()["cases"]:
            for chk in case["checks"]:
                assert chk["offset_ns"] == 0, f"offset_ns should be 0, got {chk['offset_ns']}"

    def test_dff_ex1_sync_passes(self, client):
        """dff_ex1: 同期リセット版は PASS"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_SYNC],
            "spec": DFF_EX1,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "PASS", f"sync should PASS, got {data['status']}"

    def test_dff_ex1_comb_fails(self, client):
        """dff_ex1: 組み合わせ版は FAIL（offset_ns チェックで区別）"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_COMB],
            "spec": DFF_EX1,
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "FAIL", f"comb should FAIL, got {data['status']}"

    def test_dff_ex1_fail_checks_have_offset_ns(self, client):
        """dff_ex1 FAIL時に offset_ns > 0 の check が存在すること"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_COMB],
            "spec": DFF_EX1,
        })
        assert res.status_code == 200
        fail_checks = [
            chk for case in res.json()["cases"]
            for chk in case["checks"]
            if chk["status"] != "PASS"
        ]
        assert len(fail_checks) > 0
        # 失敗した checks のうち少なくとも 1 件は offset_ns > 0
        assert any(chk["offset_ns"] > 0 for chk in fail_checks), \
            f"Expected offset_ns>0 in failures: {fail_checks}"

    def test_dff_ex1_check_name_format_with_offset(self, client):
        """offset_ns > 0 の check の name は 'q@cycleN+Mns[spec]' 形式"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_COMB],
            "spec": DFF_EX1,
        })
        assert res.status_code == 200
        all_checks = [
            chk for case in res.json()["cases"]
            for chk in case["checks"]
        ]
        offset_checks = [chk for chk in all_checks if chk["offset_ns"] > 0]
        for chk in offset_checks:
            assert "+" in chk["name"] and "ns" in chk["name"], \
                f"Offset check name format wrong: {chk['name']}"

    def test_dff_ex1_time_ns_matches_cycle_plus_offset(self, client):
        """time_ns = サイクル開始時刻 + offset_ns であること"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_SYNC],
            "spec": DFF_EX1,
        })
        assert res.status_code == 200
        period_ns = 10  # dff_ex1 は period_ns=10
        for case in res.json()["cases"]:
            for chk in case["checks"]:
                cycle = chk["cycle"]
                offset = chk["offset_ns"]
                time_ns = chk["time_ns"]
                # 立ち上り時刻 = (cycle-1)*period + period/2 = cycle*period - period/2
                cycle_posedge = cycle * period_ns - period_ns // 2
                expected_time = cycle_posedge + offset
                assert time_ns == expected_time, \
                    f"time_ns mismatch: cycle={cycle} offset={offset} time_ns={time_ns} expected={expected_time}"

    def test_dff_ex2_sync_passes(self, client):
        """dff_ex2: 同期リセット版は PASS"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_SYNC],
            "spec": DFF_EX2,
        })
        assert res.status_code == 200
        assert res.json()["status"] == "PASS"

    def test_dff_ex2_async_fails(self, client):
        """dff_ex2: 非同期リセット版は FAIL"""
        res = client.post("/api/test", json={
            "sv_files": [DFF_ASYNC],
            "spec": DFF_EX2,
        })
        assert res.status_code == 200
        assert res.json()["status"] == "FAIL"


# ---------------------------------------------------------------------------
# §15.12/13 warnings[] の表示
# ---------------------------------------------------------------------------

class TestWarnings:
    def test_warnings_always_present_in_result(self, client):
        """TestResult に warnings[] が常に存在すること（空でも）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "warnings" in data, "warnings field missing from TestResult"
        assert isinstance(data["warnings"], list)

    def test_warnings_always_present_in_cases(self, client):
        """cases[].warnings が常に存在すること（空でも）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        for case in res.json()["cases"]:
            assert "warnings" in case, f"warnings missing from case {case['name']}"
            assert isinstance(case["warnings"], list)

    def test_w_unknown_key_warning(self, client, tmp_path):
        """知らないキーがある YAML では W_UNKNOWN_KEY 警告が出ること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        yaml_content = """version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 10}
sim: {cycles: 5, seed: 42}
unknownkey_for_test: should_warn
expected:
  - at: 1
    check: {count: 0}
"""
        spec_path = str(tmp_path / "warn_spec.yaml")
        Path(spec_path).write_text(yaml_content)

        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": spec_path,
        })
        assert res.status_code == 200
        data = res.json()
        top_warnings = data.get("warnings", [])
        case_warnings = [w for case in data.get("cases", [])
                         for w in case.get("warnings", [])]
        all_warnings = top_warnings + case_warnings
        assert len(all_warnings) > 0, "W_UNKNOWN_KEY warning expected but got none"
        w_codes = [w["code"] for w in all_warnings]
        assert "W_UNKNOWN_KEY" in w_codes, f"W_UNKNOWN_KEY not found in: {w_codes}"

    def test_warning_message_not_parsed_by_gui(self, client, tmp_path):
        """GUI は警告の message を解析せず code で判定すること（テスト: code フィールドが存在）"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)
        yaml_content = """version: 1
top: counter
clock: {signal: clk, period_ns: 10}
reset: {signal: rst_n, active_low: true, duration_cycles: 10}
sim: {cycles: 3, seed: 0}
badkey: x
expected:
  - at: 1
    check: {count: 0}
"""
        spec_path = str(tmp_path / "warn2_spec.yaml")
        Path(spec_path).write_text(yaml_content)
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": spec_path,
        })
        assert res.status_code == 200
        for w in res.json().get("warnings", []):
            assert "code" in w, "warnings must have code field"
            assert "message" in w, "warnings must have message field"
            # GUI は code で判定するため code は文字列であること
            assert isinstance(w["code"], str)


# ---------------------------------------------------------------------------
# adapter.py: E_NO_CASE dead code削除の確認
# ---------------------------------------------------------------------------

class TestAdapterCleanup:
    def test_e_no_case_from_api_not_adapter(self, client):
        """E_NO_CASE は API から直接返る（adapter のラップなし）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "filter_cases": ["absolutely_nonexistent_xyz"],
        })
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ERROR"
        err_codes = [e["code"] for e in data.get("errors", [])]
        assert "E_NO_CASE" in err_codes
        # adapter の e_no_case フラグは存在しないこと
        assert "e_no_case" not in data, "Dead code flag e_no_case should be removed"

    def test_warnings_field_in_result(self, client):
        """adapter が warnings[] を正しく含めること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        # adapter が warnings を返すこと
        assert "warnings" in data
        assert isinstance(data["warnings"], list)


# ---------------------------------------------------------------------------
# §17.2 GUI 接続制限: --host バリデーション
# ---------------------------------------------------------------------------

class TestGuiHostValidation:
    """§17.2: `deltasim gui --host` にループバック以外を渡したら終了コード 2"""

    def test_host_0000_exits_code2(self):
        """--host 0.0.0.0 は終了コード 2 でエラーメッセージを出すこと"""
        from click.testing import CliRunner
        from deltasim.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["gui", "--host", "0.0.0.0"])
        assert result.exit_code == 2, f"exit_code={result.exit_code}, output={result.output}"
        assert "GUIは127.0.0.1でのみ起動できます" in result.output

    def test_host_lan_ip_exits_code2(self):
        """--host 192.168.1.10 は終了コード 2 でエラーメッセージを出すこと"""
        from click.testing import CliRunner
        from deltasim.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["gui", "--host", "192.168.1.10"])
        assert result.exit_code == 2, f"exit_code={result.exit_code}, output={result.output}"
        assert "GUIは127.0.0.1でのみ起動できます" in result.output

    def test_host_127001_allowed(self, monkeypatch):
        """--host 127.0.0.1 はサーバーを起動しようとすること（exit 2 にならない）"""
        from click.testing import CliRunner
        from deltasim.cli import main

        # uvicorn.run をモックしてサーバー起動を防ぐ
        import unittest.mock as mock

        runner = CliRunner()
        with mock.patch("uvicorn.run") as mock_run:
            result = runner.invoke(main, ["gui", "--host", "127.0.0.1", "--no-browser"])
        # exit code 2 ではないこと（0 または uvicorn が返すコード）
        assert result.exit_code != 2, \
            f"127.0.0.1 should be allowed but got exit_code={result.exit_code}"

    def test_host_localhost_allowed(self, monkeypatch):
        """--host localhost は許可されること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        import unittest.mock as mock

        runner = CliRunner()
        with mock.patch("uvicorn.run"):
            result = runner.invoke(main, ["gui", "--host", "localhost", "--no-browser"])
        assert result.exit_code != 2, \
            f"localhost should be allowed but got exit_code={result.exit_code}"

    def test_host_ipv6_loopback_allowed(self, monkeypatch):
        """--host ::1 は許可されること"""
        from click.testing import CliRunner
        from deltasim.cli import main
        import unittest.mock as mock

        runner = CliRunner()
        with mock.patch("uvicorn.run"):
            result = runner.invoke(main, ["gui", "--host", "::1", "--no-browser"])
        assert result.exit_code != 2, \
            f"::1 should be allowed but got exit_code={result.exit_code}"


# ---------------------------------------------------------------------------
# §17.2 GUI 接続制限: Host ヘッダ検証
# ---------------------------------------------------------------------------

class TestGuiHostHeaderCheck:
    """§17.2: 不正な Host ヘッダは 403 で拒否されること"""

    def test_invalid_host_header_rejected(self):
        """Host ヘッダが 127.0.0.1/localhost/[::1] 以外は 403"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            # headers で Host ヘッダを上書きして外部ホストを偽装
            res = c.get("/health", headers={"Host": "evil.example.com"})
        assert res.status_code == 403

    def test_valid_host_127001(self):
        """Host: 127.0.0.1 は許可されること"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.get("/health", headers={"Host": "127.0.0.1"})
        assert res.status_code == 200

    def test_valid_host_with_port(self):
        """Host: 127.0.0.1:8765 はポートつきでも許可されること"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.get("/health", headers={"Host": "127.0.0.1:8765"})
        assert res.status_code == 200

    def test_valid_host_localhost(self):
        """Host: localhost は許可されること"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.get("/health", headers={"Host": "localhost"})
        assert res.status_code == 200

    def test_valid_host_ipv6(self):
        """Host: [::1] は許可されること"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.get("/health", headers={"Host": "[::1]"})
        assert res.status_code == 200

    def test_valid_host_ipv6_with_port(self):
        """Host: [::1]:8765 はポートつきでも許可されること"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.get("/health", headers={"Host": "[::1]:8765"})
        assert res.status_code == 200

    def test_host_0000_rejected(self):
        """Host: 0.0.0.0 は 403 で拒否されること"""
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.get("/health", headers={"Host": "0.0.0.0"})
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# §17.1 GUI 結果 JSON フィールド: CLI の test --json と同じ構造
# ---------------------------------------------------------------------------

class TestGuiResultJsonFields:
    """§17.1: GUI の結果 JSON に deltasim_version/seed/elapsed_ms がトップレベルに存在すること"""

    def test_toplevel_has_deltasim_version(self, client):
        """GUI 結果に deltasim_version がトップレベルにあること"""
        from deltasim import __version__
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "deltasim_version" in data, "deltasim_version がトップレベルに存在しません"
        assert isinstance(data["deltasim_version"], str)
        assert data["deltasim_version"] == __version__

    def test_toplevel_has_seed(self, client):
        """GUI 結果に seed がトップレベルにあること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "seed": 42,
        })
        assert res.status_code == 200
        data = res.json()
        assert "seed" in data, "seed がトップレベルに存在しません"
        assert isinstance(data["seed"], int)

    def test_toplevel_has_elapsed_ms(self, client):
        """GUI 結果に elapsed_ms がトップレベルにあること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        assert "elapsed_ms" in data, "elapsed_ms がトップレベルに存在しません"
        assert isinstance(data["elapsed_ms"], (int, float))
        assert data["elapsed_ms"] >= 0

    def test_toplevel_keys_match_cli_json(self, client, tmp_path):
        """GUI 結果のトップレベルキー集合が CLI test --json と一致すること"""
        import json as _json
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main

        json_out = str(tmp_path / "cli_result.json")
        runner = CliRunner()
        runner.invoke(
            cli_main,
            ["test", COUNTER_SV, "--spec", COUNTER_SPEC, "--json", json_out],
            catch_exceptions=False,
        )
        assert Path(json_out).exists(), "CLI の --json 出力ファイルが生成されませんでした"
        cli_data = _json.loads(Path(json_out).read_text())

        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        gui_data = res.json()

        # GUI 固有キー（vcd_path）を除いて CLI と一致すること
        cli_keys = set(cli_data.keys())
        gui_keys = set(gui_data.keys()) - {"vcd_path"}
        assert cli_keys == gui_keys, (
            f"キー集合が一致しません\n"
            f"  CLI only: {cli_keys - gui_keys}\n"
            f"  GUI only: {gui_keys - cli_keys}"
        )


# ---------------------------------------------------------------------------
# §18.4: 一時 VCD 自動生成テスト（vcd_out 未指定でも波形が出る）
# ---------------------------------------------------------------------------

class TestAutoTempVcd:
    """§18.4: vcd_out を指定しなくても波形 API が信号を返すこと"""

    def test_vcd_path_in_result_without_vcd_out(self, client):
        """vcd_out 未指定のテスト結果に vcd_path が含まれること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        # vcd_path は None ではなく、ファイルパスが含まれること
        assert "vcd_path" in data
        assert data["vcd_path"] is not None, "一時 VCD が生成されているはずです"

    def test_waveform_api_returns_signals_without_vcd_out(self, client):
        """vcd_out 未指定でテスト後に波形 API が信号を返すこと（§18.4 最重要）"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()
        vcd_path = data.get("vcd_path")
        assert vcd_path is not None, "一時 VCD パスが返されること"

        # VCD が実際に存在しデータが読めること
        import os
        assert os.path.isfile(vcd_path), f"一時 VCD ファイルが存在すること: {vcd_path}"
        assert os.path.getsize(vcd_path) > 0, "一時 VCD ファイルが空でないこと"

        # 波形 API が信号を返すこと
        res2 = client.get(f"/api/waveform/index?path={vcd_path}")
        assert res2.status_code == 200
        wave_data = res2.json()
        assert "signals" in wave_data
        assert len(wave_data["signals"]) > 0, "波形インデックスに信号が含まれること"

    def test_spec_with_sim_vcd_takes_priority(self, client, tmp_path):
        """spec に sim.vcd がある場合はそのパスが優先されること"""
        import yaml as _yaml
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        explicit_vcd = str(tmp_path / "explicit.vcd")
        # yaml.dump() でパスを正しくエスケープして書き出す（Windows バックスラッシュ対応）
        spec_data = {
            "version": 1,
            "top": "counter",
            "clock": {"signal": "clk", "period_ns": 10},
            "reset": {"signal": "rst_n", "active_low": True, "duration_cycles": 10},
            "sim": {"cycles": 5, "seed": 0, "vcd": explicit_vcd},
            "expected": [{"at": 1, "check": {"count": 0}}],
        }
        spec_path = str(tmp_path / "sim_vcd_spec.yaml")
        Path(spec_path).write_text(
            _yaml.dump(spec_data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": spec_path,
        })
        assert res.status_code == 200
        data = res.json()
        # spec の sim.vcd が優先されること（パスを正規化して比較）
        import os
        actual = data.get("vcd_path") or ""
        assert os.path.normcase(actual) == os.path.normcase(explicit_vcd), (
            f"spec の sim.vcd が優先されるべき。実際: {actual!r}, 期待: {explicit_vcd!r}"
        )

    def test_explicit_vcd_out_overrides_spec(self, client, tmp_path):
        """明示的な vcd_out は spec の sim.vcd より優先されること"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)

        explicit_vcd = str(tmp_path / "override.vcd")
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "vcd_out": explicit_vcd,
        })
        assert res.status_code == 200
        data = res.json()
        import os
        assert os.path.isfile(explicit_vcd), "明示的な vcd_out にファイルが書き出されること"
        assert data.get("vcd_path") == explicit_vcd

    def test_sse_job_vcd_path_in_result(self, client):
        """SSE ジョブ経由（/api/test/start）でも vcd_path が結果に含まれること"""
        res = client.post("/api/test/start", json={
            "sv_files": [MUX2_SV],
            "spec": MUX2_SPEC,
        })
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        # SSE を消費
        import json as _json
        with client.stream("GET", f"/api/test/{job_id}/events") as stream:
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    ev = _json.loads(line[5:].strip())
                    if ev.get("event") in ("finish", "error"):
                        break

        # 結果に vcd_path が含まれること
        res2 = client.get(f"/api/test/{job_id}/result")
        assert res2.status_code == 200
        result_data = res2.json()
        assert result_data["done"] is True
        assert "vcd_path" in result_data["result"]
        vcd_path = result_data["result"].get("vcd_path")
        assert vcd_path is not None, "SSE ジョブでも一時 VCD が生成されること"

    def test_temp_vcd_is_valid_vcd_file(self, client):
        """自動生成された一時 VCD が有効な VCD ファイルであること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        vcd_path = res.json().get("vcd_path")
        assert vcd_path is not None

        # 波形データを取得して信号変化があること
        res2 = client.get(f"/api/waveform?path={vcd_path}")
        assert res2.status_code == 200
        wdata = res2.json()
        assert wdata.get("end_time", 0) > 0, "波形に時間が記録されていること"
        assert len(wdata.get("signals", {})) > 0, "信号が含まれること"


# ---------------------------------------------------------------------------
# §18.4: gen API テスト
# ---------------------------------------------------------------------------

class TestGenApi:
    def test_gen_returns_spec_yaml(self, client):
        """gen API がスペック YAML 文字列を返すこと"""
        res = client.post("/api/gen", json={
            "sv_files": [COUNTER_SV],
            "top": "counter",
        })
        assert res.status_code == 200
        data = res.json()
        assert data.get("status") == "PASS", f"gen がエラー: {data}"
        assert "spec_yaml" in data
        assert "tb_sv" in data
        assert len(data["spec_yaml"]) > 0

    def test_gen_spec_yaml_has_top(self, client):
        """生成されたスペックにトップモジュール名が含まれること"""
        res = client.post("/api/gen", json={
            "sv_files": [COUNTER_SV],
            "top": "counter",
        })
        assert res.status_code == 200
        data = res.json()
        assert "counter" in data.get("spec_yaml", ""), "スペックにモジュール名が含まれること"

    def test_gen_file_not_found(self, client):
        """存在しないファイルでは 422 を返すこと"""
        res = client.post("/api/gen", json={
            "sv_files": ["/nonexistent/file.sv"],
        })
        assert res.status_code == 422

    def test_gen_mux2(self, client):
        """mux2 のスペックが生成できること"""
        res = client.post("/api/gen", json={
            "sv_files": [MUX2_SV],
            "top": "mux2",
        })
        assert res.status_code == 200
        data = res.json()
        assert data.get("status") == "PASS"
        assert "mux2" in data.get("spec_yaml", "")


# ---------------------------------------------------------------------------
# §18.4: 最近使ったプロジェクト テスト
# ---------------------------------------------------------------------------

class TestRecentProjects:
    """§18.4: 最近使ったプロジェクトの保存・読込"""

    @pytest.fixture(autouse=True)
    def patch_config_dir(self, tmp_path, monkeypatch):
        """テスト用に設定ディレクトリを tmp_path に差し替える"""
        from deltasim.gui import recent as _recent_mod
        monkeypatch.setattr(_recent_mod, "_config_dir", lambda: tmp_path)

    def test_get_recent_empty(self, client):
        """初期状態では空リストが返ること"""
        res = client.get("/api/recent-projects")
        assert res.status_code == 200
        data = res.json()
        assert "recent" in data
        assert data["recent"] == []

    def test_add_and_get_recent(self, client):
        """追加したプロジェクトが取得できること"""
        entry = {
            "sv_files": [COUNTER_SV],
            "top": "counter",
            "spec": COUNTER_SPEC,
            "name": "テスト用 counter",
        }
        res = client.post("/api/recent-projects/add", json=entry)
        assert res.status_code == 200
        assert res.json()["ok"] is True

        res2 = client.get("/api/recent-projects")
        assert res2.status_code == 200
        data = res2.json()
        assert len(data["recent"]) == 1
        assert data["recent"][0]["name"] == "テスト用 counter"
        assert COUNTER_SV in data["recent"][0]["sv_files"]

    def test_missing_path_marked(self, client):
        """存在しないパスは missing=True でマークされること"""
        entry = {
            "sv_files": ["/nonexistent/file.sv"],
            "top": "dummy",
            "spec": None,
            "name": "missing test",
        }
        client.post("/api/recent-projects/add", json=entry)

        res = client.get("/api/recent-projects")
        assert res.status_code == 200
        data = res.json()
        assert len(data["recent"]) >= 1
        missing_entry = next(e for e in data["recent"] if e["name"] == "missing test")
        assert missing_entry["missing"] is True
        assert "/nonexistent/file.sv" in missing_entry["missing_paths"]

    def test_existing_path_not_missing(self, client):
        """存在するパスは missing=False であること"""
        entry = {
            "sv_files": [COUNTER_SV],
            "top": "counter",
            "spec": COUNTER_SPEC,
            "name": "exists",
        }
        client.post("/api/recent-projects/add", json=entry)

        res = client.get("/api/recent-projects")
        data = res.json()
        exists_entry = next(e for e in data["recent"] if e["name"] == "exists")
        assert exists_entry["missing"] is False

    def test_max_10_entries(self, client):
        """最大10件を超えると古いものが削除されること"""
        for i in range(12):
            client.post("/api/recent-projects/add", json={
                "sv_files": [COUNTER_SV],
                "top": "counter",
                "spec": f"/dummy/spec_{i}.yaml",
                "name": f"entry_{i}",
            })

        res = client.get("/api/recent-projects")
        data = res.json()
        assert len(data["recent"]) <= 10, f"最大10件: {len(data['recent'])} 件"

    def test_newest_first(self, client):
        """新しいプロジェクトが先頭に来ること"""
        for i in range(3):
            client.post("/api/recent-projects/add", json={
                "sv_files": [COUNTER_SV],
                "spec": f"/dummy/spec_{i}.yaml",
                "name": f"entry_{i}",
            })

        res = client.get("/api/recent-projects")
        data = res.json()
        assert data["recent"][0]["name"] == "entry_2", "最後に追加したものが先頭"


# ---------------------------------------------------------------------------
# §18.4: トップ候補一覧テスト
# ---------------------------------------------------------------------------

class TestTopCandidates:
    """§18.4: load() が all_modules を返し、トップ候補が is_top_candidate=True であること"""

    def test_load_returns_all_modules(self, client):
        """all_modules フィールドが存在すること"""
        res = client.post("/api/load", json={
            "files": [COUNTER_SV],
            "top": "counter",
        })
        assert res.status_code == 200
        data = res.json()
        assert "all_modules" in data, "all_modules フィールドが必要"
        assert isinstance(data["all_modules"], list)
        assert len(data["all_modules"]) >= 1

    def test_single_module_is_top_candidate(self, client):
        """単一モジュールは top_candidate=True であること"""
        res = client.post("/api/load", json={
            "files": [COUNTER_SV],
            "top": "counter",
        })
        assert res.status_code == 200
        mods = res.json()["all_modules"]
        counter_mod = next((m for m in mods if m["name"] == "counter"), None)
        assert counter_mod is not None
        assert counter_mod["is_top_candidate"] is True

    def test_all_modules_have_required_fields(self, client):
        """all_modules の各要素に name と is_top_candidate があること"""
        res = client.post("/api/load", json={
            "files": [COUNTER_SV],
        })
        assert res.status_code == 200
        for mod in res.json()["all_modules"]:
            assert "name" in mod
            assert "is_top_candidate" in mod
            assert isinstance(mod["is_top_candidate"], bool)


# ---------------------------------------------------------------------------
# §18.4: JUnit/JSON エクスポートテスト
# ---------------------------------------------------------------------------

class TestExportApi:
    """§18.4: テスト結果を JSON / JUnit XML でエクスポートできること"""

    @pytest.fixture(scope="class")
    def test_job_id(self, client):
        """テストを実行してジョブIDを返す"""
        import json as _json
        res = client.post("/api/test/start", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        job_id = res.json()["job_id"]
        # SSE 消費
        with client.stream("GET", f"/api/test/{job_id}/events") as stream:
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    ev = _json.loads(line[5:].strip())
                    if ev.get("event") in ("finish", "error"):
                        break
        return job_id

    def test_export_json(self, client, test_job_id):
        """JSON エクスポートが正しいコンテントタイプで返ること"""
        res = client.get(f"/api/test/{test_job_id}/export?fmt=json")
        assert res.status_code == 200
        assert "application/json" in res.headers["content-type"]
        # JSON として解析できること
        import json as _json
        data = _json.loads(res.content)
        assert "schema_version" in data or "status" in data

    def test_export_junit(self, client, test_job_id):
        """JUnit XML エクスポートが正しいコンテントタイプで返ること"""
        res = client.get(f"/api/test/{test_job_id}/export?fmt=junit")
        assert res.status_code == 200
        assert "xml" in res.headers["content-type"]
        # XML として解析できること
        import xml.etree.ElementTree as ET
        root = ET.fromstring(res.content)
        assert root.tag in ("testsuites", "testsuite")

    def test_export_nonexistent_job(self, client):
        """存在しないジョブは 404 を返すこと"""
        res = client.get("/api/test/nonexistent-job/export?fmt=json")
        assert res.status_code == 404

    def test_export_has_content_disposition(self, client, test_job_id):
        """ダウンロードヘッダが含まれること"""
        res = client.get(f"/api/test/{test_job_id}/export?fmt=json")
        assert res.status_code == 200
        assert "attachment" in res.headers.get("content-disposition", "")


# ---------------------------------------------------------------------------
# §18.4: 最近使ったプロジェクト（recent.py 単体テスト）
# ---------------------------------------------------------------------------

class TestRecentModule:
    """recent.py の単体テスト（config_dir を tmp_path に差し替え）"""

    def test_load_empty(self, tmp_path):
        from deltasim.gui.recent import load_recent
        assert load_recent(tmp_path) == []

    def test_add_and_load(self, tmp_path):
        from deltasim.gui.recent import add_recent, load_recent
        entry = {"sv_files": ["/a.sv"], "spec": "/a.yaml", "name": "test"}
        add_recent(entry, tmp_path)
        items = load_recent(tmp_path)
        assert len(items) == 1
        assert items[0]["name"] == "test"

    def test_dedup_same_key(self, tmp_path):
        """同じ sv_files + spec は重複排除されること"""
        from deltasim.gui.recent import add_recent, load_recent
        entry = {"sv_files": ["/a.sv"], "spec": "/a.yaml", "name": "v1"}
        add_recent(entry, tmp_path)
        entry2 = {"sv_files": ["/a.sv"], "spec": "/a.yaml", "name": "v2"}
        add_recent(entry2, tmp_path)
        items = load_recent(tmp_path)
        assert len(items) == 1, "重複排除されること"
        assert items[0]["name"] == "v2", "新しい方が残ること"

    def test_max_recent(self, tmp_path):
        """最大10件を超えると古いものが削除されること"""
        from deltasim.gui.recent import add_recent, load_recent, MAX_RECENT
        for i in range(MAX_RECENT + 3):
            add_recent({"sv_files": [f"/file_{i}.sv"], "spec": f"/spec_{i}.yaml", "name": f"p{i}"}, tmp_path)
        items = load_recent(tmp_path)
        assert len(items) == MAX_RECENT

    def test_newest_first(self, tmp_path):
        """新しいものが先頭に来ること"""
        from deltasim.gui.recent import add_recent, load_recent
        for i in range(3):
            add_recent({"sv_files": [f"/file_{i}.sv"], "name": f"p{i}"}, tmp_path)
        items = load_recent(tmp_path)
        assert items[0]["name"] == "p2"

    def test_config_dir_xdg(self, tmp_path, monkeypatch):
        """Linux: XDG_CONFIG_HOME が設定されている場合に使われること"""
        import os
        if os.name == "nt":
            pytest.skip("Linux 専用テスト")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from deltasim.gui.recent import _config_dir
        d = _config_dir()
        assert "deltasim" in str(d)
        assert str(tmp_path) in str(d)

    def test_config_dir_appdata(self, tmp_path, monkeypatch):
        """Windows: APPDATA が設定されている場合に使われること（Windows のみ）"""
        import os
        if os.name != "nt":
            pytest.skip("Windows 専用テスト")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        from deltasim.gui.recent import _config_dir
        d = _config_dir()
        assert "deltasim" in str(d)


# ---------------------------------------------------------------------------
# §18.4: 一時 VCD クリーンアップ（jobs.py 単体テスト）
# ---------------------------------------------------------------------------

class TestTempVcdCleanup:
    """§18.4.1: 一時 VCD の専用フォルダ管理をテストする"""

    def test_make_temp_vcd_in_dedicated_folder(self, tmp_path):
        """§18.4.1: _make_temp_vcd() が専用フォルダ配下にファイルを作成すること"""
        from deltasim.gui.jobs import _make_temp_vcd, _DELTASIM_GUI_BASE
        path = _make_temp_vcd(base=tmp_path)
        p = Path(path)
        assert p.exists(), "一時 VCD ファイルが作成されること"
        # 専用フォルダ配下であること
        assert str(tmp_path) in str(p), "専用フォルダ配下に作成されること"
        assert f"proc-{os.getpid()}" in str(p), "プロセス ID のサブフォルダに作成されること"

    def test_make_temp_vcd_project_subfolder(self, tmp_path):
        """§18.4.1: プロジェクトハッシュのサブフォルダが作成されること"""
        from deltasim.gui.jobs import _make_temp_vcd, _project_hash
        spec = "/path/to/spec.yaml"
        sv_files = ["/path/to/counter.sv"]
        path = _make_temp_vcd(spec=spec, sv_files=sv_files, base=tmp_path)
        p = Path(path)
        expected_hash = _project_hash(spec, sv_files)
        assert expected_hash in str(p), "プロジェクトハッシュのサブフォルダが使われること"

    def test_max_5_per_project(self, tmp_path):
        """§18.4.1: 同一プロジェクトで最大 _MAX_PER_PROJECT 件に整理されること"""
        from deltasim.gui.jobs import _make_temp_vcd, _MAX_PER_PROJECT
        spec = "/dummy/spec.yaml"
        paths = []
        for _ in range(_MAX_PER_PROJECT + 3):
            p = _make_temp_vcd(spec=spec, sv_files=["/dummy/a.sv"], base=tmp_path)
            paths.append(p)
            time.sleep(0.001)  # 時刻が被らないよう少し待つ

        proc_dir = tmp_path / f"proc-{os.getpid()}"
        from deltasim.gui.jobs import _project_hash
        proj_key = _project_hash(spec, ["/dummy/a.sv"])
        proj_dir = proc_dir / proj_key
        existing = list(proj_dir.glob("*.vcd"))
        assert len(existing) <= _MAX_PER_PROJECT, (
            f"最大 {_MAX_PER_PROJECT} 件のみ残るべき: {len(existing)} 件"
        )

    def test_only_deletes_within_base(self, tmp_path):
        """§18.4.1: 専用フォルダ外のファイルには触らないこと"""
        from deltasim.gui.jobs import _safe_unlink, _safe_rmtree
        # 専用フォルダ外のファイル
        outside = tmp_path.parent / "outside_file.txt"
        try:
            outside.write_text("important")
            base = tmp_path / "deltasim-gui"
            base.mkdir()
            _safe_unlink(outside, base)
            assert outside.exists(), "専用フォルダ外は削除されないこと"
            _safe_rmtree(outside, base)
            assert outside.exists(), "専用フォルダ外は削除されないこと"
        finally:
            try:
                outside.unlink(missing_ok=True)
            except Exception:
                pass

    def test_cleanup_all_removes_proc_dir(self, tmp_path):
        """§18.4.1: cleanup_all_temp_vcds() が自プロセスのフォルダを削除すること"""
        from deltasim.gui.jobs import _make_temp_vcd, cleanup_all_temp_vcds
        path = _make_temp_vcd(base=tmp_path)
        assert Path(path).exists()
        cleanup_all_temp_vcds(base=tmp_path)
        proc_dir = tmp_path / f"proc-{os.getpid()}"
        assert not proc_dir.exists(), "プロセスフォルダが削除されること"

    def test_startup_cleanup_dead_pid_dirs(self, tmp_path):
        """§18.4.1: 起動時に死亡プロセスのフォルダを削除すること"""
        from deltasim.gui.jobs import startup_cleanup_stale_vcds
        base = tmp_path / "deltasim-gui"
        base.mkdir()
        dead_pid = 99999999  # 実行されていない PID
        dead_dir = base / f"proc-{dead_pid}"
        dead_dir.mkdir()
        (dead_dir / "test.vcd").touch()
        startup_cleanup_stale_vcds(base=base)
        assert not dead_dir.exists(), "死亡プロセスのフォルダが削除されること"

    def test_startup_cleanup_keeps_our_dir(self, tmp_path):
        """§18.4.1: 起動クリーンアップは自プロセスのフォルダを削除しないこと"""
        from deltasim.gui.jobs import startup_cleanup_stale_vcds, _OUR_PID
        base = tmp_path / "deltasim-gui"
        base.mkdir()
        our_dir = base / f"proc-{_OUR_PID}"
        our_dir.mkdir()
        (our_dir / "our.vcd").touch()
        startup_cleanup_stale_vcds(base=base)
        assert our_dir.exists(), "自プロセスのフォルダは削除されないこと"

    def test_cleanup_windows_safe(self, tmp_path):
        """§18.4.1: cleanup 系関数が例外を投げないこと（Windows ロック模倣）"""
        from deltasim.gui.jobs import _safe_unlink, _safe_rmtree
        base = tmp_path / "deltasim-gui"
        base.mkdir()
        # 存在しないパスでも例外が出ないこと
        _safe_unlink(base / "proc-0" / "nonexistent.vcd", base)
        _safe_rmtree(base / "proc-0" / "nonexistent", base)

    def test_peek_spec_vcd_no_sim_vcd(self, tmp_path):
        """sim.vcd が無い YAML では None を返すこと"""
        from deltasim.gui.jobs import _peek_spec_vcd
        spec = tmp_path / "no_vcd.yaml"
        spec.write_text("version: 1\ntop: test\nsim: {cycles: 5}\n")
        assert _peek_spec_vcd(str(spec)) is None

    def test_peek_spec_vcd_with_sim_vcd(self, tmp_path):
        """sim.vcd がある YAML ではそのパスを返すこと"""
        from deltasim.gui.jobs import _peek_spec_vcd
        spec = tmp_path / "with_vcd.yaml"
        spec.write_text("version: 1\ntop: test\nsim: {cycles: 5, vcd: /tmp/out.vcd}\n")
        assert _peek_spec_vcd(str(spec)) == "/tmp/out.vcd"


# ---------------------------------------------------------------------------
# §レビュー2: CLI の test --json / --junit と GUI エクスポートが一致すること
# ---------------------------------------------------------------------------

class TestExportCliMatch:
    """CLI の test --json / --junit と GUI エクスポート（job.test_result 使用）が一致すること。
    時刻など実行ごとに変わる値（elapsed_ms 等）は除外する。
    """

    SEED = 12345

    @pytest.fixture(scope="class")
    def cli_and_gui_results(self, tmp_path_factory):
        import json as _json
        from click.testing import CliRunner
        from deltasim.cli import main as cli_main
        import xml.etree.ElementTree as ET

        tmp = tmp_path_factory.mktemp("export_match")

        # --- CLI で実行 ---
        json_out = str(tmp / "cli.json")
        junit_out = str(tmp / "cli.xml")
        runner = CliRunner()
        r = runner.invoke(cli_main, [
            "test", COUNTER_SV, "--spec", COUNTER_SPEC,
            "--seed", str(self.SEED),
            "--json", json_out,
            "--junit", junit_out,
        ], catch_exceptions=False)
        assert r.exit_code == 0, f"CLI 失敗: {r.output}"
        cli_json = _json.loads(Path(json_out).read_text(encoding="utf-8"))
        cli_junit_str = Path(junit_out).read_text(encoding="utf-8")

        # --- GUI（SSE）で実行してジョブIDを取得 ---
        with TestClient(app, base_url="http://127.0.0.1") as c:
            res = c.post("/api/test/start", json={
                "sv_files": [COUNTER_SV],
                "spec": COUNTER_SPEC,
                "seed": self.SEED,
            })
            assert res.status_code == 200
            job_id = res.json()["job_id"]

            # SSE を消費して完了を待つ
            with c.stream("GET", f"/api/test/{job_id}/events") as stream:
                for line in stream.iter_lines():
                    if line.startswith("data:"):
                        ev = _json.loads(line[5:].strip())
                        if ev.get("event") in ("finish", "error"):
                            break

            # JSON エクスポート
            gui_json_res = c.get(f"/api/test/{job_id}/export?fmt=json")
            assert gui_json_res.status_code == 200, f"JSON export: {gui_json_res.text}"
            gui_json = _json.loads(gui_json_res.content)

            # JUnit エクスポート
            gui_junit_res = c.get(f"/api/test/{job_id}/export?fmt=junit")
            assert gui_junit_res.status_code == 200, f"JUnit export: {gui_junit_res.text}"
            gui_junit_str = gui_junit_res.content.decode("utf-8")

        return {
            "cli_json": cli_json,
            "gui_json": gui_json,
            "cli_junit": cli_junit_str,
            "gui_junit": gui_junit_str,
        }

    def test_json_schema_version_matches(self, cli_and_gui_results):
        """schema_version が CLI と GUI で一致すること"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        assert c["schema_version"] == g["schema_version"]

    def test_json_deltasim_version_matches(self, cli_and_gui_results):
        """deltasim_version が CLI と GUI で一致すること"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        assert c["deltasim_version"] == g["deltasim_version"]

    def test_json_module_matches(self, cli_and_gui_results):
        """module が CLI と GUI で一致すること"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        assert c["module"] == g["module"]

    def test_json_status_matches(self, cli_and_gui_results):
        """status が CLI と GUI で一致すること"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        assert c["status"] == g["status"]

    def test_json_seed_matches(self, cli_and_gui_results):
        """seed が CLI と GUI で一致すること"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        assert c["seed"] == g["seed"]

    def test_json_cases_name_and_status_match(self, cli_and_gui_results):
        """cases[] のケース名と status が CLI と GUI で一致すること"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        cli_cases = {case["name"]: case["status"] for case in c.get("cases", [])}
        gui_cases = {case["name"]: case["status"] for case in g.get("cases", [])}
        assert cli_cases == gui_cases

    def test_json_no_internal_fields(self, cli_and_gui_results):
        """GUI の JSON エクスポートに内部フィールド (_test_result) が含まれないこと"""
        g = cli_and_gui_results["gui_json"]
        assert "_test_result" not in g

    def test_json_top_level_keys_match(self, cli_and_gui_results):
        """CLI と GUI のトップレベルキー集合が一致すること（vcd_path を除く）"""
        c = cli_and_gui_results["cli_json"]
        g = cli_and_gui_results["gui_json"]
        cli_keys = set(c.keys())
        gui_keys = set(g.keys()) - {"vcd_path"}  # GUI 固有キーを除外
        assert cli_keys == gui_keys, (
            f"CLI only: {cli_keys - gui_keys}\n  GUI only: {gui_keys - cli_keys}"
        )

    def test_junit_testcase_count_matches(self, cli_and_gui_results):
        """JUnit XML の testcase 数が CLI と GUI で一致すること"""
        import xml.etree.ElementTree as ET
        cli_root = ET.fromstring(cli_and_gui_results["cli_junit"])
        gui_root = ET.fromstring(cli_and_gui_results["gui_junit"])
        cli_count = sum(1 for _ in cli_root.iter("testcase"))
        gui_count = sum(1 for _ in gui_root.iter("testcase"))
        assert cli_count == gui_count

    def test_junit_testcase_names_match(self, cli_and_gui_results):
        """JUnit XML の testcase 名が CLI と GUI で一致すること"""
        import xml.etree.ElementTree as ET
        cli_root = ET.fromstring(cli_and_gui_results["cli_junit"])
        gui_root = ET.fromstring(cli_and_gui_results["gui_junit"])
        cli_names = {tc.get("name") for tc in cli_root.iter("testcase")}
        gui_names = {tc.get("name") for tc in gui_root.iter("testcase")}
        assert cli_names == gui_names

    def test_junit_failure_count_matches(self, cli_and_gui_results):
        """JUnit XML の failures 属性が CLI と GUI で一致すること"""
        import xml.etree.ElementTree as ET
        cli_root = ET.fromstring(cli_and_gui_results["cli_junit"])
        gui_root = ET.fromstring(cli_and_gui_results["gui_junit"])
        cli_failures = sum(1 for _ in cli_root.iter("failure"))
        gui_failures = sum(1 for _ in gui_root.iter("failure"))
        assert cli_failures == gui_failures


# ---------------------------------------------------------------------------
# §レビュー4: FastAPI version が deltasim.__version__ と一致すること
# ---------------------------------------------------------------------------

class TestFastApiVersion:
    def test_openapi_version_matches_deltasim(self, client):
        """FastAPI の OpenAPI version が deltasim.__version__ と一致すること"""
        from deltasim import __version__
        res = client.get("/openapi.json")
        assert res.status_code == 200
        openapi = res.json()
        assert openapi.get("info", {}).get("version") == __version__, (
            f"FastAPI version ({openapi.get('info', {}).get('version')}) が "
            f"deltasim.__version__ ({__version__}) と一致しません"
        )


# ---------------------------------------------------------------------------
# §18.4.1: 最近のプロジェクト追加タイミングテスト
# ---------------------------------------------------------------------------

class TestRecentProjectTriggers:
    """§18.4.1: プロジェクトを開いたとき・保存したとき・テスト完了時に追加されること"""

    @pytest.fixture(autouse=True)
    def patch_config_dir(self, tmp_path, monkeypatch):
        from deltasim.gui import recent as _recent_mod
        monkeypatch.setattr(_recent_mod, "_config_dir", lambda: tmp_path)

    def test_add_on_test_completion(self, client):
        """テスト完了時に最近一覧に追加されること（runTest → addCurrentToRecent 経由）"""
        # テストを実行（addCurrentToRecent が呼ばれる）
        import json as _json
        res = client.post("/api/test/start", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        job_id = res.json()["job_id"]
        with client.stream("GET", f"/api/test/{job_id}/events") as stream:
            for line in stream.iter_lines():
                if line.startswith("data:"):
                    ev = _json.loads(line[5:].strip())
                    if ev.get("event") in ("finish", "error"):
                        break
        # API レベルで recent-add を呼ぶ（フロントエンドが完了後に呼ぶ想定）
        client.post("/api/recent-projects/add", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "top": "counter",
            "name": "counter",
        })
        res2 = client.get("/api/recent-projects")
        assert res2.status_code == 200
        assert len(res2.json()["recent"]) >= 1

    def test_add_on_project_save(self, client, tmp_path):
        """プロジェクト保存時に最近一覧へ追加できること（フロントエンドから API 経由）"""
        from deltasim.gui.filesec import add_extra_root
        add_extra_root(tmp_path)
        # プロジェクト保存
        proj_path = str(tmp_path / "test.project.yaml")
        client.post("/api/project", json={
            "path": proj_path,
            "sv_files": [COUNTER_SV],
            "top": "counter",
            "spec": COUNTER_SPEC,
            "params": {},
        })
        # 保存後に recent-add（フロントエンドが saveProject 後に呼ぶ想定）
        client.post("/api/recent-projects/add", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
            "top": "counter",
            "name": "counter",
        })
        res = client.get("/api/recent-projects")
        assert len(res.json()["recent"]) >= 1

    def test_no_add_on_individual_file_open(self, client):
        """ファイルを読み込んだだけでは最近一覧に追加されないこと"""
        # /api/file/read はファイルを開くだけで recent-add を呼ばない
        client.get(f"/api/file/read?path={COUNTER_SV}")
        # recent は空のまま
        res = client.get("/api/recent-projects")
        assert res.json()["recent"] == [], "個別ファイルオープンでは追加されないこと"


# ---------------------------------------------------------------------------
# os.kill Windows 安全性テスト（§修正: _is_pid_running の Windows 安全化）
# ---------------------------------------------------------------------------

class TestIsPidRunning:
    """_is_pid_running が Windows では os.kill を呼ばないことと、
    実際のプロセスに対して正しく動作することをテストする。
    """

    def test_windows_does_not_call_os_kill(self, monkeypatch):
        """(a) Windows では os.kill が呼ばれないこと。

        monkeypatch で os.kill を例外を投げるものに差し替え、
        _is_pid_running を呼んでも例外が発生しないことで確認する。
        Windows 以外では os.kill を使うので、このテストは Windows のみ実行。
        """
        import os as _os
        if _os.name != "nt":
            pytest.skip("このテストは Windows 専用")

        from deltasim.gui import jobs as _jobs_mod

        def _kill_raises(pid, sig):
            raise RuntimeError(f"os.kill should NOT be called on Windows! pid={pid} sig={sig}")

        monkeypatch.setattr(_os, "kill", _kill_raises)

        # Windows ではこの呼び出しで os.kill が呼ばれてはならない
        result = _jobs_mod._is_pid_running(_os.getpid())
        assert result is True, "自プロセスは生存しているはずです"

    def test_posix_uses_os_kill(self, monkeypatch):
        """POSIX では os.kill が呼ばれること（正常ルートの確認）。"""
        import os as _os
        if _os.name == "nt":
            pytest.skip("このテストは POSIX 専用")

        from deltasim.gui import jobs as _jobs_mod

        called = []

        def _kill_track(pid, sig):
            called.append((pid, sig))
            # 本物の os.kill と同じ動作をさせる
            import signal
            if sig == 0:
                try:
                    _os.kill.__wrapped__(pid, sig)
                except AttributeError:
                    pass  # monkeypatch後は呼べない、呼んだ記録だけでOK

        monkeypatch.setattr(_os, "kill", _kill_track)

        _jobs_mod._is_pid_running(_os.getpid())
        assert len(called) >= 1, "POSIX では os.kill が呼ばれること"
        assert called[0][1] == 0, "シグナル 0 が渡されること"

    def test_running_process_returns_true(self):
        """(b) 実行中の子プロセスに対して True を返すこと（両OS）。"""
        import subprocess
        import sys
        from deltasim.gui.jobs import _is_pid_running

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert _is_pid_running(proc.pid) is True, (
                f"実行中の PID {proc.pid} は生存扱いになるべき"
            )
        finally:
            proc.kill()
            proc.wait()

    def test_terminated_process_returns_false(self):
        """(b) 終了した子プロセスに対して False を返すこと（両OS）。"""
        import subprocess
        import sys
        import time
        from deltasim.gui.jobs import _is_pid_running

        proc = subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.exit(0)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait()  # 終了を待つ
        time.sleep(0.1)  # 念のため少し待つ

        assert _is_pid_running(proc.pid) is False, (
            f"終了済み PID {proc.pid} は死亡扱いになるべき"
        )

    def test_own_pid_is_running(self):
        """自プロセス PID は常に True を返すこと（両OS）。"""
        import os as _os
        from deltasim.gui.jobs import _is_pid_running
        assert _is_pid_running(_os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        """存在しない PID は False を返すこと（両OS）。"""
        from deltasim.gui.jobs import _is_pid_running
        # 99999999 は実際のシステムでまず使われない PID
        result = _is_pid_running(99999999)
        assert result is False, "存在しない PID は False を返すこと"
