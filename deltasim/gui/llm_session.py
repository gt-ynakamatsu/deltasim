"""GUI LLM セッション管理（§18.3）

セキュリティ設計:
  - suggest_command が返した Suggestion をサーバーが UUID で保管する
  - 実行は suggestion_id のみ受け付け、任意のコマンド文字列は実行しない
  - 提案は MAX_SUGGESTIONS 件まで保管し古いものは削除
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Optional

MAX_SUGGESTIONS = 50  # 最大保管件数

_suggestions: OrderedDict = OrderedDict()  # suggestion_id → Suggestion
_lock = threading.Lock()


def store_suggestion(sug) -> str:
    """Suggestion を保管して suggestion_id を返す。"""
    sid = str(uuid.uuid4())
    with _lock:
        _suggestions[sid] = sug
        # 最大件数を超えたら古いものを削除
        while len(_suggestions) > MAX_SUGGESTIONS:
            _suggestions.popitem(last=False)
    return sid


def get_suggestion(suggestion_id: str) -> Optional[object]:
    """suggestion_id に対応する Suggestion を返す（なければ None）。"""
    return _suggestions.get(suggestion_id)


def clear_suggestions() -> None:
    """テスト用: 全件削除。"""
    with _lock:
        _suggestions.clear()
