
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMConfig:
    api_base: str
    api_key: str
    model: str


_config_cache: LLMConfig | None = None


def _load_from_file() -> LLMConfig | None:
    import json

    cfg_path = DATA_DIR / "llm_config.json"
    if not cfg_path.exists():
        return None
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return LLMConfig(
        api_base=data.get("api_base", "https://api.openai.com"),
        api_key=data.get("api_key", ""),
        model=data.get("model", "gpt-4o-mini"),
    )


def get_llm_config() -> LLMConfig:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    cfg = _load_from_file()
    if cfg is None:
        cfg = LLMConfig(
            api_base=os.getenv("SKIMCLASS_LLM_BASE", "https://api.openai.com"),
            api_key=os.getenv("SKIMCLASS_LLM_KEY", ""),
            model=os.getenv("SKIMCLASS_LLM_MODEL", "gpt-4o-mini"),
        )
    _config_cache = cfg
    return cfg


def set_llm_config(cfg: LLMConfig) -> None:
    global _config_cache
    _config_cache = cfg
    import json

    cfg_path = DATA_DIR / "llm_config.json"
    cfg_path.write_text(
        json.dumps(
            {"api_base": cfg.api_base, "api_key": cfg.api_key, "model": cfg.model},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
