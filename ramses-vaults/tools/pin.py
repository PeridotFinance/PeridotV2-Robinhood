#!/usr/bin/env python3
"""Read-only: print shell exports for a fresh public-RPC fork pin."""
import json, os, subprocess
b=json.loads(subprocess.check_output(['cast','block','latest','--rpc-url',os.environ['ROBINHOOD_RPC_URL'],'--json'],text=True))
print('export REMEDIATION_FORK_BLOCK='+str(int(b['number'],16)))
print('export REMEDIATION_NATIVE_BLOCK='+str(int(b['l1BlockNumber'],16)))
print('# blockHash='+b['hash']+' timestamp='+str(int(b['timestamp'],16)))
