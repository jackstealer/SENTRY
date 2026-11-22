"""Fakes shared by the agent and scheduler tests: a scripted LLM and a word-hash embedder."""

import itertools
import json
from collections import defaultdict
from typing import Any

from openai.types.chat import ChatCompletion

_ids = itertools.count(1)


def completion(
    content: str | None = None, calls: list[tuple[str, Any]] | None = None
) -> ChatCompletion:
    tool_calls = [
        {
            "id": f"call_{next(_ids)}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": args if isinstance(args, str) else json.dumps(args),
            },
        }
        for name, args in calls or []
    ]
    return ChatCompletion.model_validate(
        {
            "id": "cmpl",
            "object": "chat.completion",
            "created": 0,
            "model": "fake",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "tool_calls": tool_calls or None,
                    },
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
        }
    )


class FakeLLM:
    """Stands in for llm.chat: replies are queued per purpose ("route", "act")."""

    def __init__(self) -> None:
        self.replies: dict[str, list[Any]] = defaultdict(list)
        self.calls: list[dict[str, Any]] = []

    def route(self, intent: str, confirmation: str = "none") -> "FakeLLM":
        content = json.dumps({"intent": intent, "confirmation": confirmation})
        self.replies["route"].append(completion(content))
        return self

    def say(self, text: str) -> "FakeLLM":
        self.replies["act"].append(completion(text))
        return self

    def call(self, *calls: tuple[str, Any]) -> "FakeLLM":
        self.replies["act"].append(completion(None, list(calls)))
        return self

    def fail(self, purpose: str, error: Exception) -> "FakeLLM":
        self.replies[purpose].append(error)
        return self

    async def __call__(
        self, messages: Any, *, purpose: str, tier: str = "fast", **params: Any
    ) -> Any:
        self.calls.append({"purpose": purpose, "tier": tier, "messages": messages, **params})
        reply = self.replies[purpose].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def purposes(self) -> list[str]:
        return [c["purpose"] for c in self.calls]


async def fake_embed(texts: list[str]) -> list[list[float]]:
    """Same words, same direction (enough for memory relevance in tests)."""
    vectors = []
    for text in texts:
        vec = [0.0] * 1536
        for word in text.lower().split():
            vec[sum(map(ord, word.strip(".,!?"))) % 1536] += 1.0
        vectors.append(vec)
    return vectors
