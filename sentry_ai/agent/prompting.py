"""Renders the Jinja2 prompt files in agent/prompts/ (CONVENTIONS section 3: no inline prompts)."""

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(PROMPTS_DIR),
        undefined=StrictUndefined,  # a missing variable is a bug, not an empty string
        autoescape=False,  # plain-text prompts, never HTML
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def render(name: str, **values: Any) -> str:
    """Render agent/prompts/<name>.md with `values`."""
    return _env().get_template(f"{name}.md").render(**values).strip()
