"""GUI LLM セッション管理（§18.3）

セキュリティ設計:
  - suggest_command が返した Suggestion をサーバーが UUID で保管する
  - 実行は suggestion_id のみ受け付け、任意のコマンド文字列は実行しない
  - 提案は MAX_SUGGESTIONS 件まで保管し古いものは削除
  - §18.5.1 (f): project_dir も suggestion_id に紐づけて保管する
    クライアントからの project_dir 入力は使わず、サーバー側の記録を使用
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Optional

MAX_SUGGESTIONS = 50  # 最大保管件数

_suggestions: OrderedDict = OrderedDict()  # suggestion_id -> Suggestion
_suggestion_meta: OrderedDict = OrderedDict()  # suggestion_id -> {project_dir}
_lock = threading.Lock()


def store_suggestion(sug, project_dir: Optional[str] = None) -> str:
    """Suggestion と project_dir を保管して suggestion_id を返す。

    §18.5.1 (f): project_dir はサーバー側で保管し、クライアント入力は使わない。
    """
    sid = str(uuid.uuid4())
    with _lock:
        _suggestions[sid] = sug
        _suggestion_meta[sid] = {"project_dir": project_dir}
        while len(_suggestions) > MAX_SUGGESTIONS:
            oldest = next(iter(_suggestions))
            _suggestions.pop(oldest, None)
            _suggestion_meta.pop(oldest, None)
    return sid


def get_suggestion(suggestion_id: str) -> Optional[object]:
    """suggestion_id に対応する Suggestion を返す（なければ None）。"""
    return _suggestions.get(suggestion_id)


def get_suggestion_project_dir(suggestion_id: str) -> Optional[str]:
    """§18.5.1 (f): suggestion_id に紐づく project_dir を返す。"""
    meta = _suggestion_meta.get(suggestion_id, {})
    return meta.get("project_dir")


def clear_suggestions() -> None:
    """テスト用: 全件削除。"""
    with _lock:
        _suggestions.clear()
        _suggestion_meta.clear()
