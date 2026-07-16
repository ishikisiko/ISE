"""LangChain-compatible LLM adapters for multiple providers."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Type, Union

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, SecretStr

from llm.google_api import (
    build_google_endpoint,
    build_google_payload,
    extract_google_content,
)
from llm.api import resolve_model_api_style
from utils.config_validation import configured_value


def _load_config() -> Dict[str, Any]:
    """Load configuration from config.json."""
    config_path = os.getenv("NLP_CONFIG_PATH", "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


class UniversalChatModel(BaseChatModel):
    """A universal chat model for native Gemini and compatible chat APIs.
    
    Supports OpenCode Go, OpenAI, Anthropic, Google, GLM, ZAI, OpenRouter,
    MiniMax, and other compatible APIs.
    """
    
    # Pydantic model fields
    api_key: SecretStr = Field(default=None, description="API key for the provider")
    model_name: str = Field(default="gpt-3.5-turbo", alias="model")
    base_url: str = Field(default="https://api.openai.com/v1")
    provider: str = Field(default="openai")
    api_style: str = Field(default="auto")
    request_timeout: int = Field(default=60)
    max_retries: int = Field(default=3)
    backoff_factor: float = Field(default=1.0)
    thinking_enabled: bool = Field(default=False)
    display_thinking: bool = Field(default=False)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=5000)
    
    # Internal state
    _session: Any = None
    _anthropic_compatible: bool = False

    class Config:
        """Pydantic configuration."""
        arbitrary_types_allowed = True
        populate_by_name = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._setup_session()
        if self.api_style == "anthropic":
            self._anthropic_compatible = True
        elif self.api_style == "openai":
            self._anthropic_compatible = False
        else:
            self._anthropic_compatible = (
                self.provider == "anthropic" or "/anthropic" in self.base_url
            )

    def _setup_session(self) -> None:
        """Set up requests session with retry strategy."""
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        self._session = requests.Session()
        retry_strategy = Retry(
            total=self.max_retries,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=self.backoff_factor,
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    @property
    def _llm_type(self) -> str:
        return f"universal-{self.provider}"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "provider": self.provider,
            "base_url": self.base_url,
        }

    def _get_headers(self) -> Dict[str, str]:
        """Get headers based on provider."""
        headers = {"Content-Type": "application/json"}
        api_key = self.api_key.get_secret_value() if self.api_key else ""
        
        if self.provider == "google":
            headers["x-goog-api-key"] = api_key
        elif self._anthropic_compatible or self.provider == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        elif self.provider == "openrouter":
            headers["Authorization"] = f"Bearer {api_key}"
            headers["HTTP-Referer"] = "https://your-site.com"
            headers["X-Title"] = "Intelligent QA Assistant"
        else:
            headers["Authorization"] = f"Bearer {api_key}"
        
        return headers

    def _convert_messages(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        """Convert LangChain messages to API format."""
        converted = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                converted.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                # Handle multimodal content
                if isinstance(msg.content, list):
                    content_list = []
                    for item in msg.content:
                        if isinstance(item, dict):
                            if item.get("type") == "text":
                                content_list.append({"type": "text", "text": item.get("text", "")})
                            elif item.get("type") == "image_url":
                                content_list.append(item)
                        elif isinstance(item, str):
                            content_list.append({"type": "text", "text": item})
                    converted.append({"role": "user", "content": content_list})
                else:
                    converted.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                converted.append({"role": "assistant", "content": msg.content})
        return converted

    def _convert_to_anthropic_format(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Convert messages to Anthropic format, extracting system message."""
        system_msg = None
        anthropic_msgs = []
        
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            
            if role == "system":
                if isinstance(content, str):
                    system_msg = content
                continue
            
            # Convert content to block format
            if isinstance(content, str):
                content_blocks = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                # Convert OpenAI-style image_url to Anthropic-style image format
                content_blocks = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            content_blocks.append(item)
                        elif item.get("type") == "image_url":
                            # Convert from OpenAI format to Anthropic format
                            image_url = item.get("image_url", {})
                            url = image_url.get("url", "")
                            if url.startswith("data:"):
                                # Extract media type and base64 data
                                parts = url.split(";base64,")
                                if len(parts) == 2:
                                    media_type = parts[0].replace("data:", "")
                                    base64_data = parts[1]
                                    content_blocks.append({
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": media_type,
                                            "data": base64_data
                                        }
                                    })
                            else:
                                # Fallback for non-base64 URLs
                                content_blocks.append(item)
                        else:
                            content_blocks.append(item)
                    else:
                        content_blocks.append({"type": "text", "text": str(item)})
            else:
                content_blocks = [{"type": "text", "text": str(content)}]
            
            anthropic_msgs.append({"role": role, "content": content_blocks})
        
        return system_msg, anthropic_msgs

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate a response from the model."""
        import requests
        
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        if self.provider == "google":
            endpoint = build_google_endpoint(self.base_url, self.model_name)
        elif self._anthropic_compatible:
            endpoint = f"{self.base_url.rstrip('/')}/messages"
        
        converted_messages = self._convert_messages(messages)
        
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        payload: Dict[str, Any]

        if self.provider == "google":
            payload = build_google_payload(
                converted_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
            )
        elif self._anthropic_compatible:
            payload = {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            system_msg, anthropic_msgs = self._convert_to_anthropic_format(converted_messages)
            payload["messages"] = anthropic_msgs
            if system_msg:
                payload["system"] = system_msg
            if self.thinking_enabled:
                payload["thinking"] = {"type": "enabled", "budget_tokens": 10000}
        else:
            payload = {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            payload["messages"] = converted_messages
            if self.provider in ("glm", "zai"):
                payload["stream"] = False

        if stop and self.provider != "google":
            if self._anthropic_compatible:
                payload["stop_sequences"] = stop
            else:
                payload["stop"] = stop
        
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.post(
                    endpoint,
                    headers=self._get_headers(),
                    json=payload,
                    timeout=self.request_timeout,
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    time.sleep(wait_time)
                else:
                    raise ValueError(f"Request failed after {self.max_retries + 1} attempts: {exc}")
        
        data = response.json()
        content = self._extract_content(data)
        
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=content))],
            llm_output={"raw": data},
        )

    def _extract_content(self, data: Dict[str, Any]) -> str:
        """Extract content from API response."""
        if self.provider == "google":
            return extract_google_content(data)

        # Anthropic-style response
        if self._anthropic_compatible:
            content_blocks = None
            if isinstance(data, dict):
                msg = data.get("message") or data
                content_blocks = msg.get("content") if isinstance(msg, dict) else None
            
            if content_blocks:
                text_pieces = []
                thinking_pieces = []
                for block in content_blocks:
                    if isinstance(block, dict):
                        block_type = block.get("type")
                        if block_type == "thinking":
                            thinking_text = block.get("thinking")
                            if isinstance(thinking_text, str):
                                thinking_pieces.append(thinking_text)
                        elif block_type == "text":
                            text = block.get("text")
                            if isinstance(text, str):
                                text_pieces.append(text)
                
                thinking_content = "\n".join(thinking_pieces).strip()
                content = "\n".join(text_pieces).strip()
                
                if self.display_thinking and thinking_content:
                    content = f"[思考过程]\n{thinking_content}\n\n[回答]\n{content}"
                
                if content:
                    return content
        
        # OpenAI-style response
        choices = data.get("choices", [])
        if choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                pieces = []
                for item in content:
                    if isinstance(item, str):
                        pieces.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            pieces.append(text)
                return "\n".join(pieces).strip()
            # Fallback to text field
            text = first.get("text")
            if isinstance(text, str):
                return text.strip()
        
        return ""

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Stream a response from the model."""
        import requests
        
        endpoint = f"{self.base_url.rstrip('/')}/chat/completions"
        if self.provider == "google":
            endpoint = build_google_endpoint(self.base_url, self.model_name, stream=True)
        elif self._anthropic_compatible:
            endpoint = f"{self.base_url.rstrip('/')}/messages"
        
        converted_messages = self._convert_messages(messages)
        
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        temperature = kwargs.get("temperature", self.temperature)
        payload: Dict[str, Any]

        if self.provider == "google":
            payload = build_google_payload(
                converted_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop,
            )
        elif self._anthropic_compatible:
            payload = {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
            system_msg, anthropic_msgs = self._convert_to_anthropic_format(converted_messages)
            payload["messages"] = anthropic_msgs
            if system_msg:
                payload["system"] = system_msg
        else:
            payload = {
                "model": self.model_name,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
            }
            payload["messages"] = converted_messages

        if stop and self.provider != "google":
            if self._anthropic_compatible:
                payload["stop_sequences"] = stop
            else:
                payload["stop"] = stop
        
        response = self._session.post(
            endpoint,
            headers=self._get_headers(),
            json=payload,
            timeout=self.request_timeout,
            stream=True,
        )
        response.raise_for_status()
        
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('data: '):
                    json_str = decoded_line[len('data: '):]
                    if json_str.strip() == '[DONE]':
                        break
                    try:
                        chunk = json.loads(json_str)
                        content = self._extract_stream_content(chunk)
                        if content:
                            yield ChatGenerationChunk(
                                message=AIMessageChunk(content=content)
                            )
                    except json.JSONDecodeError:
                        continue

    def _extract_stream_content(self, chunk: Dict[str, Any]) -> str:
        """Extract content from a streaming chunk."""
        if self.provider == "google":
            return extract_google_content(chunk, strip=False)
        if self._anthropic_compatible:
            if chunk.get("type") == "content_block_delta":
                delta = chunk.get("delta") or {}
                return delta.get("text") or delta.get("thinking") or ""
            msg = chunk.get("message") or {}
            inner = msg.get("delta") or {}
            return inner.get("text", "") if isinstance(inner, dict) else ""
        else:
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            return delta.get("content", "")


def _resolve_provider_and_model(
    provider: Optional[str],
    model: Optional[str],
    config: Dict[str, Any],
) -> tuple[str, Optional[str]]:
    requested_value = provider or config.get("LLM_PROVIDER", "minimax")
    if not isinstance(requested_value, str) or not requested_value.strip():
        raise ValueError("A valid LLM provider or model must be configured.")
    requested = requested_value.strip()
    providers = config.get("providers", {})
    if requested in providers:
        return requested, model

    if model is None:
        for provider_name, provider_config in providers.items():
            available_models = provider_config.get("available_models") or []
            if requested == provider_config.get("model") or requested in available_models:
                return provider_name, requested

        if "/" in requested:
            provider_name = requested.split("/", 1)[0]
            if provider_name in providers:
                return provider_name, requested

    return requested, model


def create_chat_model(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> UniversalChatModel:
    """Factory function to create a chat model from configuration.
    
    Args:
        provider: Provider name, such as opencode-go, openai, google, or minimax
        model: Model ID/name
        config: Configuration dictionary (loaded from config.json if not provided)
        **kwargs: Additional parameters passed to UniversalChatModel
    
    Returns:
        Configured UniversalChatModel instance
    """
    if config is None:
        config = _load_config()
    
    provider, model = _resolve_provider_and_model(provider, model, config)
    
    # Get provider configuration
    provider_config = config.get("providers", {}).get(provider, {})
    if not provider_config:
        raise ValueError(f"Provider '{provider}' not found in configuration")
    
    # Normalize base URL for ZAI
    base_url = provider_config.get("base_url", "")
    if provider == "zai" and base_url and "/anthropic" not in base_url:
        if "/coding/paas" in base_url:
            base_url = base_url.replace("/coding/paas", "/paas")
        if not base_url.endswith("/v4") and base_url.endswith("/paas"):
            base_url = base_url.rstrip("/") + "/v4"
    
    # Get LLM settings
    llm_settings = config.get("llm_settings", {})
    
    # Get thinking configuration
    thinking_config = provider_config.get("thinking", {})
    
    overrides = dict(kwargs)
    api_key = configured_value(
        overrides.pop("api_key", provider_config.get("api_key"))
    )
    if not api_key:
        raise ValueError(f"Provider '{provider}' requires a configured API key.")

    final_model = model or provider_config.get("model", "")

    # Build model parameters
    model_params = {
        "api_key": SecretStr(api_key),
        "model_name": final_model,
        "base_url": base_url or provider_config.get("base_url", "https://api.openai.com/v1"),
        "provider": provider,
        "api_style": resolve_model_api_style(provider_config, final_model),
        "request_timeout": provider_config.get("request_timeout", llm_settings.get("default_timeout", 60)),
        "max_retries": provider_config.get("max_retries", llm_settings.get("max_retries", 3)),
        "backoff_factor": provider_config.get("backoff_factor", llm_settings.get("backoff_factor", 2.0)),
        "thinking_enabled": thinking_config.get("enabled", False) if isinstance(thinking_config, dict) else False,
        "display_thinking": thinking_config.get("display_in_response", False) if isinstance(thinking_config, dict) else False,
    }
    
    # Override with kwargs
    model_params.update(overrides)
    
    return UniversalChatModel(**model_params)


def create_role_chat_model(
    config: Dict[str, Any],
    role_config: Dict[str, Any],
) -> UniversalChatModel:
    """Create a chat model for a configured orchestration role."""

    provider = role_config.get("provider")
    model = role_config.get("model")
    requested_provider = provider or model
    model_override = model if provider else None

    overrides = {
        key: role_config[key]
        for key in (
            "api_key",
            "base_url",
            "request_timeout",
            "max_retries",
            "backoff_factor",
            "temperature",
            "max_tokens",
            "api_style",
        )
        if role_config.get(key) is not None
    }
    return create_chat_model(
        provider=requested_provider,
        model=model_override,
        config=config,
        **overrides,
    )


# Convenience aliases for specific providers
def create_openai_chat(**kwargs: Any) -> UniversalChatModel:
    """Create an OpenAI chat model."""
    return create_chat_model(provider="openai", **kwargs)


def create_anthropic_chat(**kwargs: Any) -> UniversalChatModel:
    """Create an Anthropic chat model."""
    return create_chat_model(provider="anthropic", **kwargs)


def create_zai_chat(**kwargs: Any) -> UniversalChatModel:
    """Create a ZAI (Zhipu) chat model."""
    return create_chat_model(provider="zai", **kwargs)


def create_openrouter_chat(**kwargs: Any) -> UniversalChatModel:
    """Create an OpenRouter chat model."""
    return create_chat_model(provider="openrouter", **kwargs)


# Legacy compatibility wrapper
class LangChainLLMWrapper:
    """Wrapper to provide legacy LLMClient interface using LangChain model."""
    
    def __init__(self, chat_model: UniversalChatModel) -> None:
        self.chat_model = chat_model
        self.provider = chat_model.provider
        self.model_id = chat_model.model_name
    
    def _is_vision_model(self) -> bool:
        """Check if current model supports vision."""
        vision_keywords = ["grok", "gpt-4", "claude", "gemini", "glm-4v", "glm-4.5v", "claude-4.5-haiku", "vision", "minimax"]
        is_vision = any(k in self.model_id.lower() for k in vision_keywords)
        print(f"[LangChainLLMWrapper] Checking if model '{self.model_id}' supports vision: {is_vision}")
        return is_vision
    
    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 5000,
        temperature: float = 0.7,
        extra_messages: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Legacy chat interface."""
        messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        
        if extra_messages:
            for msg in extra_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))
        
        # Handle images
        if images and self._is_vision_model():
            content_list = [{"type": "text", "text": user_prompt}]
            for img in images:
                b64 = img.get("base64", "")
                if "," in b64:
                    b64 = b64.split(",")[1]
                mime = img.get("mime_type", "image/jpeg")
                detail = img.get("detail", "auto")
                content_list.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{b64}",
                        "detail": detail
                    }
                })
            messages.append(HumanMessage(content=content_list))
        else:
            if images:
                print(f"[LangChainLLMWrapper] Warning: Images provided but model '{self.model_id}' may not support vision. Sending text only.")
                # Add a note to the user prompt about the image
                user_prompt = f"[注意：您上传了图片，但所选模型 '{self.model_id}' 不支持视觉功能。请描述图片内容以便我更好地回答。]\n\n{user_prompt}"
            messages.append(HumanMessage(content=user_prompt))
        
        try:
            result = self.chat_model.invoke(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return {
                "content": result.content,
                "raw": result.response_metadata if hasattr(result, "response_metadata") else None,
            }
        except Exception as exc:
            return {"error": str(exc)}
    
    def chat_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 5000,
        temperature: float = 0.7,
        extra_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Iterator[str]:
        """Legacy streaming chat interface."""
        messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
        
        if extra_messages:
            for msg in extra_messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))
        
        messages.append(HumanMessage(content=user_prompt))
        
        try:
            for chunk in self.chat_model.stream(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ):
                if chunk.content:
                    yield chunk.content
        except Exception as exc:
            yield f"Error: {exc}"
