"""Phase 2: provider adapters. Keys come from environment variables only and are
never printed, logged or stored. Endpoints are the providers' documented REST APIs
(checked 2026-10-25); model ids can be overridden with GRIDMEM_<NAME>_MODEL.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

Transport = Callable[[str, Dict[str, str], Dict], Dict]


class ProviderError(Exception):
    pass


class KeyNotSet(ProviderError):
    pass


def http_post_json(url: str, headers: Dict[str, str], body: Dict, timeout: int = 180) -> Dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise ProviderError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise ProviderError(f"network error: {e.reason}") from None
    except json.JSONDecodeError:
        raise ProviderError("response was not JSON") from None


class Adapter:
    name = "base"
    label = "base"
    env_key: Optional[str] = None
    default_model = ""

    def __init__(self, transport: Optional[Transport] = None, env: Optional[Dict[str, str]] = None):
        self.transport = transport or http_post_json
        self.env = os.environ if env is None else env

    @property
    def model(self) -> str:
        return self.env.get(f"GRIDMEM_{self.name.upper()}_MODEL", self.default_model)

    def key(self) -> str:
        k = self.env.get(self.env_key or "", "").strip() if self.env_key else ""
        if self.env_key and not k:
            raise KeyNotSet(f"{self.label}: skipped - {self.env_key} is not set "
                            f"(export {self.env_key}=... in your shell; gridmem never stores keys)")
        return k

    def available(self) -> bool:
        try:
            self.key()
            return True
        except KeyNotSet:
            return False

    def ask(self, system: str, user: str) -> str:
        url, headers, body = self.build_request(system, user)
        try:
            data = self.transport(url, headers, body)
            return self.parse(data)
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.label}: unexpected response shape ({type(e).__name__})") from None

    def build_request(self, system: str, user: str):
        raise NotImplementedError

    def parse(self, data: Dict) -> str:
        raise NotImplementedError


class OpenAICompatible(Adapter):
    base_url = ""

    def build_request(self, system, user):
        return (f"{self.base_url}/chat/completions", {"Authorization": f"Bearer {self.key()}"},
                {"model": self.model, "stream": False,
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})

    def parse(self, data):
        return data["choices"][0]["message"]["content"] or ""


class XAI(OpenAICompatible):
    name, label, env_key, default_model = "grok", "Grok (xAI)", "XAI_API_KEY", "grok-4.7"
    base_url = "https://api.x.ai/v1"


class OpenAI(OpenAICompatible):
    name, label, env_key, default_model = "chatgpt", "ChatGPT (OpenAI)", "OPENAI_API_KEY", "gpt-6-luna"
    base_url = "https://api.openai.com/v1"


class Anthropic(Adapter):
    name, label, env_key, default_model = "claude", "Claude (Anthropic)", "ANTHROPIC_API_KEY", "claude-sonnet-5-5"

    def build_request(self, system, user):
        return ("https://api.anthropic.com/v1/messages",
                {"x-api-key": self.key(), "anthropic-version": "2023-06-01"},
                {"model": self.model, "max_tokens": 4096, "system": system,
                 "messages": [{"role": "user", "content": user}]})

    def parse(self, data):
        return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")


class Gemini(Adapter):
    name, label, env_key, default_model = "gemini", "Gemini (Google)", "GEMINI_API_KEY", "gemini-3.8-flash"

    def build_request(self, system, user):
        return (f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                {"x-goog-api-key": self.key()},
                {"systemInstruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}]})

    def parse(self, data):
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])


class Ollama(Adapter):
    """Local models (e.g. Meta's Llama) through Ollama. No key; OLLAMA_HOST optional."""
    name, label, env_key, default_model = "ollama", "Ollama (local)", None, "llama3.1"

    def build_request(self, system, user):
        host = self.env.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if not host.startswith("http"):
            host = "http://" + host
        return (f"{host}/api/chat", {},
                {"model": self.model, "stream": False,
                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})

    def parse(self, data):
        return data["message"]["content"]


class Meta(Ollama):
    """Meta AI has no consumer API key flow here; 'meta' runs a Llama model via local Ollama."""
    name, label, default_model = "meta", "Meta Llama (via Ollama)", "llama3.1"


class Mock(Adapter):
    """For tests and dry runs. Returns canned replies; never touches the network."""
    name, label, env_key = "mock", "Mock", None

    def __init__(self, reply: str = "", name: str = "mock", fail: Optional[Exception] = None, **kw):
        super().__init__(**kw)
        self.reply, self.name, self.label, self.fail = reply, name, f"Mock({name})", fail
        self.calls: List = []

    def ask(self, system, user):
        self.calls.append((system, user))
        if self.fail:
            raise self.fail
        return self.reply


REGISTRY = {c.name: c for c in (XAI, Gemini, Anthropic, OpenAI, Ollama, Meta)}
ALIASES = {"xai": "grok", "google": "gemini", "anthropic": "claude", "openai": "chatgpt",
           "gpt": "chatgpt", "llama": "meta"}


def get_adapter(name: str, **kw) -> Adapter:
    n = ALIASES.get(name.strip().lower(), name.strip().lower())
    if n not in REGISTRY:
        raise ProviderError(f"unknown model '{name}'. Known: {', '.join(REGISTRY)}")
    return REGISTRY[n](**kw)
