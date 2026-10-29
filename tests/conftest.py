import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("DB_PATH", ":memory:")
# The bot ships private (PRIVATE_MODE on). The older tests exercise the open-server paths, so run them with it off;
# tests/test_private_mode.py turns it on explicitly.
os.environ.setdefault("PRIVATE_MODE", "false")
