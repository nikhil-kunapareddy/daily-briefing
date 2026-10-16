import os
import sys
from pathlib import Path

# Pytest runs inside a temporary directory; ensure the repository root is on
# ``sys.path`` so ``import app`` works.
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
