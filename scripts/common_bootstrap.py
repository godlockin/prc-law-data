"""Make this checkout's shared package importable from script entry points."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
