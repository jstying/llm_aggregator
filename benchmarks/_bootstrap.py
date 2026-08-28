"""Shared bootstrap: make `import main` work when a benchmark is run from anywhere."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
