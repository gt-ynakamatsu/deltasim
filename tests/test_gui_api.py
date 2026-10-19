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
    with TestClient(app) as c:
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
        assert data["schema_version"] == 1
        assert "cases" in data
        assert "summary" in data
        assert data["summary"]["total"] >= 1
        # counter は全ケース PASS であること
        assert data["summary"]["pass"] >= 1
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
        """結果 JSON の構造が仕様書 §2 の契約に沿っていること"""
        res = client.post("/api/test", json={
            "sv_files": [COUNTER_SV],
            "spec": COUNTER_SPEC,
        })
        assert res.status_code == 200
        data = res.json()

        # トップレベル
        assert "schema_version" in data
        assert "module" in data
        assert "cases" in data
        assert "summary" in data

        # ケース構造
        for case in data["cases"]:
            assert "name" in case
            assert "status" in case
            assert case["status"] in ("PASS", "FAIL", "ERROR", "SKIP")
            assert "seed" in case
            assert "duration_sec" in case
            assert "failures" in case
            assert "log" in case

        # サマリー構造
        s = data["summary"]
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
    with TestClient(app) as c:
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
        with TestClient(app) as c:
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
        with TestClient(app) as c:
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
