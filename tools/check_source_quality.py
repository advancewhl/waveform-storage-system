"""Dependency-free source checks for new non-legacy modules."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    failures = []
    for path in (ROOT / "src" / "waveform_storage").rglob("*.py"):
        if "legacy" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}: bare except")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ):
                failures.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: print in new module"
                )
    if failures:
        raise SystemExit("\n".join(failures))
    print("source quality: no bare except or print in non-legacy modules")


if __name__ == "__main__":
    main()
