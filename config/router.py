"""
config/router.py — Stage B Per-Role Model Routing & Multi-Provider Adapter (Phase 7)
=====================================================================================
PRD §4.3:
  "Per-Role Model Routing — staged, not all-at-once.
   Stage A: All roles run on one reliable model.
   Stage B: Per-role/per-domain model assignment via config, e.g. architect/reviewer
            on a stronger reasoning model, workers on domain-tuned models.
            Wire in an alternate provider (OpenRouter or similar) for one or two
            roles as a Stage B test."

Supported Providers:
  - 'ollama': local / cloud model via Ollama HTTP API (/api/chat or /api/generate)
  - 'openrouter': OpenRouter / OpenAI-compatible /chat/completions API
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
import yaml

from architect.architect import call_ollama

logger = logging.getLogger("orbit.router")

DEFAULT_CONFIG_PATH = Path("config/routing.yaml")


@dataclass
class RoleModelConfig:
    role: str
    model: str
    provider: str
    base_url: str
    api_key: str = ""


def load_routing_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load and parse routing.yaml, falling back to defaults if not found."""
    p = Path(config_path)
    if not p.exists():
        return {
            "stage_a": {
                "default_model": "nemotron-3-ultra:cloud",
                "provider": "ollama",
                "base_url": "http://localhost:11434",
            },
            "stage_b": {"enabled": False},
        }
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_role_config(role: str, config_path: Path = DEFAULT_CONFIG_PATH) -> RoleModelConfig:
    """
    Resolve the model configuration for *role*.
    If Stage B is enabled and specifies an override for *role*, returns that.
    Otherwise, returns the Stage A default.
    """
    cfg = load_routing_config(config_path)
    stage_a = cfg.get("stage_a", {})
    default_model = stage_a.get("default_model", "nemotron-3-ultra:cloud")
    default_provider = stage_a.get("provider", "ollama")
    default_base_url = stage_a.get("base_url", "http://localhost:11434")

    stage_b = cfg.get("stage_b", {})
    if stage_b.get("enabled", False):
        roles_cfg = stage_b.get("roles", {})
        if role in roles_cfg:
            rc = roles_cfg[role]
            return RoleModelConfig(
                role=role,
                model=rc.get("model", default_model),
                provider=rc.get("provider", default_provider),
                base_url=rc.get("base_url", default_base_url),
                api_key=rc.get("api_key", os.environ.get("OPENROUTER_API_KEY", "")),
            )

    return RoleModelConfig(
        role=role,
        model=default_model,
        provider=default_provider,
        base_url=default_base_url,
        api_key="",
    )


def call_openrouter(
    prompt: str,
    system: str,
    model: str,
    base_url: str = "https://openrouter.ai/api/v1",
    api_key: str = "",
    temperature: float = 0.0,
    timeout: int = 60,
) -> tuple[str, int, int]:
    """
    Call OpenRouter (or any OpenAI-compatible API) via /chat/completions.
    Returns (response_text, tokens_in, tokens_out).
    """
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise ValueError("OpenRouter API key required. Set OPENROUTER_API_KEY in environment or config.")

    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/orbit/orbit",
        "X-Title": "Orbit Harness",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }

    resp = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    tokens_in = usage.get("prompt_tokens", 0)
    tokens_out = usage.get("completion_tokens", 0)

    return content, tokens_in, tokens_out


def call_role_model(
    role: str,
    prompt: str,
    system: str,
    config_path: Path = DEFAULT_CONFIG_PATH,
    temperature: float = 0.0,
    fallback_to_stage_a: bool = True,
) -> tuple[str, int, int]:
    """
    Dispatch model call based on Stage A / Stage B configuration for *role*.
    """
    cfg = get_role_config(role, config_path)

    if cfg.provider == "openrouter":
        try:
            return call_openrouter(
                prompt=prompt,
                system=system,
                model=cfg.model,
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                temperature=temperature,
            )
        except Exception as exc:
            if fallback_to_stage_a:
                logger.warning(
                    f"OpenRouter call for role '{role}' failed ({exc}). "
                    "Falling back to Stage A Ollama model."
                )
                stage_a = load_routing_config(config_path).get("stage_a", {})
                fallback_model = stage_a.get("default_model", "nemotron-3-ultra:cloud")
                fallback_base_url = stage_a.get("base_url", "http://localhost:11434")
                return call_ollama(
                    prompt=prompt,
                    system=system,
                    model=fallback_model,
                    base_url=fallback_base_url,
                    temperature=temperature,
                )
            raise

    # Default provider: Ollama
    return call_ollama(
        prompt=prompt,
        system=system,
        model=cfg.model,
        base_url=cfg.base_url,
        temperature=temperature,
    )
