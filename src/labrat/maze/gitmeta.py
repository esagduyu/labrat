"""Git-sha provenance for derived Scent writes (Moat Extra 2.3, D2).

Honest-unknown by construction: any failure to resolve a short sha (no repo,
no git binary, timeout, nonzero exit) returns ``None`` rather than raising —
callers stamp ``git_sha=None`` in that case, which the Meta renderer omits,
so the write is byte-identical to the no-git-root path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def current_git_sha(root: Path) -> str | None:
    """Return the short HEAD sha for the git repo at ``root``, or ``None``.

    Never raises: subprocess errors, timeouts, missing git, and non-repo
    roots all collapse to ``None``.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.decode("utf-8", errors="replace").strip()
    return sha or None
