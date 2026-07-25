"""Small tracked-source secret heuristic; not a replacement for enterprise DLP."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)(?:password|passwd|secret|token)\s*=\s*['\"][^'\"]{4,}['\"]"
    ),
)


def main():
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8").split("\0")
    findings = []
    for relative in filter(None, tracked):
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if "must-not-appear" in line or "<redacted>" in line:
                continue
            if any(pattern.search(line) for pattern in PATTERNS):
                findings.append(f"{relative}:{line_number}")
    if findings:
        raise SystemExit("possible tracked secret:\n" + "\n".join(findings))
    print(f"secret heuristic: scanned {len(list(filter(None, tracked)))} tracked files")


if __name__ == "__main__":
    main()
