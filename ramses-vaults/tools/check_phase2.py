#!/usr/bin/env python3
"""Run a local check with a hard ten-minute limit. Never signs or broadcasts."""
import os, signal, subprocess, sys
child = subprocess.Popen(sys.argv[1:], env={**os.environ, 'FOUNDRY_PROFILE': 'ramses'}, start_new_session=True)
try:
    sys.exit(child.wait(timeout=600))
except subprocess.TimeoutExpired:
    os.killpg(child.pid, signal.SIGKILL)
    child.wait()
    print('STOPPED: command and children exceeded ten minutes', file=sys.stderr)
    sys.exit(124)
