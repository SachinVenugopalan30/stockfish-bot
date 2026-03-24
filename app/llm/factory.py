from app.llm.base import LLMProvider


def get_provider(provider: str, model: str = None, ollama_host: str = None) -> LLMProvider:
    """Return the configured LLM provider instance."""
    if provider == "claude":
        from app.llm.claude import ClaudeProvider
        return ClaudeProvider(model=model or "claude-sonnet-4-6")
    elif provider == "openai":
        from app.llm.openai_provider import OpenAIProvider
        return OpenAIProvider(model=model or "gpt-4o-mini")
    elif provider == "gemini":
        from app.llm.gemini import GeminiProvider
        return GeminiProvider(model=model or "gemini-2.0-flash")
    elif provider == "ollama":
        from app.llm.ollama import OllamaProvider
        return OllamaProvider(
            model=model or "llama3",
            host=ollama_host or "http://localhost:11434",
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider!r}. Choose from: claude, openai, gemini, ollama")
