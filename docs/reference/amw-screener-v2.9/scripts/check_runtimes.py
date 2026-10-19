"""Print which Strategy Lab authoring modes can execute on this machine (used by setup_windows.bat)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.lab.contract import capabilities  # noqa: E402

cap = capabilities()
sb = cap["sandbox_status"]
print(f"  OS sandbox verified: {sb['verified']}  (local trusted mode: {'on' if sb['unsandboxed_allowed'] else 'off'})")
for k in cap["_enabled"]:
    print(f"  {k:11s} execution: {cap[k]['native_execution']}")
