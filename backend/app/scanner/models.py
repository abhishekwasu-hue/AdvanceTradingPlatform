"""M55 Q1: moved to `app/screener/scanner/models.py`. This old import path is the same module object (not a copy), so
an attribute read or patched here is the one the code uses."""
import sys

from app.screener.scanner import models as _moved

sys.modules[__name__] = _moved
