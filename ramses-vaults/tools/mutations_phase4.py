#!/usr/bin/env python3
"""Targeted keeper/launch-plan mutations. Restore exact bytes, reject invalid/zero-test kills."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
WORKER=ROOT/'ramses-vaults/keeper/worker.py'
PLAN=ROOT/'ramses-vaults/script/DeployMarkets.s.sol'
CASES=[
 ('role preflight','require(not chain.uint(m[key], \'hasRole(bytes32,address)\', role, keeper), \'keeper has privileged role\')','require(True, \'keeper has privileged role\')','test_privileged_roles_rejected_even_monitor'),
 ('timelock role','require(not chain.uint(m[\'timelock\'], \'hasRole(bytes32,address)\', role, keeper), \'keeper has timelock role\')','require(True, \'keeper has timelock role\')','test_privileged_roles_rejected_even_monitor'),
 ('runtime hash',"code != '0x' and offline('keccak', code).lower() == expected.lower()","code != '0x'",'test_every_code_hash_pin'),
 ('required pins',"{address(m[k]) for k in required} <= set(pins)",'True','test_missing_required_pin'),
 ('proxy storage pin',"chain.rpc('eth_getStorageAt', [pin['address'], pin['slot'], 'latest']).lower() == pin['value'].lower()",'True','test_storage_and_call_pins'),
 ('journal before send','self.j.save() # durable intent BEFORE any possible submission','pass # mutant omits durable intent','test_intent_persisted_before_submit'),
 ('receipt status',"int(receipt['status'],16) == 1",'True','test_mined_revert_with_zero_exit_is_not_success'),
 ('receipt identity',"receipt.get('from','').lower() == self.c.keeper",'True','test_receipt_identity_mismatch_stops'),
 ('ambiguous stop',"require(p.get('hash'), 'ambiguous submission; operator must reconcile journal')",'pass','test_ambiguous_submission_stops_on_restart'),
 ('lost journal',"chain.nonce() == chain.nonce('latest') == bootstrap_nonce",'True','test_journal_loss_used_signer_refuses'),
 ('gas price cap','if price > self.max_gas_price:','if False:','test_gas_price_cap_no_sends'),
 ('cycle gas budget',"self.gas_used + GAS[action] <= MAX_CYCLE_GAS",'True','test_cycle_gas_cap'),
 ('explicit gas limit',"'gas':hex(GAS[action])","'gas':hex(21000)",'test_gas_price_is_explicit_not_only_simulated'),
 ('deadline clock',"max(int(self.c.head()['timestamp'],16), int(time.time()))","int(self.c.head()['timestamp'],16)",'test_deadline_later_chain_or_wall'),
 ('checkpoint ordering',"('checkpoint',self.m['vault']),",'','test_order_and_noop'),
 ('harvest ordering',"('harvest',self.m['vault']),",'','test_order_and_noop'),
 ('50 bps threshold',"baseline*50","baseline*51",'test_price_trigger_exactly_50bps_both_directions'),
 ('50 bps both directions',"abs(state['price']-baseline)","(state['price']-baseline)",'test_price_trigger_exactly_50bps_both_directions'),
 ('checkpoint rate limit',"now-self.j.data['lastAttempt'] < self.minimum",'False','test_rate_limit_price_moves'),
 ('receipt block baseline',"block = receipt['blockNumber']","block = 'latest'",'test_checkpoint_price_uses_receipt_block'),
 ('one signer lock',"fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",'pass','test_lock_independent_of_state_path'),
 ('RPC allowlist',"local if local_only else (local or url == RPC)",'True','test_endpoint_parser_rejects_prefix_tricks'),
 ('pre-send preflight',"preflight(self.c, self.m, True) # governance/code changes between transactions fail closed",'pass','test_preflight_repeated_before_send'),
 ('chain id',"int(chain.rpc('eth_chainId', []), 16) == 4663 == m['chainId']",'True','test_wrong_chain'),
 ('market operator',"not execute or chain.addr(m[key], 'vaultOperator()') == keeper",'True','test_execute_requires_role_and_operator'),
 ('initial range bootstrap', "if action == 'rebalance':", 'if False:', 'test_initial_range_does_not_mint_interim_full_range_nft'),
 ('caught delegate failure', "require(not any(log.get('address','').lower() == p['target'].lower() and log.get('topics', [''])[0] in failed_topics for log in receipt.get('logs', [])), 'market reported caught vault failure')", 'pass', 'test_successful_receipt_with_caught_delegate_failure_alerts'),
 ('required keeper role', "require(not execute or holds, 'wallet lacks KEEPER_ROLE')", 'pass', 'test_execute_requires_role_and_operator'),
 ('gas configuration ceiling', '0 < max_gas_price <= 2_000_000_000', 'True', 'test_configured_gas_and_rate_limit_bounds'),
 ('dust matched budget', 'dust = min(ledger[2]*price//10**18, ledger[3]*10**12) < 10**16', 'dust = True', 'test_unmatched_idle_is_dust_but_material_matched_failure_alerts'),
 ('cast gas flag', "'--gas-price', str(int(tx['gasPrice'],16))", "'--gas-price', '1'", 'test_production_cast_arguments_enforce_nonce_gas_and_async'),
 ('cooldown retry', ' or (0 < retry_at <= now)', '', 'test_cooldown_expiry_retried_without_another_price_move_after_restart'),
]
PLAN_CASES=[
 ('launch CF', '(ms[i], 0.8e18)', '(ms[i], 0.3e18)'),
 ('launch supply caps', '(i == 0 ? sCap : uCap)', '(i == 0 ? uCap : sCap)'),
 ('launch borrow caps','(ms, caps)', '(ms, new uint256[](2))'),
 ('launch borrowing enabled','controller._setBorrowPaused, (ms[i], false)', 'controller._setBorrowPaused, (ms[i], true)'),
]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--list',action='store_true'); p.add_argument('--plan',action='store_true'); p.add_argument('--only',action='append'); a=p.parse_args()
    cases=PLAN_CASES if a.plan else CASES
    if a.list: print('\n'.join(x[0] for x in cases)); return
    if a.only: cases=[x for x in cases if x[0] in a.only]
    if a.plan and len(cases) != 1: p.error('Run exactly one --plan --only case per command (ten-minute bound)')
    started=time.monotonic()
    report_path=ROOT/'ramses-vaults/research/mutations-phase4.json'
    report=json.loads(report_path.read_text()) if report_path.exists() else []
    path=PLAN if a.plan else WORKER
    for case in cases:
        name,old,new=case[:3]; original=path.read_bytes(); content=original.decode()
        if content.count(old)!=1: raise RuntimeError('Pattern must match exactly once: '+name)
        log_path=ROOT/('ramses-vaults/research/mutation-phase4-'+name.replace(' ','-')+'.log')
        cmd=(['forge','test','--threads','1','--match-path','ramses-vaults/test/LaunchPlan.t.sol','--match-test','testApprovedCFAndCapsInActualTimelockPlan','-vv'] if a.plan else
             [sys.executable,'-B','-m','unittest','test_worker.WorkerTests.'+case[3]])
        killed=False; code=124
        try:
            path.write_text(content.replace(old,new))
            with log_path.open('w') as out:
                child=subprocess.Popen(cmd,cwd=ROOT if a.plan else WORKER.parent,env={**os.environ,'FOUNDRY_PROFILE':'ramses','PYTHONDONTWRITEBYTECODE':'1'},stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
                try: code=child.wait(timeout=max(1,590-(time.monotonic()-started)))
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL); child.wait()
            log=log_path.read_text()
            killed=code!=0 and code!=124 and ('[FAIL:' in log if a.plan else ('FAIL:' in log and 'Ran 1 test' in log))
        finally:
            path.write_bytes(original)
            assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(original).digest()
        entry=dict(name=name,killed=killed,returncode=code,sourceSHA256=hashlib.sha256(original).hexdigest(),command=cmd)
        report=[x for x in report if x['name']!=name]+[entry]; report_path.write_text(json.dumps(report,indent=2)+'\n')
        print(name, 'KILLED' if killed else 'SURVIVED/INVALID',flush=True)
        if not killed: raise SystemExit(log[-3000:])

if __name__=='__main__':
    import sys
    main()
