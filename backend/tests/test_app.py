"""Smoke test: the app imports and the health endpoint responds.

Runs without a database. Requires dev dependencies (``pip install -e .[dev]``).
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_health() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 200
    # No lifespan (no ``with``) and no REDIS_URL: the bus reports local mode.
    assert response.json() == {"status": "ok", "redis": "off"}


class TestApiDocsToggle:
    """EXPOSE_API_DOCS gates the Swagger/ReDoc/spec routes (README §Environment)."""

    @pytest.mark.parametrize(("value", "expected"), [("true", 200), ("false", 404)])
    def test_docs_follows_the_setting(
        self, monkeypatch: pytest.MonkeyPatch, value: str, expected: int
    ) -> None:
        monkeypatch.setenv("EXPOSE_API_DOCS", value)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.setenv("JWT_SECRET", "a" * 64)
        get_settings.cache_clear()
        try:
            client = TestClient(create_app())
            assert client.get("/api/docs").status_code == expected
            assert client.get("/api/redoc").status_code == expected
            assert client.get("/api/openapi.json").status_code == expected
        finally:
            get_settings.cache_clear()
