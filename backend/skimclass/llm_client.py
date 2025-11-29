
from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx

from .config import get_llm_config


class LLMError(RuntimeError):
    pass


def chat(messages: List[Dict[str, Any]], max_tokens: int = 1024, temperature: float = 0.2) -> str:
    cfg = get_llm_config()
    if not cfg.api_key:
        raise LLMError("大模型 API Key 尚未配置，请在设置中填写。")

    url = cfg.api_base.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=120)
    except httpx.RequestError as exc:
        raise LLMError(f"无法连接到大模型服务：{exc}") from exc

    if resp.status_code >= 400:
        raise LLMError(f"大模型服务返回错误 {resp.status_code}: {resp.text}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"大模型响应格式异常：{json.dumps(data)[:2000]}") from exc
