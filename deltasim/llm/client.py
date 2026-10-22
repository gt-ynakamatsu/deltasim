"""deltasim/llm/client.py — §18.3 LLM HTTP クライアント

urllib のみで ollama と openai 互換 API を呼ぶ。
新しい依存ライブラリは追加しない。
"""
from __future__ import annotations

import json as _json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

from deltasim.llm.config import LLMConfig


# ---------------------------------------------------------------------------
# エラー
# ---------------------------------------------------------------------------

class LLMConnectError(Exception):
    """接続失敗・タイムアウト"""
    code = "E_LLM_CONNECT"


class LLMResponseError(Exception):
    """LLM が正しい応答を返さなかった"""
    code = "E_LLM_RESPONSE"


# ---------------------------------------------------------------------------
# クライアント型（モック差し替え用インタフェース）
# ---------------------------------------------------------------------------

LLMCallable = Callable[[str, LLMConfig], str]
"""LLM 呼び出しシグネチャ: (prompt, config) -> response_text"""


# ---------------------------------------------------------------------------
# ollama / openai 形式の実装
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, cfg: LLMConfig) -> str:
    """ollama の /api/generate を呼ぶ。"""
    url = cfg.endpoint.rstrip("/") + "/api/generate"
    payload = {
        "model":  cfg.model,
        "prompt": prompt,
        "stream": False,
    }
    return _http_post(url, payload, cfg.timeout_s, "response")


def _call_openai(prompt: str, cfg: LLMConfig) -> str:
    """OpenAI /v1/chat/completions 互換 API を呼ぶ。"""
    url = cfg.endpoint.rstrip("/") + "/v1/chat/completions"
    payload = {
        "model":    cfg.model,
        "messages": [{"role": "user", "content": prompt}],
    }
    data = _http_post_raw(url, payload, cfg.timeout_s)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError(f"予期しない応答形式: {exc}") from exc


def _http_post(url: str, payload: dict, timeout: int, key: str) -> str:
    """JSON POST して response[key] を返す。"""
    raw = _http_post_raw(url, payload, timeout)
    if key not in raw:
        raise LLMResponseError(f"応答に '{key}' がありません: {list(raw.keys())}")
    return str(raw[key])


def _http_post_raw(url: str, payload: dict, timeout: int) -> dict:
    body = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise LLMConnectError(
            f"LLM への接続に失敗しました ({url}): {exc.reason}"
        ) from exc
    except TimeoutError as exc:
        raise LLMConnectError(
            f"LLM がタイムアウトしました ({timeout}s)"
        ) from exc
    except Exception as exc:
        raise LLMConnectError(
            f"LLM 通信エラー: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# 公開エントリポイント
# ---------------------------------------------------------------------------

def call_llm(prompt: str, cfg: LLMConfig,
             llm_fn: Optional[LLMCallable] = None) -> str:
    """LLM にプロンプトを送り、応答テキストを返す。

    Args:
        prompt: 送信するプロンプト文字列
        cfg: LLM 設定
        llm_fn: モック差し替え用。None なら cfg.api に応じた実装を使う。

    Returns:
        LLM の応答テキスト

    Raises:
        LLMConnectError: 接続失敗 / タイムアウト
        LLMResponseError: 予期しない応答形式
    """
    if llm_fn is not None:
        return llm_fn(prompt, cfg)
    if cfg.api == "openai":
        return _call_openai(prompt, cfg)
    return _call_ollama(prompt, cfg)
