"""Groq LLM access with resilience built in.

- Model fallback chain (GROQ_MODELS): if a model is rate-limited, overloaded or decommissioned we move on.
- Key rotation (GROQ_API_KEYS): every teammate's free key is a separate rate-limit bucket.
- Malformed tool calls (Groq returns HTTP 400 'tool_use_failed') are surfaced as ToolFormatError so the
  orchestrator can tell the model what went wrong and let it retry — a visible self-correction step.
"""
import itertools
import logging
import re
import threading
import time

from groq import (APIConnectionError, APIStatusError, APITimeoutError, BadRequestError, Groq, NotFoundError,
                  RateLimitError)

from app.config import settings
from app.fetch import is_simulated_down

log = logging.getLogger("khetdrishti.llm")


class LLMUnavailable(Exception):
    pass


class ToolFormatError(Exception):
    pass


THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def model_options(model: str) -> dict:
    """Per-family settings. Reasoning tokens count against max_tokens, so gpt-oss runs at low effort
    (otherwise it can spend the whole budget thinking and return empty content), and Qwen's reasoning is
    returned in a separate field instead of <think> tags inside the answer."""
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    if model.startswith("qwen/"):
        return {"reasoning_format": "parsed"}
    return {}


class LLMPool:
    def __init__(self):
        self._clients = [Groq(api_key=k, max_retries=0, timeout=40) for k in settings.groq_keys]
        self._rr = itertools.count()
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._clients)

    def _ordered_clients(self) -> list[Groq]:
        with self._lock:
            start = next(self._rr) % len(self._clients)
        return self._clients[start:] + self._clients[:start]

    def chat(self, messages: list[dict], *, tools: list[dict] | None = None, models: list[str] | None = None,
             temperature: float = 0.2, max_tokens: int = 1500, notes: list[str] | None = None):
        """Returns (message, model_used). `notes` collects human-readable fallback events for the trace."""
        if not self._clients:
            raise LLMUnavailable("GROQ_API_KEYS is not set")
        models = list(models or settings.groq_model_chain)
        if is_simulated_down("primary-llm") and len(models) > 1:
            if notes is not None:
                notes.append(f"{models[0]}: simulated outage (resilience demo) — switching model")
            models = models[1:]
        last = "no model tried"
        for attempt in range(2):
            rate_limited, wait_s = False, 0.0
            for model in models:
                for client in self._ordered_clients():
                    try:
                        return self._call(client, model, messages, tools, temperature, max_tokens)
                    except RateLimitError as e:
                        last, rate_limited = f"{model}: rate limited", True
                        wait_s = max(wait_s, _retry_after(e))
                        continue                    # same model, next key
                    except BadRequestError as e:
                        body = str(e)
                        if "tool_use_failed" in body or "Failed to call a function" in body:
                            raise ToolFormatError(body[:300])
                        last = f"{model}: rejected request ({_short(body)})"
                        log.warning("Groq 400 on %s: %s", model, body[:300])
                        break                       # model-specific problem: try the next model
                    except NotFoundError:
                        last = f"{model}: not found"
                        break
                    except APIStatusError as e:     # e.g. 413 request exceeds tokens-per-minute
                        last, rate_limited = f"{model}: HTTP {e.status_code}", e.status_code in (413, 429)
                        break
                    except (APIConnectionError, APITimeoutError) as e:
                        last = f"{model}: {type(e).__name__}"
                        continue
                if notes is not None:
                    notes.append(f"{last} — switching model")
            if not rate_limited or attempt == 1:
                break
            # Every model is rate-limited: wait out Groq's per-minute window once rather than failing.
            pause = min(max(wait_s, 2.0), 8.0)
            if notes is not None:
                notes.append(f"All models rate-limited — waiting {pause:.0f} s for the limit window, then retrying")
            time.sleep(pause)
        raise LLMUnavailable(last)

    @staticmethod
    def _call(client, model, messages, tools, temperature, max_tokens):
        kwargs = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
                      **model_options(model))
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        if msg.content:
            # gpt-oss often writes source tags with CJK brackets 【…】; normalise to [...]
            # so the UI badges, the verifier and the speech cleanup all recognise them.
            msg.content = THINK_RE.sub("", msg.content).replace("【", "[").replace("】", "]").strip()
        return msg, model


def _retry_after(e: Exception) -> float:
    try:
        return float(e.response.headers.get("retry-after", 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _short(body: str) -> str:
    m = re.search(r"'message': '([^']{0,120})", body)
    return m.group(1) if m else body[:120]


pool = LLMPool()
