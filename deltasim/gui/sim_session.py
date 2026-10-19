"""対話デバッグ用 Simulator セッション管理（仕様書 §8）。

設計:
  - Simulator インスタンスを UUID キーで保持
  - 操作は REST POST（WebSocket を使わない）
  - 状態変化はポーリング or SSE（POST操作の返り値に snapshot を含む）
  - step/run_until は同期実行（ブロッキング）→ asyncio.to_thread でラップ
  - 波形遅延取得に get_signal_changes を活用

未対応（仕様書との差異）:
  - Simulator.force() の NBA 抑制精度: docs/api.md §未決事項 #5
  - 階層シグナルパス: フラット展開のみ（未決事項 #6）
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Dict, List, Optional

from deltasim.result import CancelToken


# ---------------------------------------------------------------------------
# セッション
# ---------------------------------------------------------------------------

class SimSession:
    """1 つの Simulator インスタンスとそのメタデータ。"""

    def __init__(self, sim, session_id: str):
        self.session_id = session_id
        self.sim = sim
        self.cancel_token: Optional[CancelToken] = None

    def snapshot_dict(self) -> dict:
        snap = self.sim.snapshot()
        return {
            "session_id": self.session_id,
            "time_ns": self.sim.time_ns,
            "cycle": self.sim.cycle,
            "signals": {
                s["name"]: s["value"]
                for s in self.sim.list_signals()
            },
        }

    def signals_list(self) -> List[dict]:
        return self.sim.list_signals()

    def breakpoints_list(self) -> List[dict]:
        return self.sim.list_breakpoints()


# ---------------------------------------------------------------------------
# グローバルセッションレジストリ
# ---------------------------------------------------------------------------

_sessions: Dict[str, SimSession] = {}


def create_session(sv_files: List[str], top: Optional[str] = None,
                   params: Optional[dict] = None,
                   half_period: int = 5, reset_cycles: int = 10) -> SimSession:
    """新しい Simulator セッションを作成して返す。"""
    import deltasim.api as ds

    sim = ds.Simulator(
        [str(f) for f in sv_files],
        top=top,
        params=params or {},
        half_period=half_period,
        reset_cycles=reset_cycles,
        keep_changes=True,
    )
    session_id = str(uuid.uuid4())
    sess = SimSession(sim, session_id)
    _sessions[session_id] = sess
    return sess


def get_session(session_id: str) -> Optional[SimSession]:
    return _sessions.get(session_id)


def delete_session(session_id: str) -> bool:
    return _sessions.pop(session_id, None) is not None


def list_sessions() -> List[dict]:
    return [
        {"session_id": sid, "time_ns": s.sim.time_ns, "cycle": s.sim.cycle}
        for sid, s in _sessions.items()
    ]


# ---------------------------------------------------------------------------
# 信号変化取得（§2.6 get_signal_changes を使用）
# ---------------------------------------------------------------------------

def get_signal_changes(
    session_id: str,
    signal: str,
    start_ns: int = 0,
    end_ns: Optional[int] = None,
) -> List[dict]:
    """Simulator の keep_changes DB から信号変化を取得する（VCDファイル不要）。"""
    sess = get_session(session_id)
    if sess is None:
        return []
    changes = sess.sim.get_signal_changes(signal, start_ns, end_ns)
    return [{"time_ns": c.time_ns, "value": c.value} for c in changes]


def get_all_signal_changes(
    session_id: str,
    start_ns: int = 0,
    end_ns: Optional[int] = None,
) -> dict:
    """全信号の変化を取得して波形ビューア形式で返す。"""
    sess = get_session(session_id)
    if sess is None:
        return {"signals": {}, "end_time": 0}

    sigs = sess.sim.list_signals()
    signals_data = {}
    end_time = sess.sim.time_ns

    for s in sigs:
        name = s["name"]
        changes = sess.sim.get_signal_changes(name, start_ns, end_ns)
        signals_data[name] = {
            "width": s["width"],
            "changes": [[c.time_ns, c.value] for c in changes],
        }

    return {
        "timescale": "1ns",
        "signals": signals_data,
        "end_time": end_time,
    }
