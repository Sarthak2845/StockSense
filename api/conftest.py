# Makes the api modules importable from tests/ without installing a package.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
