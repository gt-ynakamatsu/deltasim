"""GUI API のテスト — FastAPI TestClient を使ったエンドポイント検証"""

from __future__ import annotations

import os
import tempfile
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
