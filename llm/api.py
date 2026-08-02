import json
import os
import time
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, Timeout, RequestException
from urllib3.util.retry import Retry


VALID_API_STYLES = {"auto", "openai", "anthropic"}


def resolve_model_api_style(
    provider_config: Dict[str, Any],
    model_id: str,
) -> str:
    """Resolve the wire protocol for a provider/model combination."""
    model_styles = provider_config.get("model_api_styles") or {}
    if not isinstance(model_styles, dict):
        raise ValueError("model_api_styles must be an object mapping model IDs to API styles.")

    api_style = model_styles.get(model_id, provider_config.get("api_style", "auto"))
    normalized = str(api_style or "auto").strip().lower()
    if normalized not in VALID_API_STYLES:
        supported = ", ".join(sorted(VALID_API_STYLES))
        raise ValueError(f"Unsupported API style '{api_style}'. Supported styles: {supported}")
    return normalized


class LLMClient:
    """Universal client for various LLM API endpoints with enhanced error handling."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_id: str = "deepseek-v4-flash",
        base_url: str = "https://opencode.ai/zen/go/v1",
        request_timeout: int = 60,
        provider: str = "opencode-go",
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        thinking_enabled: bool = False,
        display_thinking: bool = False,
        api_style: str = "auto",
        model_base_urls: Optional[Dict[str, str]] = None
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self.provider = provider
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.thinking_enabled = thinking_enabled
        self.display_thinking = display_thinking
        self.api_style = str(api_style or "auto").strip().lower()
        if self.api_style not in VALID_API_STYLES:
            supported = ", ".join(sorted(VALID_API_STYLES))
            raise ValueError(
                f"Unsupported API style '{api_style}'. Supported styles: {supported}"
            )
        self.model_base_urls = model_base_urls or {}
        
        # Use model-specific base URL if available for this model
        if model_id in self.model_base_urls:
            self.base_url = self.model_base_urls[model_id].rstrip("/")
        else:
            self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        # Detect Anthropic-compatible endpoint (Minimax & other IB provider endpoints)
        if self.api_style == "anthropic":
            self.anthropic_compatible = True
        elif self.api_style == "openai":
            self.anthropic_compatible = False
        else:
            self.anthropic_compatible = (
                provider == "anthropic" or "/anthropic" in self.base_url
            )
        
        if not self.api_key:
            raise ValueError(f"{provider.upper()}_API_KEY must be provided.")
        
        # Set headers based on provider
        self.headers = {
            "Content-Type": "application/json",
        }
        
        if self.anthropic_compatible:
            # Use x-api-key and Anthropic-style headers
            self.headers["x-api-key"] = self.api_key
            # Some Anthropic-compatible providers require an explicit version header
            self.headers["anthropic-version"] = "2023-06-01"
        elif provider == "anthropic":
            # Anthropic provider should also use x-api-key and anthopic-version header
            self.headers["x-api-key"] = self.api_key
            self.headers["anthropic-version"] = "2023-06-01"
        elif provider in ("glm", "zai") and not self.anthropic_compatible:
            self.headers["Authorization"] = f"Bearer {self.api_key}"
        elif provider == "openrouter":
            self.headers["Authorization"] = f"Bearer {self.api_key}"
            self.headers["HTTP-Referer"] = "https://your-site.com"
            # OpenRouter requires ASCII-safe header values; keep title simple to avoid encoding issues
            self.headers["X-Title"] = "Intelligent QA Assistant"
        elif provider == "minimax":
            # MiniMax uses x-api-key for Anthropic-compatible endpoint
            if self.anthropic_compatible:
                self.headers["x-api-key"] = self.api_key
                self.headers["anthropic-version"] = "2023-06-01"
            else:
                self.headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            # Default to Bearer token
            self.headers["Authorization"] = f"Bearer {self.api_key}"

        # Configure session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            status_forcelist=[429, 500, 502, 503, 504],
            backoff_factor=backoff_factor,
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    @staticmethod
    def _convert_to_anthropic_messages(
        messages: List[Dict[str, Any]],
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Extract the system prompt and convert content to Anthropic blocks."""
        system_message = None
        converted = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                if isinstance(content, str):
                    system_message = content
                continue

            if isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            elif isinstance(content, list):
                blocks = []
                for item in content:
                    if not isinstance(item, dict):
                        blocks.append({"type": "text", "text": str(item)})
                        continue
                    if item.get("type") != "image_url":
                        blocks.append(item)
                        continue
                    image_url = item.get("image_url") or {}
                    url = image_url.get("url", "")
                    parts = url.split(";base64,", 1) if url.startswith("data:") else []
                    if len(parts) == 2:
                        blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": parts[0].replace("data:", ""),
                                "data": parts[1],
                            },
                        })
            else:
                blocks = [{"type": "text", "text": str(content)}]
            converted.append({"role": role, "content": blocks})
        return system_message, converted

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 5000,
        temperature: float = 0.7,
        extra_messages: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Chat method for OpenAI-compatible APIs with enhanced error handling."""
        endpoint = f"{self.base_url}/chat/completions"
        if self.anthropic_compatible:
            endpoint = f"{self.base_url}/messages"

        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]
        if extra_messages:
            messages.extend(extra_messages)

        # Handle images for supported vision models
        # We enable this for known vision-capable models or if explicitly requested
        vision_keywords = ["grok", "gpt-4", "claude", "gemini", "glm-4v", "glm-4.5v", "claude-4.5-haiku", "vision", "minimax"]
        is_vision_model = any(k in self.model_id.lower() for k in vision_keywords)
        
        if images and is_vision_model:
             content_list = [{"type": "text", "text": user_prompt}]
             for img in images:
                 b64 = img.get("base64", "")
                 if "," in b64:
                     b64 = b64.split(",")[1]
                 mime = img.get("mime_type", "image/jpeg")
                 detail = img.get("detail", "auto")  # Support detail level: low, high, auto
                 content_list.append({
                     "type": "image_url",
                     "image_url": {
                         "url": f"data:{mime};base64,{b64}",
                         "detail": detail
                     }
                 })
             messages.append({"role": "user", "content": content_list})
        else:
             # For non-vision models, always send the original user prompt
             # The system prompt should contain information about images and any vision metadata
             messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # GLM/Zai-specific adjustments
        if self.provider in ("glm", "zai") and not self.anthropic_compatible:
            payload["stream"] = False

        # For Anthropic-compatible endpoints, convert messages to the expected block format
        if self.anthropic_compatible:
            system_msg, anthro_msgs = self._convert_to_anthropic_messages(messages)
            payload["messages"] = anthro_msgs
            if system_msg:
                payload["system"] = system_msg
            if self.thinking_enabled:
                payload["thinking"] = {"type": "enabled", "budget_tokens": 10000}

        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    endpoint,
                    headers=self.headers,
                    json=payload,
                    timeout=self.request_timeout,
                )
                response.raise_for_status()
                break
                
            except ConnectionError as exc:
                last_error = exc
                if "Connection aborted" in str(exc) or "RemoteDisconnected" in str(exc):
                    error_msg = f"连接被远程服务器断开: {str(exc)}"
                else:
                    error_msg = f"网络连接错误: {str(exc)}"
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                    
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    try:
                        print(f"网络连接失败，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1}): {error_msg}")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    time.sleep(wait_time)
                else:
                    return {"error": f"经过 {self.max_retries + 1} 次重试后仍然失败: {error_msg}"}
                    
            except Timeout as exc:
                last_error = exc
                error_msg = f"请求超时 ({self.request_timeout}秒): {str(exc)}"
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    try:
                        print(f"请求超时，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1})")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    time.sleep(wait_time)
                else:
                    return {"error": f"经过 {self.max_retries + 1} 次重试后仍然超时: {error_msg}"}
                    
            except RequestException as exc:
                last_error = exc
                error_msg = f"请求错误: {str(exc)}"
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    try:
                        print(f"请求失败，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1}): {error_msg}")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    time.sleep(wait_time)
                else:
                    return {"error": f"经过 {self.max_retries + 1} 次重试后仍然失败: {error_msg}"}
        else:
            return {"error": f"请求最终失败: {str(last_error)}"}

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            return {"error": f"响应JSON解析失败: {str(exc)}".encode('utf-8', errors='replace').decode('utf-8')}

        # Helper to coerce different response shapes to plain text
        def _coerce_content(value: Any) -> str:
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, list):
                pieces = []
                for item in value:
                    if isinstance(item, str):
                        pieces.append(item)
                    elif isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            pieces.append(text)
                return "\n".join(piece for piece in pieces if piece).strip()
            if isinstance(value, dict):
                text = value.get("text") or value.get("content")
                if isinstance(text, str):
                    return text.strip()
            return ""

        content = ""
        thinking_content = ""
        try:
            # Anthropic-style response handling
            if self.anthropic_compatible:
                # Try to find content blocks
                content_blocks = None
                if isinstance(data, dict):
                    # Minimax may return message.content
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
                            else:
                                # Fallback for other block types
                                text = block.get("text") or block.get("content")
                                if isinstance(text, str):
                                    text_pieces.append(text)
                    
                    thinking_content = "\n".join(thinking_pieces).strip()
                    content = "\n".join(text_pieces).strip()
                    
                    # If display_thinking is enabled, prepend thinking to content
                    if self.display_thinking and thinking_content:
                        content = f"[思考过程]\n{thinking_content}\n\n[回答]\n{content}"
                    
                    # fallback to previous parsing
                    if content:
                        pass
            # Standard OpenAI-like response handling if not set
            choices = data.get("choices", [])
            if choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                # Chat schema
                message = first.get("message") or {}
                content = _coerce_content(message.get("content"))
                # Fallback to text-based schema
                if not content:
                    content = _coerce_content(first.get("text"))
                # Provider specific handling when standard fields are absent
                if not content:
                    if self.provider == "minimax":
                        # Minimax responses may return a messages array or output_text field
                        messages = first.get("messages") or []
                        for msg in reversed(messages):
                            if isinstance(msg, dict) and msg.get("role") == "assistant":
                                candidate = _coerce_content(msg.get("content"))
                                if candidate:
                                    content = candidate
                                    break
                        if not content:
                            output_text = first.get("output_text") or data.get("output_text")
                            content = _coerce_content(output_text)
                    elif self.provider == "openrouter":
                        # OpenRouter occasionally nests content inside a "delta" key
                        delta = first.get("delta") or {}
                        if isinstance(delta, dict):
                            content = _coerce_content(delta.get("content"))
        except Exception as exc:
            try:
                print(f"解析响应内容时出错: {exc}")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            return {"error": f"解析响应内容时出错: {str(exc)}".encode('utf-8', errors='replace').decode('utf-8')}

        if not content:
            finish_reason = None
            try:
                finish_reason = data.get("choices", [{}])[0].get("finish_reason")
            except Exception:
                pass
            return {
                "content": "",
                "warning": "Empty content returned. Possible causes: wrong endpoint for model, content filter, or max_tokens too small.",
                "finish_reason": finish_reason,
                "raw": data,
            }

        return {"content": content, "raw": data}

    def chat_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 5000,
        temperature: float = 0.7,
        extra_messages: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Chat method for OpenAI-compatible APIs with enhanced error handling."""
        endpoint = f"{self.base_url}/chat/completions"
        if self.anthropic_compatible:
            endpoint = f"{self.base_url}/messages"

        # Build messages array
        messages = [{"role": "system", "content": system_prompt}]
        if extra_messages:
            messages.extend(extra_messages)
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }

        if self.anthropic_compatible:
            system_msg, anthro_msgs = self._convert_to_anthropic_messages(messages)
            payload["messages"] = anthro_msgs
            if system_msg:
                payload["system"] = system_msg
            if self.thinking_enabled:
                payload["thinking"] = {"type": "enabled", "budget_tokens": 10000}

        last_error = None
        
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    endpoint,
                    headers=self.headers,
                    json=payload,
                    timeout=self.request_timeout,
                    stream=True,
                )
                response.raise_for_status()
                break
                
            except ConnectionError as exc:
                last_error = exc
                if "Connection aborted" in str(exc) or "RemoteDisconnected" in str(exc):
                    error_msg = f"连接被远程服务器断开: {str(exc)}"
                else:
                    error_msg = f"网络连接错误: {str(exc)}"
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                    
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    try:
                        print(f"网络连接失败，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1}): {error_msg}")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    time.sleep(wait_time)
                else:
                    yield f"Error: 经过 {self.max_retries + 1} 次重试后仍然失败: {error_msg}"
                    return
                    
            except Timeout as exc:
                last_error = exc
                error_msg = f"请求超时 ({self.request_timeout}秒): {str(exc)}"
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    try:
                        print(f"请求超时，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1})")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    time.sleep(wait_time)
                else:
                    yield f"Error: 经过 {self.max_retries + 1} 次重试后仍然超时: {error_msg}"
                    return
                    
            except RequestException as exc:
                last_error = exc
                error_msg = f"请求错误: {str(exc)}"
                error_msg = error_msg.encode('utf-8', errors='replace').decode('utf-8')
                
                if attempt < self.max_retries:
                    wait_time = self.backoff_factor * (2 ** attempt)
                    try:
                        print(f"请求失败，{wait_time}秒后重试 (尝试 {attempt + 1}/{self.max_retries + 1}): {error_msg}")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                    time.sleep(wait_time)
                else:
                    yield f"Error: 经过 {self.max_retries + 1} 次重试后仍然失败: {error_msg}"
                    return
        else:
            yield f"Error: 请求最终失败: {str(last_error)}"
            return

        try:
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8')
                    if decoded_line.startswith('data: '):
                        json_str = decoded_line[len('data: '):]
                        if json_str.strip() == '[DONE]':
                            break
                        try:
                            chunk = json.loads(json_str)
                            if self.anthropic_compatible:
                                # Minimax/Athropic style streaming: look for content deltas
                                # chunk may have 'content', 'type', or nested message
                                # Example: {"type":"content_block_delta", "delta": {"type": "text_delta", "text": "..."}}
                                delta = chunk.get("delta") or chunk.get("delta", {})
                                # Try to find text fields in the delta
                                text = None
                                if isinstance(chunk, dict):
                                    if chunk.get("type") == "content_block_delta":
                                        delta = chunk.get("delta") or {}
                                        if isinstance(delta, dict):
                                            text = delta.get("text") or delta.get("thinking") or delta.get("content")
                                    else:
                                        # nested structure used by other implementations
                                        msg = chunk.get("message") or {}
                                        inner = msg.get("delta") or {}
                                        text = inner.get("text") if isinstance(inner, dict) else None
                                if text:
                                    yield text
                                continue
                            else:
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"Error: Failed to process stream: {str(e)}"
