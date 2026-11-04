#!/usr/bin/env python3
"""SDK runner entry point: dk scripts/run_devkit.py --backend neat ..."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from construction_safety.app import main

raise SystemExit(main())
