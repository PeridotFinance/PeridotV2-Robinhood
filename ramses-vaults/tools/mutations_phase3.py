#!/usr/bin/env python3
"""One to three bounded mutations per command; never edit imported/frozen source."""
import argparse, hashlib, json, os, pathlib, re, signal, subprocess, time
ROOT=pathlib.Path('ramses-vaults')
CASES=[]
def case(name,path,old,new,test): CASES.append((name,ROOT/'src'/path,old,new,test))
D='lending/RamsesBoostedDelegate.sol'; A='lending/RamsesMarketAccounting.sol'; P='lending/RamsesLendingPriceSource.sol'
case('buffer floor',D,r'buffer_ < 0.5e18 \|\| ', '', 'testRoleBufferFloorAndPauseGates')
case('buffer target',D,r'uint256 targetBuffer = _bufferTarget\(totalAssets\);', 'uint256 targetBuffer = (totalAssets * vaultBufferMantissa) / (2 * MANTISSA_ONE);', 'testBufferBothSidesAndRamsesLP')
case('supply cap',D,r'require\(\s*mintAmount\s*!=\s*0\s*&&\s*super.getCashPrior\(\)\s*\+\s*_vaultAccountedAssets\(\)\s*\+\s*totalBorrows\s*-\s*totalReserves\s*\+\s*mintAmount\s*<=\s*supplyCap,\s*"SUPPLY_CAP"\s*\);', '', 'testBorrowRepayAccrueInterestAndCaps')
case('cap admin',D,r'(function setSupplyCap\(uint256 value\) external \{)\s*if \(msg.sender != admin\) revert OnlyAdmin\(\);',r'\1', 'testRoleBufferFloorAndPauseGates')
case('pause',D,r'if \(vaultPaused \|\| !_vaultConfigured\(\)\) return;', 'if (!_vaultConfigured()) return;', 'testRoleBufferFloorAndPauseGates')
# Remove both pause checks for this mutation: the second check deliberately provides defense in depth.
case('withdrawable cash',D,r'try robinhoodVault.withdrawableAssets\(robinhoodPairId, underlying\)', 'try robinhoodVault.liquidAssets(robinhoodPairId, underlying)', 'testBorrowUsesWithdrawableNotCustody')
case('loss preview',A,r'return gross < benchmark \? Math.mulDiv\(principal, gross, benchmark\) : principal;', 'return principal;', 'testLossPreviewAndCheckpointBeforeCashExit')
case('checkpoint before redeem',D,r'(function redeemUnderlying\(uint256 redeemAmount\)[\s\S]*?)_checkpoint\(true\);',r'\1', 'testLossPreviewAndCheckpointBeforeCashExit')
case('named stale exception',A,r'if \(!allowStale \|\| !stale\(reason\)\)', 'if (!allowStale)', 'testUnknownCheckpointFailureCannotMasqueradeAsStale')
case('fresh price source',P,r'\(uint256 stockPrice, uint256 dollarPrice\) = guard.pricesUSD18\(pair\);', '(uint256 stockPrice, uint256 dollarPrice) = (100e18, 1e18);', 'testLendingSourceItselfFailsClosedForBothMarkets')
case('USDG source units',P,r'return asset == c.stockToken \? stockPrice : dollarPrice;', 'return asset == c.stockToken ? stockPrice : dollarPrice * 1e12;', 'testOracleDecimalsAndIsolation')
case('dead seed',D,r'accountTokens\[DEAD\] \+= DEAD_SHARES;', 'accountTokens[msg.sender] += DEAD_SHARES;', 'testSeedMakesDonationAttackCostlyAndZeroSharesRevert')
case('seed gate',D,r'if \(first\) require\(msg.sender == admin, "TIMELOCK_SEED_REQUIRED"\);', '', 'testFreshMarketRequiresTimelockSeed')
case('rebalance role',D,r'function rebalanceVault\(\) external onlyVaultOperator nonReentrant', 'function rebalanceVault() external nonReentrant', 'testRoleBufferFloorAndPauseGates')
case('buffer ceil',D,r'\(deployable \* vaultBufferMantissa \+ MANTISSA_ONE - 1\)\s*/ MANTISSA_ONE', '(deployable * vaultBufferMantissa) / MANTISSA_ONE', 'testOddRawUnitRoundsBufferUp')
case('checkpoint before rebalance',D,r'(function rebalanceVault\(\)[\s\S]*?)_checkpoint\(false\);',r'\1', 'testManualRebalanceCheckpointsBeforeDeposit')
case('operator sync receipt',D,r'if \(requested != 0 && !success\) revert StrategyLiquidityShortfall\(\);', '', 'testOperatorSyncDoesNotReportSilentWithdrawSuccess')
case('buffer setter floor',D,r'value < 0.5e18 \|\| ', '', 'testRoleBufferFloorAndPauseGates')

case('no unbooked gains',A,r'return gross < benchmark \? Math.mulDiv\(principal, gross, benchmark\) : principal;', 'return Math.mulDiv(principal, gross, benchmark);', 'testCompositionSurplusCannotInflateMarketRates')

case('reserve cash protection',D,r'uint256 deployable = totalAssets > totalReserves \? totalAssets - totalReserves : 0;', 'uint256 deployable = totalAssets;', 'testProtocolReserveDonationCannotIncreaseLPExposure')

case('partial emergency cash exit',A,r'if \(c.emergencyMode && allowStale\) return;', '', 'testPartialEmergencyDoesNotBrickLocalCash')

case('cap accrued interest',D,r'accrueInterest\(\); // Cap includes interest accrued through this block.', '', 'testSupplyCapIncludesPendingInterest')

p=argparse.ArgumentParser(); p.add_argument('--list',action='store_true'); p.add_argument('--check-patterns',action='store_true'); p.add_argument('--batch',type=int); p.add_argument('--only',action='append'); args=p.parse_args()
if args.list: print('\n'.join(x[0] for x in CASES)); raise SystemExit
if args.check_patterns:
    for name,path,pattern,_,_ in CASES:
        assert len(re.findall(pattern,path.read_text()))==1,name
    print(f'All {len(CASES)} mutation patterns match the final formatted source.'); raise SystemExit
selected=CASES[(args.batch-1)*3:args.batch*3] if args.batch else [x for x in CASES if args.only and x[0] in args.only]
if not 1<=len(selected)<=3: p.error('Select one to three cases')
start=time.monotonic(); reportpath=ROOT/'research/mutations.json'; report=json.loads(reportpath.read_text())
for name,path,pattern,replacement,test in selected:
    original=path.read_text(); changed,n=re.subn(pattern,replacement,original,count=1); assert n==1,name
    # Pause is checked both at rebalancing and deposit. Disable the pair together.
    if name in {'pause','deposit pause'}:
        changed=changed.replace('if (vaultPaused || !_vaultConfigured()) return;', 'if (!_vaultConfigured()) return;').replace('if (amount == 0 || vaultPaused || !_vaultConfigured()) return;', 'if (amount == 0 || !_vaultConfigured()) return;')
    skips=sorted({x.name for x in (ROOT/'test').rglob('*.t.sol') if x.name!='Lending.t.sol'})
    cmd=['forge','test','--threads','1','--match-path','ramses-vaults/test/Lending.t.sol','--match-test',test,'--skip','script','-vv']
    for skip in skips: cmd += ['--skip',skip]
    code=124; log=''; killed=False
    try:
        path.write_text(changed)
        child=subprocess.Popen(cmd,env={**os.environ,'FOUNDRY_PROFILE':'ramses'},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        try: log,_=child.communicate(timeout=max(1,590-(time.monotonic()-start))); code=child.returncode
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL); log,_=child.communicate(); log+='\nSTOPPED ten-minute limit\n'
        killed=code!=0 and '[FAIL:' in log and code!=124 and 'Compilation failed' not in log
    finally:
        path.write_text(original)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==hashlib.sha256(original.encode()).hexdigest()
        entry={'mutation':'phase3: '+name,'test':test,'killed':killed,'returncode':code,'source_sha256':hashlib.sha256(original.encode()).hexdigest(),'command':cmd}
        report=[r for r in report if r['mutation']!=entry['mutation']]+[entry]; reportpath.write_text(json.dumps(report,indent=2)+'\n')
        (ROOT/('research/mutation-phase3-'+name.replace(' ','-')+'.log')).write_text(log)
    print(name, 'KILLED' if killed else 'SURVIVED/INVALID',flush=True)
    if not killed: raise SystemExit(log[-2500:])
