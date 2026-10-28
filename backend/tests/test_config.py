"""Boot-time JWT_SECRET guard (AGENTS.md §3.1, A-8).

Pure settings-level tests: no database, no app. ``Settings`` is built with
``_env_file=None`` so the repository ``.env`` cannot influence the outcome,
and every relevant field is passed explicitly so host environment variables
cannot either (init kwargs win over the environment).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG = "a" * 64  # any 64-char secret satisfies the guard


class TestEmptySecret:
    def test_debug_auto_generates_a_random_secret(self) -> None:
        first = Settings(jwt_secret="", debug=True, _env_file=None)
        second = Settings(jwt_secret="", debug=True, _env_file=None)
        # token_hex(32) -> 64 hex chars, fresh on every boot.
        assert len(first.jwt_secret) == 64
        assert first.jwt_secret != second.jwt_secret

    def test_without_debug_fails_boot(self) -> None:
        with pytest.raises(ValidationError, match="must be set"):
            Settings(jwt_secret="", debug=False, _env_file=None)


class TestWeakSecret:
    @pytest.mark.parametrize(
        "secret",
        [
            "short",
            "a" * 31,  # one char under the minimum
            "dev-only-change-me-in-prod",  # the old shipped default
            "change-me",
            "secret",
        ],
    )
    def test_weak_without_debug_fails_boot(self, secret: str) -> None:
        with pytest.raises(ValidationError, match="too weak"):
            Settings(jwt_secret=secret, debug=False, _env_file=None)

    def test_weak_with_debug_is_kept(self) -> None:
        # Warn-and-continue in dev: the value is preserved, not replaced.
        assert Settings(jwt_secret="short", debug=True, _env_file=None).jwt_secret == "short"


class TestStrongSecret:
    def test_strong_secret_passes_without_debug(self) -> None:
        settings = Settings(jwt_secret=STRONG, debug=False, _env_file=None)
        assert settings.jwt_secret == STRONG
