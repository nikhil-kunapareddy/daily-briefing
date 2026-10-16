"""Top‑level package for *daily‑briefing*.

Only submodules are imported explicitly by callers (e.g. ``from app import db``).
Importing this package must not have side effects such as reading environment
variables, because the test suite imports ``app`` before setting ``DAILY_BRIEFING_DATA``.
Therefore we expose the submodules lazily via ``__all__`` without importing them
eagerly.
"""

__all__ = ["config", "db"]

