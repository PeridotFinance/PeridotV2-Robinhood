#!/usr/bin/env python3
"""Disposable chain-4663 fork, no generated accounts/keys, hard ten-minute lifetime."""
import os, signal, subprocess, sys
from pathlib import Path
port=os.environ.get('RAMSES_ANVIL_PORT','8557')
args=['anvil','--host','127.0.0.1','--port',port,'--chain-id','4663','--accounts','0','--silent',
      '--fork-url',os.environ['ROBINHOOD_RPC_URL'],'--fork-block-number',os.environ['REMEDIATION_FORK_BLOCK'],
      '--gas-limit','100000000','--disable-code-size-limit']
# EIP170 is independently checked; larger test-driver/script initcode is local-only.
child=subprocess.Popen(args,start_new_session=True)
def stop(*_):
    os.killpg(child.pid,signal.SIGTERM)
    child.wait(); sys.exit(0)
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
try: sys.exit(child.wait(timeout=600))
except subprocess.TimeoutExpired:
    os.killpg(child.pid,signal.SIGKILL); child.wait()
    print('STOPPED disposable anvil at ten-minute lifetime',file=sys.stderr); sys.exit(124)
