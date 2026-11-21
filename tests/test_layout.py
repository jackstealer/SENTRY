"""Guards the package layout promised in CLAUDE.md section 6."""

import importlib

import pytest

PACKAGES = [
    "sentry_ai",
    "sentry_ai.agent",
    "sentry_ai.tools",
    "sentry_ai.services",
    "sentry_ai.scheduler",
    "sentry_ai.interfaces",
    "sentry_ai.interfaces.api",
    "sentry_ai.interfaces.telegram",
    "sentry_ai.db",
    "sentry_ai.db.repositories",
    "sentry_ai.memory",
    "sentry_ai.observability",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name: str) -> None:
    assert importlib.import_module(name) is not None
