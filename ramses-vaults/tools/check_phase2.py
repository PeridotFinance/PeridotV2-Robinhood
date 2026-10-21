#!/usr/bin/env python3
"""Run a local check with a hard ten-minute limit. Never signs or broadcasts."""
import os, signal, subprocess, sys, time
command = ['/usr/bin/caffeinate', '-i', *sys.argv[1:]] if sys.platform == 'darwin' else sys.argv[1:]
child = subprocess.Popen(command, env={**os.environ, 'FOUNDRY_PROFILE': 'ramses'}, start_new_session=True)
deadline = time.time() + 600
while child.poll() is None:
    if time.time() >= deadline:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        print('STOPPED: command and children exceeded ten minutes of wall time', file=sys.stderr)
        sys.exit(124)
    time.sleep(0.2)
sys.exit(child.returncode)
