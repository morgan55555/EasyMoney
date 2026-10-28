"""AGENTS.md §5.5: no floats in money paths.

AST-scans the backend money paths — ``services/``, ``schemas/``, ``models/``,
``api/``, ``core/`` — for ``float`` names (calls, annotations) and float
literals.

The single tolerated ``float`` in the codebase is the JWT ``exp`` timestamp
in ``app/ws/endpoint.py`` — a time value, not money — which is deliberately
outside the money paths scanned here.
"""

from __future__ import annotations

import ast
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
MONEY_PATHS = ("services", "schemas", "models", "api", "core")


def _float_hits(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "float":
            hits.append(f"line {node.lineno}: 'float' name (call/annotation)")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            hits.append(f"line {node.lineno}: float literal {node.value!r}")
    return hits


def test_no_floats_in_money_paths() -> None:
    offenders: dict[str, list[str]] = {}
    for sub in MONEY_PATHS:
        for path in sorted((_APP / sub).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hits = _float_hits(tree)
            if hits:
                offenders[str(path.relative_to(_APP))] = hits
    assert not offenders, f"floats found in money paths: {offenders}"
