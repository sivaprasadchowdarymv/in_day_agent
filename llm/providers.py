"""Optional free-tier LLM providers (Google Gemini, Groq, OpenRouter).

LLMs are used ONLY to (a) describe chart screenshots as structured JSON and
(b) rephrase deterministic evidence into readable explanations. They never
produce prices, indicator values, costs or quantities that the app relies on.
API keys are read from environment variables and sent in headers – never in
URLs, logs or the database.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

import requests

from config.logging_setup import get_logger, redact
from config.settings import get_secret

log = get_logger("llm")

DEFAULT_MODELS = {
    "gemini": {"text": "gemini-2.5-flash", "vision": "gemini-2.5-flash", "env": "GEMINI_API_KEY"},
    "groq": {"text": "llama-3.3-70b-versatile", "vision": "meta-llama/llama-4-scout-17b-16e-instruct",
             "env": "GROQ_API_KEY"},
    "openrouter": {"text": "", "vision": "", "env": "OPENROUTER_API_KEY"},
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


class LLMProvider:
    name = "none"

    def available(self) -> bool:
        return False

    def generate(self, prompt: str, system: str = "", images: list[tuple[bytes, str]] | None = None,
                 json_mode: bool = False, max_tokens: int = 1200) -> LLMResponse:
        raise LLMError("No LLM provider configured")


class NoLLM(LLMProvider):
    pass


class GeminiProvider(LLMProvider):
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model: str = "", vision_model: str = "", timeout: int = 60):
        self.key = get_secret("GEMINI_API_KEY")
        self.model = model or DEFAULT_MODELS["gemini"]["text"]
        self.vision_model = vision_model or DEFAULT_MODELS["gemini"]["vision"]
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.key)

    def generate(self, prompt, system="", images=None, json_mode=False, max_tokens=1200) -> LLMResponse:
        if not self.key:
            raise LLMError("GEMINI_API_KEY not set")
        model = self.vision_model if images else self.model
        parts = [{"text": prompt}]
        for data, mime in images or []:
            parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}})
        body: dict = {"contents": [{"role": "user", "parts": parts}],
                      "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        try:
            r = requests.post(self.URL.format(model=model), json=body, timeout=self.timeout,
                              headers={"x-goog-api-key": self.key, "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise LLMError(f"Gemini HTTP {r.status_code}: {redact(r.text[:300])}")
        try:
            data = r.json()
            text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Unexpected Gemini response format") from exc
        return LLMResponse(text, self.name, model)


class OpenAICompatibleProvider(LLMProvider):
    """Groq and OpenRouter both expose an OpenAI-compatible chat API."""

    BASE = {"groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions"}

    def __init__(self, name: str, model: str = "", vision_model: str = "", timeout: int = 60):
        self.name = name
        self.key = get_secret(DEFAULT_MODELS[name]["env"])
        self.model = model or DEFAULT_MODELS[name]["text"]
        self.vision_model = vision_model or DEFAULT_MODELS[name]["vision"] or self.model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.key and self.model)

    def generate(self, prompt, system="", images=None, json_mode=False, max_tokens=1200) -> LLMResponse:
        if not self.available():
            raise LLMError(f"{self.name}: API key or model not configured")
        model = self.vision_model if images else self.model
        content: list | str
        if images:
            content = [{"type": "text", "text": prompt}] + [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(d).decode()}"}}
                for d, mime in images]
        else:
            content = prompt
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        body: dict = {"model": model, "messages": msgs, "temperature": 0.1, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(self.BASE[self.name], json=body, timeout=self.timeout,
                              headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        except requests.RequestException as exc:
            raise LLMError(f"{self.name} request failed: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise LLMError(f"{self.name} HTTP {r.status_code}: {redact(r.text[:300])}")
        try:
            text = r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected {self.name} response format") from exc
        return LLMResponse(text, self.name, model)


def make_llm(provider: str, model: str = "", vision_model: str = "") -> LLMProvider:
    p = (provider or "none").lower()
    if p == "gemini":
        return GeminiProvider(model, vision_model)
    if p in ("groq", "openrouter"):
        return OpenAICompatibleProvider(p, model, vision_model)
    return NoLLM()


def extract_json(text: str) -> dict:
    """Parse the first JSON object in an LLM reply (handles ```json fences)."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if m:
        t = m.group(1)
    else:
        a, b = t.find("{"), t.rfind("}")
        if a == -1 or b == -1:
            raise ValueError("No JSON object found")
        t = t[a: b + 1]
    return json.loads(t)
