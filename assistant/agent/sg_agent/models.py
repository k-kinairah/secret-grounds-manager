"""Build the Strands model for the configured provider.

Provider SDK imports are deferred so a missing key or an unreachable provider
only fails when /chat is used, not at import time, and so tests never touch them.
"""

from typing import Any

from strands.models import Model

from sg_agent.config import Settings


class ModelConfigError(Exception):
    """The configured provider is missing something it needs. Safe to show."""


def build_model(settings: Settings) -> Model:
    if not settings.model_id:
        raise ModelConfigError(
            f"No model configured: set SG_AGENT_MODEL_ID for provider {settings.provider!r}."
        )
    common: dict[str, Any] = {"model_id": settings.model_id}

    if settings.provider == "bedrock":
        from strands.models.bedrock import BedrockModel

        # Credentials come from the standard AWS chain (AWS_PROFILE, env keys, SSO, ...).
        config: dict[str, Any] = dict(common, max_tokens=settings.max_tokens)
        if settings.temperature is not None:
            config["temperature"] = settings.temperature
        return BedrockModel(region_name=settings.bedrock_region, **config)

    if settings.provider == "anthropic":
        from strands.models.anthropic import AnthropicModel

        if not settings.anthropic_api_key:
            raise ModelConfigError("ANTHROPIC_API_KEY is not set.")
        params = {"temperature": settings.temperature} if settings.temperature is not None else None
        return AnthropicModel(
            client_args={"api_key": settings.anthropic_api_key},
            max_tokens=settings.max_tokens,
            **({"params": params} if params else {}),
            **common,
        )

    if settings.provider == "openai":
        from strands.models.openai import OpenAIModel

        if not settings.openai_api_key:
            raise ModelConfigError("OPENAI_API_KEY is not set.")
        client_args: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            client_args["base_url"] = settings.openai_base_url
        params = {"max_tokens": settings.max_tokens}
        if settings.temperature is not None:
            params["temperature"] = settings.temperature
        return OpenAIModel(client_args=client_args, params=params, **common)

    if settings.provider == "ollama":
        from strands.models.ollama import OllamaModel

        config = dict(common, max_tokens=settings.max_tokens)
        if settings.temperature is not None:
            config["temperature"] = settings.temperature
        return OllamaModel(settings.ollama_host, **config)

    raise ModelConfigError(f"Unknown provider {settings.provider!r}.")
