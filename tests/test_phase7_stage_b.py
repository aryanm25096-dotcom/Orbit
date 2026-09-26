"""
tests/test_phase7_stage_b.py — Phase 7 Stage B Router & Telemetry Tests
========================================================================
Tests Phase 7:
  1. Stage B per-role model routing from routing.yaml (§4.3).
  2. Multi-provider adapter (Ollama vs. OpenRouter).
  3. OpenRouter API integration and fallback to Stage A.
  4. TelemetryCollector structured JSON reporting with phase durations and recovery loops (§4.8).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from config.router import (
    RoleModelConfig,
    call_openrouter,
    call_role_model,
    get_role_config,
    load_routing_config,
)
from telemetry.collector import TelemetryCollector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_routing_yaml(tmp_path: Path) -> Path:
    cfg = {
        "stage_a": {
            "default_model": "nemotron-3-ultra:cloud",
            "provider": "ollama",
            "base_url": "http://localhost:11434",
        },
        "stage_b": {
            "enabled": True,
            "roles": {
                "backend": {
                    "model": "deepseek-coder-v2:latest",
                    "provider": "ollama",
                    "base_url": "http://localhost:11434",
                },
                "frontend": {
                    "model": "mistralai/mistral-7b-instruct:free",
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "test-key-123",
                },
            },
        },
    }
    p = tmp_path / "routing.yaml"
    p.write_text(yaml.safe_dump(cfg))
    return p


# ---------------------------------------------------------------------------
# Router Tests
# ---------------------------------------------------------------------------

class TestStageBRouter:

    def test_get_role_config_stage_b_enabled(self, mock_routing_yaml: Path):
        be_cfg = get_role_config("backend", config_path=mock_routing_yaml)
        assert be_cfg.role == "backend"
        assert be_cfg.model == "deepseek-coder-v2:latest"
        assert be_cfg.provider == "ollama"

        fe_cfg = get_role_config("frontend", config_path=mock_routing_yaml)
        assert fe_cfg.role == "frontend"
        assert fe_cfg.model == "mistralai/mistral-7b-instruct:free"
        assert fe_cfg.provider == "openrouter"
        assert fe_cfg.api_key == "test-key-123"

    def test_get_role_config_fallback_to_stage_a(self, mock_routing_yaml: Path):
        # Database worker is not in Stage B overrides -> falls back to Stage A default
        db_cfg = get_role_config("database", config_path=mock_routing_yaml)
        assert db_cfg.role == "database"
        assert db_cfg.model == "nemotron-3-ultra:cloud"
        assert db_cfg.provider == "ollama"

    @patch("requests.post")
    def test_call_openrouter_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "def hello(): pass"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        content, tok_in, tok_out = call_openrouter(
            prompt="write function",
            system="system prompt",
            model="mistralai/mistral-7b-instruct:free",
            api_key="sk-or-test",
        )

        assert content == "def hello(): pass"
        assert tok_in == 12
        assert tok_out == 8

        # Verify Authorization header was sent
        args, kwargs = mock_post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-or-test"

    @patch("requests.post")
    def test_call_role_model_fallback_on_openrouter_failure(self, mock_post, mock_routing_yaml: Path):
        # OpenRouter call fails -> falls back to call_ollama
        mock_post.side_effect = ConnectionError("OpenRouter unreachable")

        with patch("config.router.call_ollama") as mock_ollama:
            mock_ollama.return_value = ("fallback response", 15, 20)

            content, tok_in, tok_out = call_role_model(
                role="frontend",
                prompt="test",
                system="test",
                config_path=mock_routing_yaml,
                fallback_to_stage_a=True,
            )

            assert content == "fallback response"
            assert tok_in == 15
            mock_ollama.assert_called_once()


# ---------------------------------------------------------------------------
# Telemetry Tests
# ---------------------------------------------------------------------------

class TestTelemetryCollector:

    def test_telemetry_records_full_lifecycle(self, tmp_path: Path):
        t = TelemetryCollector()
        t.record_llm_call(phase="architect", tokens_in=500, tokens_out=300, model="m1")
        t.record_tool_call(phase="worker", tool="write_file", success=True)
        t.record_recovery_loop(attempt=1, task_id="task-be-1", passing_tests=2)
        t.record_phase_duration(phase="scheduler", duration_sec=5.2)

        rep = t.report()
        assert rep["total_tokens_in"] == 500
        assert rep["total_tokens_out"] == 300
        assert rep["total_tokens"] == 800
        assert rep["total_tool_calls"] == 1
        assert rep["total_recovery_loops"] == 1
        assert rep["phase_durations"]["scheduler"] == 5.2

        save_file = tmp_path / "telemetry.json"
        t.save(save_file)
        assert save_file.exists()
        loaded = json.loads(save_file.read_text())
        assert loaded["total_tokens"] == 800
