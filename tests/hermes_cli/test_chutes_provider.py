"""Tests for Chutes provider support."""

import sys
import types

import pytest

# Ensure dotenv doesn't interfere
if "dotenv" not in sys.modules:
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = fake_dotenv

from hermes_cli.auth import (
    PROVIDER_REGISTRY,
    resolve_provider,
    get_api_key_provider_status,
    resolve_api_key_provider_credentials,
)
from hermes_cli.models import CANONICAL_PROVIDERS, _PROVIDER_MODELS, _PROVIDER_LABELS, _PROVIDER_ALIASES, normalize_provider


class TestChutesProviderRegistry:
    def test_chutes_in_registry(self):
        assert "chutes" in PROVIDER_REGISTRY

    def test_chutes_config(self):
        pconfig = PROVIDER_REGISTRY["chutes"]
        assert pconfig.name == "Chutes"
        assert pconfig.auth_type == "api_key"
        assert pconfig.inference_base_url == "https://llm.chutes.ai/v1"
        assert pconfig.api_key_env_vars == ("CHUTES_API_KEY",)


class TestChutesAliases:
    @pytest.mark.parametrize("alias", ["chutes", "chutes-ai"])
    def test_alias_resolves(self, alias, monkeypatch):
        monkeypatch.setenv("CHUTES_API_KEY", "cpk_test")
        assert resolve_provider(alias) == "chutes"

    def test_models_py_alias(self):
        assert _PROVIDER_ALIASES.get("chutes-ai") == "chutes"

    def test_normalize_provider_models_py(self):
        assert normalize_provider("chutes-ai") == "chutes"

    def test_normalize_provider_providers_py(self):
        from hermes_cli.providers import normalize_provider as normalize_provider_providers
        assert normalize_provider_providers("chutes-ai") == "chutes"


class TestChutesAutoDetection:
    def test_auto_detects_chutes_api_key(self, monkeypatch):
        for var in (
            "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN",
            "GOOGLE_API_KEY", "GEMINI_API_KEY", "GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY",
            "KIMI_API_KEY", "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY", "DEEPSEEK_API_KEY",
            "XAI_API_KEY", "AI_GATEWAY_API_KEY", "KILOCODE_API_KEY", "DASHSCOPE_API_KEY",
            "OPENCODE_ZEN_API_KEY", "OPENCODE_GO_API_KEY", "HF_TOKEN", "XIAOMI_API_KEY",
            "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("CHUTES_API_KEY", "cpk_test")
        assert resolve_provider("auto") == "chutes"


class TestChutesCredentials:
    def test_status_configured(self, monkeypatch):
        monkeypatch.setenv("CHUTES_API_KEY", "cpk_test")
        status = get_api_key_provider_status("chutes")
        assert status["configured"]

    def test_resolve_credentials(self, monkeypatch):
        monkeypatch.setenv("CHUTES_API_KEY", "cpk_test")
        creds = resolve_api_key_provider_credentials("chutes")
        assert creds["provider"] == "chutes"
        assert creds["api_key"] == "cpk_test"
        assert creds["base_url"] == "https://llm.chutes.ai/v1"

    def test_runtime_chutes(self, monkeypatch):
        monkeypatch.setenv("CHUTES_API_KEY", "cpk_test")
        from hermes_cli.runtime_provider import resolve_runtime_provider
        result = resolve_runtime_provider(requested="chutes")
        assert result["provider"] == "chutes"
        assert result["api_mode"] == "chat_completions"
        assert result["api_key"] == "cpk_test"
        assert result["base_url"] == "https://llm.chutes.ai/v1"


class TestChutesModelCatalog:
    def test_provider_in_canonical_list(self):
        assert any(p.slug == "chutes" for p in CANONICAL_PROVIDERS)

    def test_provider_label(self):
        assert _PROVIDER_LABELS["chutes"] == "Chutes"

    def test_static_model_list_fallback(self):
        assert "chutes" in _PROVIDER_MODELS
        models = _PROVIDER_MODELS["chutes"]
        assert "default" in models
        assert "default:latency" in models
        assert "default:throughput" in models


class TestChutesMetadataHints:
    def test_provider_prefixes_include_chutes(self):
        from agent.model_metadata import _PROVIDER_PREFIXES
        assert "chutes" in _PROVIDER_PREFIXES

    def test_base_url_hints_include_chutes(self):
        from agent.model_metadata import _URL_TO_PROVIDER
        assert _URL_TO_PROVIDER["llm.chutes.ai"] == "chutes"
