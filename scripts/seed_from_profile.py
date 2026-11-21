"""Seed routines, habits and preferences from docs/USER_PROFILE.md. Safe to run again.

Usage: uv run python scripts/seed_from_profile.py [--profile PATH]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sentry_ai.config import get_settings
from sentry_ai.db.session import get_engine, get_sessionmaker
from sentry_ai.services.profile import ProfileError, load_profile, seed_profile

DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / "docs" / "USER_PROFILE.md"


async def run(path: Path) -> int:
    try:
        profile = load_profile(path)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if profile.is_template:
        print(
            f'error: {path.name} still has template values (name: "Your Name"). '
            "Fill in your real routine first.",
            file=sys.stderr,
        )
        return 2
    settings = get_settings()
    try:
        async with get_sessionmaker()() as session, session.begin():
            report = await seed_profile(session, settings.user_id, profile, settings=settings)
    finally:
        await get_engine().dispose()
    print(report.summary())
    print("nothing to change." if report.is_noop else "seeded.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SENTRY from docs/USER_PROFILE.md")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    raise SystemExit(asyncio.run(run(parser.parse_args().profile)))


if __name__ == "__main__":
    main()
