import pytest

from app.llm.claude import ClaudeProvider
from app.llm.factory import get_provider
from app.llm.gemini import GeminiProvider
from app.llm.ollama import OllamaProvider
from app.llm.openai_provider import OpenAIProvider


def test_factory_ollama():
    provider = get_provider("ollama", model="llama3", ollama_host="http://localhost:11434")
    assert isinstance(provider, OllamaProvider)
    assert provider.provider_name == "ollama"


def test_factory_claude():
    provider = get_provider("claude", model="claude-sonnet-4-6")
    assert isinstance(provider, ClaudeProvider)
    assert provider.provider_name == "claude"


def test_factory_openai():
    provider = get_provider("openai", model="gpt-4o-mini")
    assert isinstance(provider, OpenAIProvider)
    assert provider.provider_name == "openai"


def test_factory_gemini():
    provider = get_provider("gemini", model="gemini-2.0-flash")
    assert isinstance(provider, GeminiProvider)
    assert provider.provider_name == "gemini"


def test_factory_invalid():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_provider("unknown_provider")


def test_factory_default_models():
    ollama = get_provider("ollama")
    assert ollama.model == "llama3"
