r"""deltasim/llm/config.py — §18.3 simconfig.yaml 読み込み

探す順番:
  1. カレントディレクトリ ./simconfig.yaml
  2. ユーザー設定ディレクトリ
     Windows: %APPDATA%\deltasim\simconfig.yaml
     その他:  $XDG_CONFIG_HOME/deltasim/simconfig.yaml
              （未設定なら ~/.config/deltasim/simconfig.yaml）

設定が無いか llm 節が無い場合は None を返す。
ファイルが壊れていても例外を投げず None を返す（他の機能に影響しない）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    endpoint: str  = "http://127.0.0.1:11434"
    model: str     = ""
    timeout_s: int = 60
    api: str       = "ollama"   # "ollama" | "openai"

    def is_valid(self) -> bool:
        """model が設定されていれば有効とみなす。"""
        return bool(self.model.strip())


# ---------------------------------------------------------------------------
# 設定ファイル検索
# ---------------------------------------------------------------------------

def _user_config_dir() -> Path:
    """プラットフォーム別ユーザー設定ディレクトリを返す。"""
    if os.name == "nt":
        # Windows: %APPDATA%\deltasim
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "deltasim"
    # Linux / macOS: $XDG_CONFIG_HOME/deltasim or ~/.config/deltasim
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "deltasim"
    return Path.home() / ".config" / "deltasim"


def _search_paths() -> list[Path]:
    return [
        Path.cwd() / "simconfig.yaml",
        _user_config_dir() / "simconfig.yaml",
    ]


def load_llm_config(config_path: Optional[str | Path] = None) -> Optional[LLMConfig]:
    """simconfig.yaml を探して LLMConfig を返す。

    設定ファイルが無いか llm 節が無い場合は None を返す。
    ファイルが壊れていても None を返す（他の機能に影響しない）。

    Args:
        config_path: 明示的なパス（テスト用）。None なら自動検索。

    Returns:
        LLMConfig or None
    """
    import yaml as _yaml

    if config_path is not None:
        paths = [Path(config_path)]
    else:
        paths = _search_paths()

    for path in paths:
        if not path.exists():
            continue
        try:
            data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            # 壊れていても他の機能に影響させない
            continue
        llm_section = data.get("llm")
        if not isinstance(llm_section, dict):
            continue
        cfg = LLMConfig(
            endpoint  = str(llm_section.get("endpoint", LLMConfig.endpoint)),
            model     = str(llm_section.get("model", LLMConfig.model)),
            timeout_s = int(llm_section.get("timeout_s", LLMConfig.timeout_s)),
            api       = str(llm_section.get("api", LLMConfig.api)).lower(),
        )
        return cfg
    return None
