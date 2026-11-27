from __future__ import annotations

import sys
from pathlib import Path

CONSULT_ROOT: Path = Path(__file__).resolve().parent.parent
LESSONS_DIR: Path = CONSULT_ROOT / "skills" / "lessons"

if str(LESSONS_DIR) not in sys.path:
    sys.path.insert(0, str(LESSONS_DIR))
