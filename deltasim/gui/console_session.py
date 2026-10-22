"""GUI コンソールセッション管理（§18.4 コンソール入力欄）

デバッグパネルの Simulator セッション（sim_session.py）と同一の api.Simulator
インスタンスを共有し、interp.SimSession を通じて Questa 風コマンドを実行する。

アーキテクチャ:
  - session_id（デバッグパネルと共通）→ interp.SimSession をレジストリで管理
  - コンソールコマンドは jobs.py の非同期ジョブとして実行（run -all 等の長時間実行対応）
  - 出力はコールバック経由でリアルタイムに SSE へ流す
  - quit/exit は quit シグナルとしてジョブに伝え、サーバーは止めない
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

_interp_sessions: Dict[str, object] = {}  # session_id → interp.SimSession
_lock = threading.Lock()


def get_or_create(session_id: str, simulator) -> object:
    """session_id に対応する interp.SimSession を返す（なければ作成）。

    simulator は api.Simulator インスタンス（sim_session.SimSession.sim）。
    """
    with _lock:
        if session_id not in _interp_sessions:
            from deltasim.interp.session import SimSession as InterpSession
            cs = InterpSession(simulator)
            _interp_sessions[session_id] = cs
        return _interp_sessions[session_id]


def get(session_id: str) -> Optional[object]:
    """既存の interp.SimSession を返す（なければ None）。"""
    return _interp_sessions.get(session_id)


def delete(session_id: str) -> None:
    """session_id に対応する interp.SimSession を削除する。"""
    with _lock:
        _interp_sessions.pop(session_id, None)


def list_sessions() -> List[str]:
    return list(_interp_sessions.keys())
