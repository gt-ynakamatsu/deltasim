"""GUI の E_LOOP_LIMIT / E_DELTA_LIMIT 明示表示テスト（PR #31）

テスト内容:
- /api/simulator/{id}/step が DeltaLimitError を 422 + code で返すこと
- /api/simulator/{id}/run_until が LoopLimitError を 422 + code で返すこと
- コンソール経路でも error_code が返ること
- 汎用 Exception は引き続き 500 であること
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

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
def sim_session(client):
    res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
    assert res.status_code == 200
    return res.json()["session_id"]


class TestLimitErrors:
    """§18 PR#31: 限界エラーが 422 + code で返ること"""

    def test_step_delta_limit_returns_422_with_code(self, client, sim_session):
        """step() が DeltaLimitError を投げたとき 422 + E_DELTA_LIMIT を返すこと"""
        from deltasim.sim.engine import DeltaLimitError

        with mock.patch.object(
            __import__("deltasim.api", fromlist=["Simulator"]).Simulator,
            "step",
            side_effect=DeltaLimitError(time_ns=100, max_delta=10_000),
        ):
            res = client.post(f"/api/simulator/{sim_session}/step", json={"n": 1})

        assert res.status_code == 422, f"DeltaLimitError は 422: {res.status_code}"
        data = res.json()
        assert data.get("code") == "E_DELTA_LIMIT", f"code = E_DELTA_LIMIT: {data}"

    def test_step_loop_limit_returns_422_with_code(self, client, sim_session):
        """step() が LoopLimitError を投げたとき 422 + E_LOOP_LIMIT を返すこと"""
        from deltasim.sim.engine import LoopLimitError

        with mock.patch.object(
            __import__("deltasim.api", fromlist=["Simulator"]).Simulator,
            "step",
            side_effect=LoopLimitError(time_ns=200, max_loop=1_000_000),
        ):
            res = client.post(f"/api/simulator/{sim_session}/step", json={"n": 1})

        assert res.status_code == 422, f"LoopLimitError は 422: {res.status_code}"
        data = res.json()
        assert data.get("code") == "E_LOOP_LIMIT", f"code = E_LOOP_LIMIT: {data}"

    def test_run_until_delta_limit_returns_422_with_code(self, client, sim_session):
        """run_until() が DeltaLimitError を投げたとき 422 + E_DELTA_LIMIT を返すこと"""
        from deltasim.sim.engine import DeltaLimitError

        with mock.patch.object(
            __import__("deltasim.api", fromlist=["Simulator"]).Simulator,
            "run_until",
            side_effect=DeltaLimitError(time_ns=50, max_delta=10_000),
        ):
            res = client.post(f"/api/simulator/{sim_session}/run_until", json={"time_ns": 100})

        assert res.status_code == 422
        assert res.json().get("code") == "E_DELTA_LIMIT"

    def test_run_until_loop_limit_returns_422_with_code(self, client, sim_session):
        """run_until() が LoopLimitError を投げたとき 422 + E_LOOP_LIMIT を返すこと"""
        from deltasim.sim.engine import LoopLimitError

        with mock.patch.object(
            __import__("deltasim.api", fromlist=["Simulator"]).Simulator,
            "run_until",
            side_effect=LoopLimitError(time_ns=50, max_loop=1_000_000),
        ):
            res = client.post(f"/api/simulator/{sim_session}/run_until", json={"time_ns": 100})

        assert res.status_code == 422
        assert res.json().get("code") == "E_LOOP_LIMIT"

    def test_step_generic_exception_returns_500(self, client, sim_session):
        """通常の例外は引き続き 500 を返すこと（回帰テスト）"""
        with mock.patch.object(
            __import__("deltasim.api", fromlist=["Simulator"]).Simulator,
            "step",
            side_effect=RuntimeError("generic error"),
        ):
            res = client.post(f"/api/simulator/{sim_session}/step", json={"n": 1})

        assert res.status_code == 500

    def test_limit_error_has_detail_message(self, client, sim_session):
        """422 レスポンスに detail メッセージが含まれること"""
        from deltasim.sim.engine import DeltaLimitError

        with mock.patch.object(
            __import__("deltasim.api", fromlist=["Simulator"]).Simulator,
            "step",
            side_effect=DeltaLimitError(time_ns=0, max_delta=10_000),
        ):
            res = client.post(f"/api/simulator/{sim_session}/step", json={"n": 1})

        assert res.status_code == 422
        data = res.json()
        assert "detail" in data
        assert len(data["detail"]) > 0


class TestConsoleLimitErrors:
    """§18 PR#31: コンソール経路でも限界エラーが error_code として返ること"""

    @pytest.fixture(scope="class")
    def console_session(self, client):
        res = client.post("/api/simulator", json={"sv_files": [COUNTER_SV], "top": "counter"})
        return res.json()["session_id"]

    def _exec_console(self, client, session_id, command, timeout=10.0):
        res = client.post(f"/api/console/{session_id}/exec/start", json={"command": command})
        assert res.status_code == 200
        job_id = res.json()["job_id"]

        result = {}
        with client.stream("GET", f"/api/console/{session_id}/exec/{job_id}/events",
                           timeout=timeout) as stream:
            for line in stream.iter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                if ev.get("event") in ("finish", "error"):
                    result = ev
                    break
        return result

    def test_console_delta_limit_has_error_code(self, client, console_session, monkeypatch):
        """コンソール run コマンドで DeltaLimitError → error_code=E_DELTA_LIMIT"""
        from deltasim.sim.engine import DeltaLimitError

        # interp.SimSession._advance_to_ns が DeltaLimitError を投げるようにモック
        original_eval = None

        def _raise_delta(*args, **kwargs):
            raise DeltaLimitError(time_ns=10, max_delta=10_000)

        import deltasim.interp.session as _sess_mod
        with mock.patch.object(_sess_mod.SimSession, "_advance_to_ns", side_effect=_raise_delta):
            result = self._exec_console(client, console_session, "run 10")

        # error_code またはメッセージに E_DELTA_LIMIT が含まれること
        output_text = " ".join(result.get("output", []))
        assert result.get("error_code") == "E_DELTA_LIMIT" or "E_DELTA_LIMIT" in output_text, (
            f"E_DELTA_LIMIT が返ること: {result}"
        )

    def test_console_loop_limit_has_error_code(self, client, console_session, monkeypatch):
        """コンソール run コマンドで LoopLimitError → error_code=E_LOOP_LIMIT"""
        from deltasim.sim.engine import LoopLimitError

        import deltasim.interp.session as _sess_mod
        with mock.patch.object(_sess_mod.SimSession, "_advance_to_ns",
                               side_effect=LoopLimitError(time_ns=10, max_loop=1_000_000)):
            result = self._exec_console(client, console_session, "run 10")

        output_text = " ".join(result.get("output", []))
        assert result.get("error_code") == "E_LOOP_LIMIT" or "E_LOOP_LIMIT" in output_text, (
            f"E_LOOP_LIMIT が返ること: {result}"
        )
