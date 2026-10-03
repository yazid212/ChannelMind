from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from .utils import logger
from config import (
    LLM_PROVIDER,
    LLM_MODELS,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    OPENROUTER_API_KEY,
    GROQ_API_KEY,
    GROK_API_KEY,
    NVIDIA_API_KEY
)


class LLMFactory:
    @staticmethod
    def create(provider: Optional[str] = None) -> BaseChatModel:
        provider = provider or LLM_PROVIDER
        model_name = LLM_MODELS.get(provider)

        if not model_name:
            raise ValueError(f"Unknown provider: {provider}")

        logger.info(f"LLM: {provider} / {model_name}")

        if provider == "openrouter":
            if not OPENROUTER_API_KEY:
                raise ValueError("OPENROUTER_API_KEY not set in .env")
            return ChatOpenAI(
                model=model_name,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                openai_api_key=OPENROUTER_API_KEY,
                openai_api_base="https://openrouter.ai/api/v1"
            )

        elif provider == "groq":
            if not GROQ_API_KEY:
                raise ValueError("GROQ_API_KEY not set in .env")
            return ChatOpenAI(
                model=model_name,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                openai_api_key=GROQ_API_KEY,
                openai_api_base="https://api.groq.com/openai/v1"
            )

        elif provider == "grok":
            if not GROK_API_KEY:
                raise ValueError("GROK_API_KEY not set in .env")
            return ChatOpenAI(
                model=model_name,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                openai_api_key=GROK_API_KEY,
                openai_api_base="https://api.x.ai/v1"
            )

        elif provider == "nvidia":
            if not NVIDIA_API_KEY:
                raise ValueError("NVIDIA_API_KEY not set in .env")
            return ChatOpenAI(
                model=model_name,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                openai_api_key=NVIDIA_API_KEY,
                openai_api_base="https://integrate.api.nvidia.com/v1"
            )

        raise ValueError(f"Unsupported provider: {provider}")
