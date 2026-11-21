#!/usr/bin/env python3
"""One bounded, reversible Phase 5a mutation per invocation. Never signs or broadcasts."""
import argparse, hashlib, json, os, pathlib, signal, subprocess, time
ROOT=pathlib.Path(__file__).resolve().parents[2]
ADAPTER='ramses-vaults/src/RamsesV3PairedAdapter.sol'
CASES={
 'allocation': (ADAPTER,'_checkAllocationValue(p, ref, added, a.used0, a.used1);','// removed allocation value check','testPhase5aAllocationNarrowUpper'),
 'recovery': (ADAPTER,'uint256 credited = pendingRewards[id][token];','revert InvalidConfiguration();\n        uint256 credited = pendingRewards[id][token];','testPhase5aRestrictedTransferRecoveryAndPrincipalExclusion'),
 'recovery-authority': (ADAPTER,'if (!IRewardAuthority(vault).hasRole(keccak256("CONFIG_ROLE"), msg.sender)) {\n            revert NotVault();\n        }','// no authorization','testPhase5aRestrictedTransferRecoveryAndPrincipalExclusion'),
 'recovery-principal': (ADAPTER,'_principalTokens[token] || token == address(positionManager)','token == address(positionManager)','testPhase5aRestrictedTransferRecoveryAndPrincipalExclusion'),
 'forfeiture': ('ramses-vaults/src/libraries/RamsesRewardLib.sol','if (p.fresh && p.adapter.allowRewardForfeiture(p.id))','if (false)','testPhase5aRecenterForfeitureSkipsBrokenRewardBookkeeping'),
 'identity': ('ramses-vaults/src/RamsesVaultAdapter.sol','if (address(RamsesOracleGuard(address(oracle)).poolOf(id)) != address(p.pool))','if (false)','testPhase5aRegistrationRejectsGuardPoolMismatch'),
 'borrow-order': ('ramses-vaults/src/lending/RamsesBoostedDelegate.sol','        _prepareFixedLiquidity(amount);\n        // borrowFresh','        // borrowFresh','testPhase5aBorrowChecksHealthAfterWithdrawalLoss'),
 'rate': ('ramses-vaults/tools/verify_deployment.py',"assert maximum <= 5 * 10**12, 'Interest model exceeds market maximum borrow rate'",'pass','test_phase5a_maximum_rate_guard'),
}

def main():
    p=argparse.ArgumentParser(); p.add_argument('case',choices=CASES); a=p.parse_args()
    path,old,new,test=CASES[a.case]; path=ROOT/path
    original=path.read_bytes(); content=original.decode()
    assert content.count(old)==1
    changed=content.replace(old,new)
    if a.case=='borrow-order':
        marker='require(err == 0 && shortfall == 0, "POST_CHECKPOINT_SHORTFALL");'
        assert changed.count(marker)==1
        changed=changed.replace(marker,marker+'\n        _prepareFixedLiquidity(amount);')
    python=a.case=='rate'
    target='Phase5aAdapter.t.sol' if path.name=='RamsesV3PairedAdapter.sol' else 'Phase5aLending.t.sol'
    cmd=['python3','-m','unittest','test_deployment_tools.Phase5aRateTest'] if python else ['forge','test','--threads','1','--match-path','ramses-vaults/test/'+target,'--match-test',test,'-vv']
    # Exclude unrelated root test files from mutation compilation. Imports remain resolved.
    if not python:
        for file in (ROOT/'ramses-vaults/test').rglob('*.t.sol'):
            if file.name!=target: cmd+=['--skip',str(file.relative_to(ROOT))]
        cmd+=['--skip','script']
    log=ROOT/('ramses-vaults/research/mutation-phase5a-'+a.case+'.log')
    try:
        path.write_text(changed)
        with log.open('w') as output:
            proc=subprocess.Popen(cmd,cwd=ROOT/'ramses-vaults/tools' if python else ROOT,env={**os.environ,'FOUNDRY_PROFILE':'ramses'},stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
            try: code=proc.wait(timeout=590)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL); proc.wait(); code=124
    finally:
        path.write_bytes(original)
        assert path.read_bytes()==original
    text=log.read_text()
    killed=code not in (0,124) and (('FAIL:' in text and 'Ran 1 test' in text) if python else ('[FAIL:' in text and test in text))
    result={'case':a.case,'killed':killed,'returncode':code,'sourceSHA256':hashlib.sha256(original).hexdigest(),'command':cmd}
    report=ROOT/'ramses-vaults/research/mutations-phase5a.json'
    records=json.loads(report.read_text()) if report.exists() else []
    report.write_text(json.dumps([r for r in records if r['case']!=a.case]+[result],indent=2)+'\n')
    print(json.dumps(result))
    if not killed: raise SystemExit(text[-4000:])
if __name__=='__main__': main()
