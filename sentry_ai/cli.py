"""Dev CLI: the same agent as Telegram, in a terminal.

uv run python -m sentry_ai.cli "what's on today"     # one turn
uv run python -m sentry_ai.cli --repl                # chat until "exit"
add -v for INFO logs (the full trace) on stderr, -vv for DEBUG
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator, Callable

from sentry_ai.agent.deps import AgentDeps, default_deps
from sentry_ai.agent.graph import Graph, build_graph, postgres_checkpointer, run_turn
from sentry_ai.db.session import get_engine
from sentry_ai.observability.logging import configure_logging

EXIT_WORDS = {"exit", "quit", ":q"}


async def converse(
    graph: Graph,
    deps: AgentDeps,
    lines: AsyncIterator[str],
    out: Callable[[str], None] = print,
) -> None:
    """Run each line as a turn and print the reply with its trace id, until an exit word."""
    async for raw in lines:
        text = raw.strip()
        if not text:
            continue
        if text.lower() in EXIT_WORDS:
            return
        try:
            result = await run_turn(graph, text, deps=deps)
        except Exception as exc:  # keep the REPL alive; the log line has the details
            out(f"error: {type(exc).__name__}: {exc}")
            continue
        out(f"{result.reply}\n[trace {result.trace_id}]")


async def _stdin_lines() -> AsyncIterator[str]:
    while True:
        try:
            yield await asyncio.to_thread(input, "you> ")
        except EOFError:
            return


async def _one(text: str) -> AsyncIterator[str]:
    yield text


async def run(args: argparse.Namespace) -> int:
    try:
        async with postgres_checkpointer() as saver:
            lines = _stdin_lines() if args.repl else _one(" ".join(args.message))
            await converse(build_graph(saver), default_deps(), lines)
    finally:
        await get_engine().dispose()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sentry_ai.cli", description="Talk to SENTRY.")
    parser.add_argument("message", nargs="*", help="one message (omit with --repl)")
    parser.add_argument("--repl", action="store_true", help="interactive chat")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v INFO, -vv DEBUG")
    args = parser.parse_args(argv)
    if args.repl == bool(args.message):
        parser.error("give a message, or --repl (not both)")
    return args


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # quiet by default so replies stay readable; logs go to stderr either way
    configure_logging(("WARNING", "INFO", "DEBUG")[min(args.verbose, 2)], sys.stderr)
    # psycopg (the checkpointer) cannot use Windows' default Proactor loop
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        try:
            code = runner.run(run(args))
        except KeyboardInterrupt:
            code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
