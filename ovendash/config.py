"""
Environment loading.

Deployment-specific values - which tenant, which site - stay out of source
because this repo is public and the real values embed a tenant hostname and a
user's UPN. A tiny .env reader rather than a dependency, since this is the
only thing that needs one.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path | None = None) -> None:
    """Read .env into os.environ. Existing variables always win.

    Silent when the file is absent - running on fixtures needs no config, and
    a missing .env is the normal state for someone who just cloned this.
    """
    target = path or ENV_PATH
    if not target.exists():
        return

    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
