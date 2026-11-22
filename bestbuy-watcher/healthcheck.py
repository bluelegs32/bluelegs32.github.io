"""Container health: the state file must have been touched recently.

In loop mode every pass rewrites state.json, so a stale file means the loop
has wedged. A fresh container with no state yet is considered healthy.
"""

import os
import sys
import time

state = os.environ.get("STATE_PATH", "/data/state.json")
interval = float(os.environ.get("INTERVAL_SECONDS", "3600"))

if os.environ.get("RUN_MODE", "loop") != "loop" or not os.path.exists(state):
    sys.exit(0)

age = time.time() - os.path.getmtime(state)
if age > interval * 3:
    print(f"state.json is {age/60:.0f} min old (interval {interval/60:.0f} min)")
    sys.exit(1)
sys.exit(0)
