"""OpenAI-compatible chat clients and offline fake model."""

import json
import re
from typing import Any, Protocol

from pydantic import BaseModel

from .config import AgentConfig


class ChatModel(Protocol):
    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False,
                 response_model: type[BaseModel] | None = None) -> str: ...


def strip_think_blocks(text: str) -> str:
    """Remove private/reasoning blocks emitted by some Qwen models."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL).strip()


def repair_json(text: str) -> str:
    """Extract a JSON object/array from common fenced or prefaced model output."""
    cleaned = strip_think_blocks(text).strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start = next((i for i, char in enumerate(cleaned) if char in "[{"), None)
    if start is None:
        raise ValueError("Model response contains no JSON object or array")
    opening = cleaned[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                candidate = cleaned[start:index + 1]
                json.loads(candidate)
                return candidate
    raise ValueError("Model response contains incomplete JSON")


class OpenAICompatibleModel:
    """Uses Ollama locally by default, or OpenRouter when explicitly configured."""

    def __init__(self, config: AgentConfig):
        self.config = config
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install kg-agent dependencies to use a real LLM") from exc
        self.client = OpenAI(base_url=config.llm_base_url, api_key=config.api_key,
                             timeout=config.request_timeout_seconds,
                             max_retries=config.max_retries)
        self.call_count = 0

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False,
                 response_model: type[BaseModel] | None = None) -> str:
        request: dict[str, Any] = {"model": self.config.llm_model, "messages": messages}
        if json_mode or response_model is not None:
            request["response_format"] = {"type": "json_object"}
        active_messages = list(messages)
        for attempt in range(self.config.max_retries + 1):
            request["messages"] = active_messages
            self.call_count += 1
            response = self.client.chat.completions.create(**request)
            content = strip_think_blocks(response.choices[0].message.content or "")
            if not json_mode and response_model is None:
                return content
            try:
                repaired = repair_json(content)
                if response_model is not None:
                    response_model.model_validate_json(repaired)
                return repaired
            except (ValueError, json.JSONDecodeError) as exc:
                if attempt >= self.config.max_retries:
                    raise ValueError("Model did not return valid JSON") from exc
                active_messages = [*messages, {"role": "user", "content":
                    "Your previous response was invalid JSON. Return only valid JSON "
                    "matching the requested schema."}]
        raise AssertionError("unreachable")


class FakeLLM:
    """Deterministic test double; no network or model download is required."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses or ['{"ok": true}'])
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False,
                 response_model: type[BaseModel] | None = None) -> str:
        self.calls.append({"messages": messages, "json_mode": json_mode,
                           "response_model": response_model})
        if not self.responses:
            raise RuntimeError("FakeLLM has no queued responses")
        answer = self.responses.pop(0)
        if response_model is not None:
            response_model.model_validate_json(repair_json(answer))
        return answer
