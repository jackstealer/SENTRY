"""Conversion between LangGraph's message objects and OpenAI Chat Completions."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, convert_to_openai_messages
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageParam

UNPARSED = "_unparsed_arguments"  # set when the model sent tool arguments that are not JSON


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
