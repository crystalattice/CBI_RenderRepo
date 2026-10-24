#!/usr/bin/env python3
"""Bootstrap RenderRepo setup without requiring RR/PyYAML in this interpreter."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from renderrepo.setup import main
if __name__ == '__main__':
    raise SystemExit(main())
