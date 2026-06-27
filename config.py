"""API and model configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Provider selection: "openai", "deepseek", "moonshot", "qwen"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")

API_KEYS = {
    "openai": os.getenv("OPENAI_API_KEY"),
    "deepseek": os.getenv("DEEPSEEK_API_KEY"),
    "moonshot": os.getenv("MOONSHOT_API_KEY"),
    "qwen": os.getenv("QWEN_API_KEY"),
}

MODELS = {
    "openai": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "deepseek": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    "moonshot": os.getenv("MOONSHOT_MODEL", "moonshot-v1-8k"),
    "qwen": os.getenv("QWEN_MODEL", "qwen-plus"),
}

BASE_URLS = {
    "openai": os.getenv("OPENAI_BASE_URL"),
    "deepseek": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    "moonshot": os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn"),
    "qwen": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
}


def get_api_key():
    key = API_KEYS.get(LLM_PROVIDER)
    if not key:
        raise ValueError(
            f"Missing API key for provider '{LLM_PROVIDER}'. "
            f"Set {LLM_PROVIDER.upper()}_API_KEY in environment or .env file."
        )
    return key


def get_model():
    return MODELS[LLM_PROVIDER]


def get_base_url():
    return BASE_URLS[LLM_PROVIDER]
