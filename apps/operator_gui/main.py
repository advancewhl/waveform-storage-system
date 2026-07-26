"""Primary operator entry point for the integrated registry-driven UI.

The historical Designer-based application remains available as
``legacy_main.py`` during the experimental-branch migration. This entry point
defaults to the safe demo backend and the audited 383-device candidate
registry; connected modes still require explicit command-line selection.
"""

from __future__ import annotations

from experimental_main import main


if __name__ == "__main__":
    raise SystemExit(main())
