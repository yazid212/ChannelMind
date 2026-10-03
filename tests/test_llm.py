from unittest.mock import patch

from src.llm import LLMFactory


def test_groq_provider_uses_groq_openai_compatible_endpoint():
    with (
        patch("src.llm.GROQ_API_KEY", "test-key"),
        patch("src.llm.ChatOpenAI") as chat_openai,
    ):
        model = LLMFactory.create("groq")

    assert model is chat_openai.return_value
    kwargs = chat_openai.call_args.kwargs
    assert kwargs["openai_api_base"] == "https://api.groq.com/openai/v1"
    assert kwargs["openai_api_key"] == "test-key"
    assert kwargs["max_tokens"] > 0
