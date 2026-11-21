"""Conversion between LangGraph's message objects and OpenAI Chat Completions."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, convert_to_openai_messages
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageParam

UNPARSED = "_unparsed_arguments"  # set when the model sent tool arguments that are not JSON
HISTORY_MESSAGES = 12  # ARCHITECTURE section 3: system prompt + the last 12 messages
MAX_PROMPT_TOKENS = 6000  # whole request: system prompt, tool schemas and history
CHARS_PER_TOKEN = 4  # rough estimate; good enough for a budget, no tokenizer dependency


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _size(message: AnyMessage) -> int:
    calls = json.dumps(message.tool_calls) if isinstance(message, AIMessage) else ""
    return len(str(message.content)) + len(calls)


def trim(messages: Sequence[AnyMessage], *, budget_chars: int) -> list[AnyMessage]:
    """Recent history that fits: whole turns only, newest first, the current turn always.

    Cutting only at user messages keeps every tool call next to its results, which the API
    requires. Earlier turns are added while the total stays within HISTORY_MESSAGES messages
    and `budget_chars`.
    """
    turns: list[list[AnyMessage]] = []
    for message in messages:
        if isinstance(message, HumanMessage) or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)
    if not turns:
        return []
    kept = [turns[-1]]
    count, size = len(turns[-1]), sum(map(_size, turns[-1]))
    for turn in reversed(turns[:-1]):
        turn_size = sum(map(_size, turn))
        if count + len(turn) > HISTORY_MESSAGES or size + turn_size > budget_chars:
            break
        kept.insert(0, turn)
        count, size = count + len(turn), size + turn_size
    return [message for turn in kept for message in turn]


def to_openai(messages: Sequence[AnyMessage]) -> list[ChatCompletionMessageParam]:
    converted: list[ChatCompletionMessageParam] = convert_to_openai_messages(list(messages))  # type: ignore[assignment]
    return converted


def from_openai(message: ChatCompletionMessage) -> AIMessage:
    calls = []
    for call in message.tool_calls or []:
        if call.type != "function":
            continue
        try:
            args: Any = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):
            args = {UNPARSED: call.function.arguments}
        calls.append({"name": call.function.name, "args": args, "id": call.id})
    return AIMessage(content=message.content or "", tool_calls=calls)
