#!/usr/bin/env python3
"""Serial, bounded Phase 2 mutations. Restore source and preserve inherited evidence.
Select at most three cases per command so the entire command stays below ten minutes.
Compiler errors, timeouts and zero matched tests NEVER count as a kill.
"""
import argparse, hashlib, json, os, pathlib, re, signal, subprocess, time
ROOT = pathlib.Path('ramses-vaults')
if pathlib.Path('/tmp/ramses-phase2-mutations.pause').exists():
    raise SystemExit('Mutation batches paused between commands; source restored.')
cases=[]
def case(name,file,old,new,test,suite='Phase2.t.sol'):
    cases.append((name,ROOT/'src'/file,old,new,test,suite))
case('feed freshness','RamsesOracleGuard.sol',r'if \(block.timestamp - updated > c.maxStaleness\)', 'if (false)', 'testTwelveHourBoundaryAndWeekendFreshFivePercent')
case('token pause','RamsesOracleGuard.sol',r'if \(IPausableStock\(c.stockToken\).paused\(\)\)', 'if (false)', 'testPausedDisabledInvalidFutureIncompleteRevert')
case('feed validity','RamsesOracleGuard.sol',r'answer <= 0 \|\| updated == 0 \|\| updated > block.timestamp','answer <= 0', 'testPausedDisabledInvalidFutureIncompleteRevert')
case('completed feed round','RamsesOracleGuard.sol',r'if \(answered < round\)','if (false)', 'testPausedDisabledInvalidFutureIncompleteRevert')
case('asymmetric price gates','RamsesOracleGuard.sol',r'allocating \? c.maxPriceDeviationBps : c.maxRemovalDeviationBps','c.maxRemovalDeviationBps', 'testTwelveHourBoundaryAndWeekendFreshFivePercent')
case('immutable pair identity','RamsesOracleGuard.sol',r'old.stockToken != address\(0\)\s*&&','false &&', 'testGuardIdentityCannotChangeToAnotherValidPair','MultiPair.t.sol')
case('forfeiture config only','RamsesBoostedVault.sol',r'(function setAllowRewardForfeiture[\s\S]*?onlyRole\()CONFIG_ROLE',r'\1KEEPER_ROLE','testForfeitureOnlyConfigAndStillOracleGated')
case('RAM config only','RamsesBoostedVault.sol',r'(function forwardExternalReward[\s\S]*?onlyRole\()CONFIG_ROLE',r'\1KEEPER_ROLE','testKeeperCannotForwardOrConfigureAndGuardianOnlyPauses')
case('deployer initialization','RamsesBoostedVault.sol',r'if \(msg.sender != _deployer\)\s*\{?\s*revert InvalidConfiguration\(\);\s*\}?','', 'testVaultInitializationCannotBeStolenOrRepeated')
case('vault harvest reentrancy','RamsesBoostedVault.sol',r'(function harvest\([^\n]*\) external onlyRole\(KEEPER_ROLE\)) nonReentrant',r'\1', 'testVaultReentrancyFromAuthorizedKeeperCallbackRollsBack')
case('harvest before recenter cap','RamsesBoostedVault.sol',r'        _receiveRewards\(pairId, config, pairLedger, deadline, true\);','', 'testHarvestCapStopsRecenterBeforeOldNftBurn')
case('deposit pair cap','RamsesBoostedVault.sol',r'        _enforcePairCap\(pairId, config, pairLedger\);','', 'testPairCapAndDeadline')
case('aggregate cap','RamsesBoostedVault.sol',r'if \(aggregateCap != 0 && aggregateUsdgPrincipal\[config.usdg\] > aggregateCap\)','if (false)', 'testSharedUsdgAggregateCapAcrossPairs','MultiPair.t.sol')
case('deadline upper bound','RamsesBoostedVault.sol',r'if \(delay > config.maxDeadlineDelay\)','if (false)', 'testPairCapAndDeadline')
case('emergency fee reserve cut','RamsesBoostedVault.sol',r'        _collectFees\(pairId, config, _ledger\[pairId\], deadline\);','', 'testEmergencyCollectsFeesWithReserveCut')
case('pro rata stock loss','libraries/RamsesAccountingLib.sol',r'l.stockPrincipal = Math.mulDiv\(l.stockPrincipal, gross, benchmark\);','l.stockPrincipal = l.stockPrincipal;', 'testCheckpointSharesLossAtEqualPercentage','VaultV3Compatibility.t.sol')
case('native sides covered gain','libraries/RamsesAccountingLib.sol',r'stockAssets >= l.stockPrincipal && usdgAssets >= l.usdgPrincipal','stockAssets >= l.stockPrincipal || usdgAssets >= l.usdgPrincipal', 'testCompositionDoesNotWriteOffSolventClaimsWhenSwapsPaused','VaultV3Recovery.t.sol')
case('oracle priced accounting','libraries/RamsesAccountingLib.sol',r'(function checkpoint[\s\S]*?)p.adapter.positionStateAt\(p.id, p.ref\)',r'\1p.adapter.positionState(p.id)', 'testSkewedPoolCannotManufactureLossAtCheckpoint','VaultV3Compatibility.t.sol')
case('reserve fee cut','libraries/RamsesRewardLib.sol',r'uint256 sr = Math.mulDiv\(stock, c.reserveFeeBps, 10_000\);','uint256 sr = 0;', 'testNativeRewardsReserveCutRamZeroNavAndExplicitForward')
case('native reward principal','libraries/RamsesRewardLib.sol',r'l.stockPrincipal \+= stock - sr;','', 'testNativeRewardsReserveCutRamZeroNavAndExplicitForward')
case('RAM zero NAV','libraries/RamsesRewardLib.sol',r'externalRewards\[paid\[i\]\] \+= amounts\[i\];','externalRewards[paid[i]] += amounts[i]; l.stockPrincipal += amounts[i];', 'testNativeRewardsReserveCutRamZeroNavAndExplicitForward')
case('swap minimum output','libraries/RamsesSwapLib.sol',r'output < minOut\s*\|\|','false ||', 'testWithdrawalSettlementMinimumAndAtomicity')
case('swap callback authority','libraries/RamsesSwapLib.sol',r'if \(msg.sender != state.pool \|\| state.pool == address\(0\)\) revert InvalidCallback\(\);','', 'testActiveSwapRejectsCallbackFromWrongCaller')
case('swap vault authority','RamsesVaultAdapter.sol',r'(function swapExactInput[\s\S]*?external\s*)onlyVault',r'\1', 'testSettlementAdapterOnlyVault')
case('deploy loss bound','libraries/RamsesRangeLib.sol',r'if \(gained \+ Math.mulDiv\(usedValue, maxLossBps, BPS\) < usedValue\)','if (false)', 'testLossBoundRejectsOverpricedLiquidity','RangeLibDeploy.t.sol')
case('separate deploy policy on recenter','libraries/RamsesRangeLib.sol',r'uint16\(Math.min\(policy.maxLossBps, maxDeployLossBps\)\)','policy.maxLossBps', 'testRecenterAlsoEnforcesSeparateDeployLossPolicy')
case('outer recenter loss bound','libraries/RamsesRangeLib.sol',r'if \(valueAfter \+ Math.mulDiv\(valueBefore, policy.maxLossBps, BPS\) < valueBefore\)','if (false)', 'testOuterRecenterLossBoundRejectsLostPrincipal','RangeLibDeploy.t.sol')
case('recenter cooldown','libraries/RamsesRangeLib.sol',r'state.lastRecenter != 0\s*&&','false &&', 'testCooldownAndClearCannotResetHistory')
case('history across clear','RamsesBoostedVault.sol',r'rangeState\[pairId\].initialized = false;','delete rangeState[pairId];', 'testCooldownAndClearCannotResetHistory')
case('rolling recenter budget','libraries/RamsesRangeLib.sol',r'        _consumeBudget\(state, policy.maxPerDay\);','', 'testRollingBudgetAndNotNeeded')
case('only named dust swallowed','libraries/RamsesRangeLib.sol',r'if \(reason.length == 4 && bytes4\(reason\) == InsufficientLiquidity.selector\)','if (true)', 'testUnexpectedAdapterFailureBubblesEvenWhenSoft','RangeLibDeploy.t.sol')
case('deploy usage delta','libraries/RamsesRangeLib.sol',r'if \(\s*IERC20\(config.stockToken\).balanceOf\(address\(this\)\) \+ stockUsed != before_.stockBalance[\s\S]*?revert BalanceDeltaMismatch\(\);','', 'testReportedUsageMustMatchBalanceDelta','RangeLibDeploy.t.sol')
case('deploy approval revocation','libraries/RamsesRangeLib.sol',r'        IERC20\(config.stockToken\).forceApprove\(address\(c.adapter\), 0\);','', 'testAdapterRefusalLeavesAssetsIdleAndApprovalsRevokedWhenSoft','RangeLibDeploy.t.sol')
case('reserve receipt delta','libraries/RamsesSettlementLib.sol',r'if \(reportedCovered != covered\) revert BalanceDeltaMismatch\(\);','', 'testReserveReportCannotOvercreditObservedVaultBalance','VaultV3Compatibility.t.sol')
case('guardian one way pause','RamsesBoostedVault.sol',r'if \(!allocationPaused \|\| !swapsPaused \|\| \(config.emergencyMode && !emergencyMode\)\)','if (false)', 'testGuardianCanPauseButCannotUnpause','VaultV3Compatibility.t.sol')

case('pending rewards isolated from exits','RamsesBoostedVault.sol',r'(function _checkpointPair[\s\S]*?)(        pnlUSDG = RamsesAccountingLib.checkpoint)',r'\1        _receiveRewards(pairId, config, pairLedger, deadline, false);\n\2', 'testToxicPendingRamCannotBrickPrincipalSettlement')

case('allocation cap includes unrecognized assets','libraries/RamsesAccountingLib.sol',r'Math.max\(assets, claims\) > c.maxPairValueUSDG','claims > c.maxPairValueUSDG', 'testAllocationCapIncludesUnrecognizedCompositionSurplus','VaultV3Compatibility.t.sol')

case('stock oracle pause','RamsesOracleGuard.sol',r'if \(IPausableStock\(c.stockToken\).oraclePaused\(\)\)','if (false)', 'testTokenOraclePauseBlocksAllocationAndRemoval')

p=argparse.ArgumentParser(); group=p.add_mutually_exclusive_group(); group.add_argument('--only',action='append'); group.add_argument('--batch',type=int,help='One-based batch of up to three cases'); p.add_argument('--list',action='store_true'); args=p.parse_args()
if args.list:
    print('\n'.join(c[0] for c in cases)); raise SystemExit
if args.batch is not None:
    if args.batch < 1 or args.batch > (len(cases)+2)//3: p.error('Batch out of range')
    args.only=[c[0] for c in cases[(args.batch-1)*3:args.batch*3]]
selected=[c for c in cases if args.only and c[0] in args.only]
if not selected or len(selected)>3 or len(selected)!=len(args.only): p.error('Select one to three exact names with --only')
reportpath=ROOT/'research/mutations.json'
report=json.loads(reportpath.read_text()) if reportpath.exists() else []
start=time.monotonic()
for name,path,pattern,replacement,test,suite in selected:
    original=path.read_text(); changed,n=re.subn(pattern,replacement,original,count=1)
    if n!=1: raise RuntimeError('Mutation did not match: '+name)
    testpath=ROOT/'test'/suite
    # Skip unrelated test compilation. Imported fixtures still compile normally.
    skips=sorted({x.name for x in (ROOT/'test').rglob('*.t.sol') if x.name != suite})
    cmd=['forge','test','--threads','1','--match-path',str(testpath),'--match-test',test,'-vv']
    for skip in skips: cmd += ['--skip',skip]
    log=''; code=124; killed=False
    timeout=min(590,590-(time.monotonic()-start))
    if timeout<30: raise RuntimeError('Batch nearing ten-minute limit; rerun remaining selections separately')
    try:
        path.write_text(changed)
        child=subprocess.Popen(cmd,env={**os.environ,'FOUNDRY_PROFILE':'ramses'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        try: log,_=child.communicate(timeout=timeout); code=child.returncode
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL); log,_=child.communicate(); log+='\nSTOPPED: ten-minute limit\n'
        killed=code!=0 and '[FAIL:' in log and 'Compiler run failed' not in log and code!=124
    finally:
        path.write_text(original)
        entry={'mutation':'phase2: '+name,'test':test,'suite':suite,'killed':killed,'returncode':code,'source_sha256':hashlib.sha256(original.encode()).hexdigest(),'command':cmd}
        report=[r for r in report if r['mutation']!=entry['mutation']]+[entry]
        reportpath.write_text(json.dumps(report,indent=2)+'\n')
        slug=re.sub('[^a-z0-9]+','-',name)
        (ROOT/f'research/mutation-phase2-{slug}.log').write_text(log)
    print(name, 'KILLED' if killed else 'SURVIVED/INVALID',flush=True)
    if not killed: raise RuntimeError(log[-4000:])
