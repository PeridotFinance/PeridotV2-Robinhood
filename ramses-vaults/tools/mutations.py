#!/usr/bin/env python3
"""Local, serial mutation checks. Always restores the exact reviewed adapter source.
Requires a clean test run first; compiler errors and zero matched tests never count as kills.
"""
import argparse,json,os,pathlib,re,subprocess
parser=argparse.ArgumentParser()
parser.add_argument('--only', action='append', help='Exact mutation name; repeat to select changed safeguards')
args=parser.parse_args()
path=pathlib.Path('ramses-vaults/src/RamsesV3PairedAdapter.sol');original=path.read_text()
cases=[
 ('pool receipt delta',r'        if \(\n            IERC20\(t0\)\.balanceOf\(address\(p.pool\)\) != a.poolStart0[\s\S]*?revert BalanceDeltaMismatch\(\);','', 'test_taxedManagerPaymentRejected'),
 ('burn custody delta',r'        if \(positionManager.balanceOf\(address\(this\)\) \+ 1 != countBefore\) revert InvalidPosition\(\);','', 'test_silentBurnRejected'),
 ('harvest before decrease',r'        _harvestBeforeExit\(id, p\); // Must precede[^\n]*','', 'test_rewardsSeparateAndHarvestBeforeDecreaseAndBurn'),
 ('harvest before burn',r'(_validatePosition\(p\);\n)        _harvestBeforeExit\(id, p\);',r'\1', 'test_rewardsSeparateAndHarvestBeforeDecreaseAndBurn'),
 ('inverse stock price band',r'return \(_scale\(ref, BPS, BPS \+ d\), _scale\(ref, BPS, BPS - d\)\);','return (_scale(ref, BPS-d, BPS), _scale(ref, BPS+d, BPS));','test_inverseRemovalBandFarEndpoint'),
 ('20bp quantisation widening',r'constant QUANTISATION = 20','constant QUANTISATION = 0','test_inverseRemovalBandFarEndpoint'),
 ('fresh removal oracle',r'        if \(oracle.referenceSqrtPriceX96\(id, false\) != ref\) revert InvalidReferencePrice\(\);','', 'test_staleOracleBlocksAddAndRemovalButNotHarvest'),
 ('reward slashing delta',r'            if \(received != expected\[i\] \|\| gaugeBalances\[i\] - gaugeAfter != received\) \{?\s*revert RewardClaimMismatch\(\);\s*\}?','', 'test_silentRewardSlashingRevertsAtomically'),
 ('approval revocation',r'        _revoke\(t0\);\n        _revoke\(t1\);','', 'test_roundTripAndApprovals'),
 ('explicit removal price gate',r'        _checkBand\(p, ref, d\);','', 'test_removalOutsideBandRejectedEvenIfBothMinimumsZero'),
 ('forfeiture default closed',r'if \(!allowRewardForfeiture\[id\]\)', 'if (false)', 'test_forfeitureRevertingHarvestExitsAndBurns'),
 ('forfeiture self-only frame',r'        if \(msg.sender != address\(this\)\) revert NotSelf\(\);', '', 'test_forfeitureFlagAndSelfCallAccess'),
 ('forfeiture vault-only flag',r'(function setAllowRewardForfeiture\(bytes32 id, bool allowed\) external) onlyVault', r'\1', 'test_forfeitureFlagAndSelfCallAccess'),
 ('forfeiture atomic mismatch',r'            if \(received != expected\[i\] \|\| gaugeBalances\[i\] - gaugeAfter != received\) \{?\s*revert RewardClaimMismatch\(\);\s*\}?', '', 'test_forfeitureMismatchRollsBackAllRewards'),
 ('exit reentrancy lock',r'        if \(_entered != 0\) revert ReentrantCall\(\);', '', 'test_reentrantHarvestCannotEnterVaultActions'),
]
if args.only and set(args.only)-{c[0] for c in cases}:parser.error('Unknown mutation name')
report=[]
reportfile=pathlib.Path('ramses-vaults/research/mutations-targeted.json' if args.only else 'ramses-vaults/research/mutations.json')
previous=json.loads(reportfile.read_text()) if args.only and reportfile.exists() else []
try:
 for index,(name,pattern,replacement,test) in enumerate(cases,1):
    if args.only and name not in args.only:continue
    modified,n=re.subn(pattern,replacement,original,count=1)
    if n!=1:raise RuntimeError('Mutation did not match: '+name)
    path.write_text(modified)
    cmd=['forge','test','--threads','1','--match-test',test,'--no-match-path','*/fork/*','-vv']
    p=subprocess.run(cmd,env={**os.environ,'FOUNDRY_PROFILE':'ramses'},capture_output=True,text=True,timeout=600)
    log=p.stdout+p.stderr
    killed=p.returncode!=0 and '[FAIL:' in log and 'Compiler run failed' not in log
    report.append({'mutation':name,'test':test,'killed':killed,'returncode':p.returncode})
    pathlib.Path('ramses-vaults/research/mutation-'+('targeted-' if args.only else '')+str(index)+'.log').write_text(log)
    print(name, 'KILLED' if killed else 'SURVIVED/INVALID',flush=True)
    if not killed:raise RuntimeError(log[-2500:])
finally:
 path.write_text(original)
 updated={r['mutation']:r for r in previous}
 updated.update({r['mutation']:r for r in report})
 reportfile.write_text(json.dumps(list(updated.values()),indent=2))
