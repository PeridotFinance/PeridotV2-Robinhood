# Blackhole WAVAX/USDC concentrated pool versus Pharaoh: facts at block 99924388

Pin: END = block 99,924,388 (2026-11-10T09:12:09Z, hash `0x6b4ae5c8e69d8e7bb381910a28ea9d61af1edab6bafabb8b57ec6aeab9c8323f`), START = block 91,716,610 (2026-08-01T00:00:01Z). State reads are archive `eth_call`s on api.avax.network at END (or START-1 = 91716609 where stated); logs from avalanche-c-chain-rpc.publicnode.com. Every call with its raw result is in `state_at_end.json` (`calls`), every quote in `depth_at_end.json`, every receipt decode in `traces.json`, the checks in `checks.json`. Tags: VERIFIED-ON-CHAIN = read from the chain here (call, event or verified source code on Routescan); DOCUMENTED-ONLY = stated by the protocol's docs, not re-derived; NOT FOUND = looked for, not there.

## A. Pool and contracts

| Fact | Value | Tag | Evidence |
|---|---|---|---|
| Pool type | Algebra Integral pool (`AlgebraPool`, solc 0.8.20), not Uniswap-v3 style: `globalState()` answers, `slot0()` reverts; events `SwapFee`, `BurnFee`, `Plugin`, `CommunityFee` | VERIFIED-ON-CHAIN | verified source of `0x41100C6D2c6920B10d12Cd8D59c8A9AA2eF56fC7` on Routescan; `globalState()`, `slot0()` at END |
| Exact Algebra version | not exposed (no `version()` on the pool); the source has the plugin-fee swap path (`SwapFee`, `BurnFee`, `getPluginFeePending()`), which I read as the Integral core 1.2 line: an inference from the source, not a chain read | NOT FOUND (version string) | verified source |
| WAVAX/USDC pools on Blackhole | 4 Algebra pools (one per custom deployer below), no default-deployer pool, no classic vAMM/sAMM pair | VERIFIED-ON-CHAIN | `AlgebraFactory.customPoolByPair(deployer, WAVAX, USDC)` for all 9 documented deployers; factory `CustomPool` events with token topics (4 logs, Routescan); `poolByPair` = 0x0; `PairFactory.getPair(WAVAX, USDC, false/true)` = 0x0 |
| - v1_ts200 (dataset pool) | `0x41100C6D2c6920B10d12Cd8D59c8A9AA2eF56fC7`: tick spacing 200, fee 500 ppm, 50,783.9 WAVAX + 47,951 USDC = $473,984; gauge `0x3ADE52f9779c07471F4B6d5997444C3c2124C1c0` alive = True | VERIFIED-ON-CHAIN | `balanceOf`, `globalState`, `tickSpacing`, `GaugeManager.gauges/isAlive` at END |
| - v1_ts1 | `0xA02Ec3Ba8d17887567672b2CDCAF525534636Ea0`: tick spacing 1, fee 10000 ppm, 345.6 WAVAX + 194 USDC = $3,109; gauge `0x19D860B389260f3ff31ca77B1c91C92f2236429B` alive = False | VERIFIED-ON-CHAIN | `balanceOf`, `globalState`, `tickSpacing`, `GaugeManager.gauges/isAlive` at END |
| - v1_ts50 | `0x668Aa7AEfa8512416Fc6244afBE5129200277A69`: tick spacing 50, fee 7000 ppm, 561.3 WAVAX + 449 USDC = $5,184; gauge `0x38aa1b1C65531fde294D2aDa1Af94C9d3Fb30e54` alive = True | VERIFIED-ON-CHAIN | `balanceOf`, `globalState`, `tickSpacing`, `GaugeManager.gauges/isAlive` at END |
| - v2_ts1 | `0x7789fA308147Eae364e3BCfa35351F2b9E0bd012`: tick spacing 1, fee 500 ppm, 2,104.8 WAVAX + 14,982 USDC = $32,654; gauge `0x885bbeD6F89Bd617BDc29D75722D3a19010b4184` alive = True | VERIFIED-ON-CHAIN | `balanceOf`, `globalState`, `tickSpacing`, `GaugeManager.gauges/isAlive` at END |
| Dataset pool (largest balances) | `0x41100C6D2c6920B10d12Cd8D59c8A9AA2eF56fC7` | VERIFIED-ON-CHAIN | table rows above |
| Token order, decimals | token0 = WAVAX (18), token1 = USDC (6) | VERIFIED-ON-CHAIN | `token0()`, `token1()`, `decimals()` |
| Tick spacing | 200 (TickSpacing events since creation: [150, 200]) | VERIFIED-ON-CHAIN | `tickSpacing()`; events in `blackhole_wavax_usdc_fee_events.csv.gz` |
| Factory / deployer | factory `0x512eb749541B7cf294be882D636218c84a5e9E5F`; custom pool deployer (tick spacing 200, legacy 'V1' set) `0x5D433A94A4a2aA8f9AA34D8D15692Dc2E9960584` | VERIFIED-ON-CHAIN | `pool.factory()`; `factory.customPoolByPair`; `AlgebraPoolApiStorage.pairToDeployer(pool)` = `0x5D433A94A4a2aA8f9AA34D8D15692Dc2E9960584` |
| Created / initialised | factory `CustomPool` event at block 65,228,165; `Initialize(price=350220741689374829123515, tick=-246598)` at block 65,228,165 (2025-07-10T11:02:14Z) | VERIFIED-ON-CHAIN | Routescan log; pool log |
| Fee mechanism | static, administrator-set (no dynamic-fee plugin): plugin config = 2 = ['AFTER_SWAP'] (neither BEFORE_SWAP 0x01 nor DYNAMIC_FEE 0x80 is set: the pool does not call the plugin before a swap, so overrideFee = pluginFee = 0 and the fee is `globalState.lastFee`, which only the administrator's `setFee` changes); `fee()` = 500 ppm at END and 500 ppm at START-1; 9 Fee events since creation (5000 ppm from block 65,228,165 (2025-07-10T11:02:14Z); 500 ppm from block 65,282,749 (2025-07-11T12:53:35Z); 600 ppm from block 66,147,026 (2025-07-27T14:09:13Z); 700 ppm from block 66,241,855 (2025-07-29T11:24:13Z); 500 ppm from block 66,506,040 (2025-08-03T16:55:25Z); 700 ppm from block 66,616,014 (2025-08-05T20:02:42Z); 500 ppm from block 66,814,426 (2025-08-09T16:28:29Z); 700 ppm from block 67,417,028 (2025-08-21T13:08:33Z); 500 ppm from block 67,621,544 (2025-08-25T13:28:37Z)); Fee events in the window: 0 | VERIFIED-ON-CHAIN | `globalState()`, `fee()`; `Fee(uint16)` events; `SwapFee(overrideFee, pluginFee)` = (0, 0) on 808,773 of 808,773 window swaps |
| Docs on the fee | 'Concentrated Pools ... Fee Structure: Static fee model' | DOCUMENTED-ONLY | https://docs.blackhole.xyz/technical-amm-documentation |
| Pool set | the docs describe two CL sets, 'JIT-protected pools (V2)' and 'Legacy pools (V1)', each with its own NFPM and farming contracts; the dataset pool's deployer is in the docs' legacy list | DOCUMENTED-ONLY | https://docs.blackhole.xyz/technical-amm-documentation |
| No JIT / burn fee on the dataset pool | plugin config 2 has no BEFORE_POSITION_MODIFY flag; `BurnFee(pluginFee)` was non-zero on 0 burns of the window; Flash events 0, Skim events 0 | VERIFIED-ON-CHAIN | `globalState()`; pool events START..END |
| State at END | sqrtPriceX96 229476327054149863882010, tick -255054, price 8.389124 USDC per WAVAX, active liquidity 722133245316243265 | VERIFIED-ON-CHAIN | `globalState()`, `liquidity()` |
| Balances at END | 50,783.945 WAVAX + 47,951.12 USDC = $473,984 at the pool price | VERIFIED-ON-CHAIN | `balanceOf(pool)` on both tokens |
| Position manager (this pool's set) | `0x3fED017EC0f5517Cdf2E8a9a4156c64d74252146`, `factory()` = the Algebra factory, `farmingCenter()` = `0xa47Ad2C95FaE476a73b85A355A5855aDb4b3A449` | VERIFIED-ON-CHAIN | calls; gauge.nonfungiblePositionManager() = `0x3fED017EC0f5517Cdf2E8a9a4156c64d74252146` |
| Quoter / swap router | QuoterV2 `0x3e182bcf14Be6142b9217847ec1112e3c39Eb689`, SwapRouter `0xaBfc48e8BED7b26762745f3139555F320119709d` (both `factory()` = the Algebra factory); RouterV2 `0x9EED160D7D8253DeC1A2A512e504DE5E7ff3C111` (classic factory `0xfE926062Fb99CA5653080d6C14fE945Ad68c265C`) | VERIFIED-ON-CHAIN | `factory()` calls; quoter reproduces the pool price (section E) |
| Gauge manager / voter / minter / BLACK | GaugeManager `0x59aa177312Ff6Bdf39C8Af6F46dAe217bf76CBf6` (proxy, implementation `0x66c6650a106e82FC40824077fA501D6f28974091`), voter `0xE30D0C8532721551a51a9FeC7FB233759964d9e3`, minter `0xAcc34Ad51457930989fB5050C2Dce6339F06479B`, BLACK `0xcd94a87696FAC69Edae3a70fE5725307Ae1c43f6` (BLACKHOLE, 18 decimals) | VERIFIED-ON-CHAIN | `GaugeManager.voter()`, `minter()`, EIP-1967 slot; addresses as in https://docs.blackhole.xyz/technical-amm-documentation |
| Docs versus chain | the docs' 'AlgebraPool' address `0xA02Ec3Ba...` is the tick-spacing-1 WAVAX/USDC pool; the docs' 'GaugeManager (New Implementation)' `0x93C601C31145185787dAb3617D6B419894dC0B8f` is not the implementation in the proxy slot at END (`0x66c6650a106e82FC40824077fA501D6f28974091`) | VERIFIED-ON-CHAIN | `customPoolByPair`; `eth_getStorageAt` |

## B. Share of the swap fee that reaches liquidity providers

| Fact | Value | Tag | Evidence |
|---|---|---|---|
| Community fee parameter | `globalState().communityFee` = 1000 of 1000 at END and 1000 of 1000 at START-1 (unit: `Constants.COMMUNITY_FEE_DENOMINATOR = 1e3`) | VERIFIED-ON-CHAIN | `globalState()` at both blocks; verified source `Constants.sol` |
| Community fee history | 0/1000 from block 65,228,165 (2025-07-10T11:02:14Z); 1000/1000 from block 65,228,209 (2025-07-10T11:03:30Z); CommunityFee events in the window: 0 | VERIFIED-ON-CHAIN | `CommunityFee(uint16)` events from creation, `blackhole_wavax_usdc_fee_events.csv.gz` |
| What the pool does with a swap fee | per step: `delta = feeAmount * communityFee / 1000` is removed first and only the remainder is added to `totalFeeGrowth`; with 1000/1000 the remainder is 0 | VERIFIED-ON-CHAIN | verified source `SwapCalculation._calculateSwap` |
| Window accounting, WAVAX | gross swap fee (sum of input x fee) 8,799.4987 WAVAX; community fee that left the swaps (vault balance change 306.8661 + vault withdrawals 8,492.6186 + pool pending change 0.014011) = 100.0000 % of it | VERIFIED-ON-CHAIN | `checks.json` replay: Swap events x fee; `balanceOf(vault)` at START-1 and END; vault `TokensWithdrawal` / `AlgebraTokensWithdrawal` events; `getCommunityFeePending()` |
| Fee growth to in-pool liquidity, WAVAX | `totalFeeGrowth0Token` grew by 359487692000076584391326344671139759 (X128) between START-1 and END; reproduced exactly from the 1,633 `ExcessTokens` events alone = True; those events total 0.001483 WAVAX = 0.00002 % of the gross swap fee | VERIFIED-ON-CHAIN | `totalFeeGrowth*Token()` at both blocks; `ExcessTokens(uint256,uint256)` events with the replayed active liquidity (`ReservesManager._updateReserves` books tokens sent to the pool outside a swap as fee growth) |
| Window accounting, USDC | gross swap fee (sum of input x fee) 76,444.7094 USDC; community fee that left the swaps (vault balance change 2,875.0303 + vault withdrawals 73,569.8647 + pool pending change 0.000000) = 100.0002 % of it (the excess over 100 % is the per-swap rounding of the fee, at most one base unit of USDC a swap, which sum(input x fee) does not carry) | VERIFIED-ON-CHAIN | `checks.json` replay: Swap events x fee; `balanceOf(vault)` at START-1 and END; vault `TokensWithdrawal` / `AlgebraTokensWithdrawal` events; `getCommunityFeePending()` |
| Fee growth to in-pool liquidity, USDC | `totalFeeGrowth1Token` grew by 2943710714862749361379670501 (X128) between START-1 and END; reproduced exactly from the 1,633 `ExcessTokens` events alone = True; those events total 11.177840 USDC = 0.01462 % of the gross swap fee | VERIFIED-ON-CHAIN | `totalFeeGrowth*Token()` at both blocks; `ExcessTokens(uint256,uint256)` events with the replayed active liquidity (`ReservesManager._updateReserves` books tokens sent to the pool outside a swap as fee growth) |
| (i) Position NOT staked (not entered into farming) | 0 % of swap fees (only the `ExcessTokens` dust above, pro rata to in-range liquidity); no emissions | VERIFIED-ON-CHAIN | rows above; the farming virtual pool only counts positions entered through `FarmingCenter.enterFarming` (source: `AlgebraEternalFarming.enterFarming` is `onlyFarmingCenter`) |
| (ii) Position staked (entered into farming, normally by `GaugeCL.deposit`) | 0 % of swap fees as well (same pool-level fee growth); BLACK emissions through Algebra eternal farming | VERIFIED-ON-CHAIN | rows above and section C |
| Docs statement | CL pools: 'Unstaked positions do not earn rewards since all fees go towards the gauge'; 'Staked positions earn BLACK emissions from the gauge'; 'Unstaked: Receive neither fees nor emissions' | DOCUMENTED-ONLY | https://docs.blackhole.xyz/protocol-design/rewards ; https://docs.blackhole.xyz/technical-amm-documentation |
| Where the fee goes: vault | pool `communityVault()` = `0xA35df874df4b412F845c1236C8Cca0FFcdb08c8B` (AlgebraCommunityVault, one per pool); a swap transfers its fee to the vault in the same transaction (sample: 0xe50c0d234db081fd3beb5de86586d1c8d496ac698ea15f3c9e468fd466ec8cae) | VERIFIED-ON-CHAIN | `communityVault()`; receipt in `traces.json` (section 2) |
| Vault settings | `communityFeeReceiver()` = the gauge `0x3ADE52f9779c07471F4B6d5997444C3c2124C1c0`; `algebraFee()` = 20 of 1000 to `algebraFeeReceiver()` `0x6cbd743d9b97DA1855E64893D3226F8eDCa16e76`; same at START-1 (20 of 1000) | VERIFIED-ON-CHAIN | calls at END and START-1 |
| Weekly fee distribution (sample) | tx `0x61ad593ec15a47327fecca53ebf804da690902d35a24e105d87fd1548327593d` (block 99,447,047, to GaugeManager): vault -> Algebra fee receiver 12.0243 WAVAX (2.000 %), vault -> gauge 589.1892 WAVAX (98.000 %), gauge -> internal bribe 589.1892 WAVAX (100.0 % of what the gauge received); USDC likewise | VERIFIED-ON-CHAIN | receipt Transfer logs + `AlgebraTokensWithdrawal`, `TokensWithdrawal`, `ClaimFees`, Bribe `RewardAdded` (`traces.json` section 3); source `GaugeManager._distributeFees`, `GaugeCL._claimFees` |
| Referral ('dibs') cut in the gauge | `GaugeFactoryCL.dibsPercentage()` = 0 of 1000 at END, 0 at START-1 | VERIFIED-ON-CHAIN | calls |
| Vault withdrawals in the window, WAVAX | to the gauge 8,322.7662, to the Algebra fee receiver 169.8524 (2.000 %) | VERIFIED-ON-CHAIN | vault events START..END |
| Vault withdrawals in the window, USDC | to the gauge 72,098.4674, to the Algebra fee receiver 1,471.3973 (2.000 %) | VERIFIED-ON-CHAIN | vault events START..END |
| Gauge -> internal bribe in the window | 14 `ClaimFees` events: 8,322.7662 WAVAX + 72,098.47 USDC to the internal bribe `0x89Ac76981DE552E27083a53CDCc2359c9438E5cf` (claimable by veBLACK voters of the pool) | VERIFIED-ON-CHAIN | `blackhole_gauge_fee_claims.csv.gz`; `GaugeCL._claimFees` -> `Bribe.notifyRewardAmount` |
| How the gauge holds liquidity | it does not hold the NFTs: `NFPM.balanceOf(gauge)` = 0, `balanceOf(FarmingCenter)` = 0, `balanceOf(AlgebraEternalFarming)` = 0. `GaugeCL.deposit(tokenId)` (caller must be the NFT owner, and the gauge must be approved on the NFPM) calls `FarmingCenter.enterFarming`: the NFT stays with its owner, `NFPM.tokenFarmedIn(tokenId)` becomes the FarmingCenter and the position's liquidity is mirrored in an Algebra eternal-farming virtual pool `0x45204AC8f938b44bfb0f19be6d2794EeFBfe08B2` | VERIFIED-ON-CHAIN | sample deposit `0xc13d0185a7e03c1f9bc8b279bfe588ba928ce105121125d5040766203cb6dbc3`: `ownerOf` = depositor after the call, `tokenFarmedIn` 0x0 -> FarmingCenter, `getApproved` = gauge, events `FarmEntered` + `Deposit` (`traces.json` section 6); source `GaugeCL.deposit` |
| Share of liquidity staked | ACTIVE liquidity: virtual pool `currentLiquidity()` / pool `liquidity()` = 99.976 % at END, 99.991 % at START-1 | VERIFIED-ON-CHAIN | calls at both blocks (`GaugeCL.totalSupply()` returns the same `currentLiquidity`) |
| Share of ALL liquidity staked (out-of-range included) | not derived in this pull: it is derivable from the incentive's FarmEntered / FarmEnded events, which were not pulled (the tag here means 'not produced', not 'absent on chain') | NOT FOUND | - |
| Who is staked (sample, not a census) | sample deposit: tokenId 3532437, block 99,924,236, position liquidity 721411111938156855 = 49.97 % of `pool.liquidity()` at the end of that block (1443544357254400120); the NFT was minted in the same transaction and no longer exists at END (`ownerOf` reverts). Its owner `0x087beFDBCF63C7D0D5D96499e66daA8d43Da9790` is a contract (9,736 bytes of code) and made 292 of the 300 gauge `Deposit` events of the last 50,000 blocks | VERIFIED-ON-CHAIN | `FarmEntered` event, `pool.liquidity()` at block 99,924,236, `eth_getCode`, gauge logs (`traces.json` section 6) |

Peridot-style vault NFT (a position NFT held by a vault contract and NOT staked), strictly as the contracts behave: it earns **neither** swap fees (communityFee = 1000/1000 sends every swap fee to the community vault, then 98 % to the gauge's internal bribe for voters and 2 % to the Algebra fee receiver) **nor** emissions (BLACK accrues only to positions entered into Algebra eternal farming). Its only income is the pro-rata `ExcessTokens` fee growth quantified above. Staking does not move the NFT: the owner approves the gauge on the NFPM and calls `GaugeCL.deposit(tokenId)`, which calls `FarmingCenter.enterFarming`; by the verified source `FarmingCenter.enterFarming` itself only requires the caller to be the NFT's owner or approved (after `NFPM.approveForFarming`), so the gauge is the usual entry point, not the only one the code allows (not exercised here). A staked position earns BLACK while in range and still no swap fees. The BLACK is booked to `rewards[NFPM.ownerOf(tokenId)]` in the farming contract and only that address can withdraw it (`AlgebraEternalFarming.claimReward(token, to, amount)` pays out of `rewards[msg.sender]`), so a vault contract would have to make that call itself; `GaugeManager.claimRewards` -> `GaugeCL.getReward` books the reward to the owner but pays nothing out (the one `Harvest` found emitted only `RewardsCollected` and moved no token; `rewards(gauge, BLACK)` = 0 at END).

## C. Emissions

| Fact | Value | Tag | Evidence |
|---|---|---|---|
| Gauge of the pool | `0x3ADE52f9779c07471F4B6d5997444C3c2124C1c0` (GaugeCL), alive; `rewardToken()` = `bonusRewardToken()` = BLACK | VERIFIED-ON-CHAIN | `GaugeManager.gauges(pool)`, `isAlive`, `isCLGauge`; gauge calls |
| Rate and period at END | `rewardRate()` = 7456898475260512229 wei/s = 7.456898 BLACK/s = 4,509,932 BLACK per 7 days; `_periodFinish` (internal, storage slot 7) = 1794441684 = 2026-11-12T00:01:24Z; there is no public `periodFinish()` | VERIFIED-ON-CHAIN | `rewardRate()`; `eth_getStorageAt(gauge, 7)` (layout checked: slot 6 = DURATION, slot 8 = rewardRate) |
| Farming side at END | incentive nonce 3, virtual pool `rewardRates()` = (7456898475260512229, 0), `rewardReserves()` = 1,043,078 BLACK left to stream, bonus rate 0 | VERIFIED-ON-CHAIN | `AlgebraEternalFarming.incentiveKeys(pool)`, `incentives(id)`, virtual pool calls |
| Rate at START-1 | 7.974720 BLACK/s = 4,823,111 BLACK per 7 days (epoch notified 2026-07-30T00:01:16Z: 4,823,111 BLACK) | VERIFIED-ON-CHAIN | `rewardRate()` at START-1; `gauge_epochs.json` |
| Notification event | `GaugeCL.RewardAdded(uint256 reward)` (topic0 `0xde88a922e0d3b88b24e9623efeb464919c6bf9f66857a65e2bfcf2ce87a9433d`), one per weekly `GaugeManager.distribute`, in the same transaction as `GaugeManager.DistributeReward(sender, gauge, amount)` and the farming contract's `RewardsAdded` / `RewardsRatesChanged` | VERIFIED-ON-CHAIN | sample tx `0xfb3b8ec9f6b39edf363b6d06f11d88b51040c83bc27036633069c6b4232448e8` (`traces.json` section 4); 14 rows in `blackhole_gauge_notify.csv.gz` |
| Reward form | liquid BLACK (the ERC-20 `0xcd94a87696FAC69Edae3a70fE5725307Ae1c43f6`, verified contract `Black`); no escrowed or option token appears anywhere in the notify or claim path | VERIFIED-ON-CHAIN | notify tx: BLACK GaugeManager -> gauge -> AlgebraEternalFarming; claim tx `0xfe7748a96b1bfdc248465f419aa3507ed032053796cb94f45db76a4f438e41a3`: `RewardClaimed(rewardAddress = BLACK)` and a BLACK transfer from the farming contract to the claimer; RewardClaimed events of the farming contract in the last 50,000 blocks by reward token: BLACK 414 |
| Docs on the reward form | '$BLACK emissions are distributed to liquidity pools'; 'Staked positions earn BLACK emissions from the gauge' | DOCUMENTED-ONLY | https://docs.blackhole.xyz/protocol-design/rewards ; https://docs.blackhole.xyz/emissions |
| Claim path actually used | claims go to the farming contract directly (`AlgebraEternalFarming.claimReward`, event `RewardClaimed`): 310 such claims by depositors of this gauge in the last 50,000 blocks, against 0 gauge `Harvest` events in four 50,000-block probes. In the one `Harvest` found (tx `0x40b4bd0fef2970938660a8ba89db3f7c008f3abfa12638325aa53c02f355bb27`, block 66403279) the farming contract emitted ['RewardsCollected'] and no token left it: the reward ledger is `rewards[owner][token]`, `claimReward` pays out of `rewards[msg.sender]`, and `rewards(gauge, BLACK)` = 0 at END | VERIFIED-ON-CHAIN | `traces.json` section 5; sources `AlgebraEternalFarming.collectRewards`, `_claimReward`, `FarmingCenter.claimReward`, `GaugeCL.getReward` |
| BLACK/USD price source | classic pair vAMM-USDC/BLACK `0x0D9Fd6dd9b1FF55fB0A9bB0e5f1B6a2D65b741A3` (token0 USDC, token1 BLACK): the deepest BLACK pool quoted in USDC or WAVAX at END (quote side $35,707, vAMM-WAVAX/BLACK $27,914, the two CL pools $143 and $8) and quoted in USD directly | VERIFIED-ON-CHAIN | `balanceOf` of every candidate at END (`state_at_end.json` C_black_pools) |
| BLACK price | 0.001926 USD at the first window swap, 0.002420 USD at the last (2026-11-10T08:54:23Z); 19,302 swaps in `black_pool_swaps.csv.gz`; pair fee 7000 ppm (every `Fees` event = floor(amountIn x 70 / 10000)) | VERIFIED-ON-CHAIN | pair `Sync` / `Swap` / `Fees` events |
| Withholding / anti-sybil rule on claims | no time threshold: `timeThresholdForGauge(gauge)` and four similar getters revert on VoterV3, GaugeManager, GaugeCL, AlgebraEternalFarming and FarmingCenter, and the verified sources of deposit / withdraw / claim have no time condition | NOT FOUND | 25 probe calls (`state_at_end.json` C_claim_threshold_probes); sources |
| Entry condition that does exist | farming incentive `minimalPositionWidth` = 200 ticks (one tick spacing): narrower positions cannot be staked | VERIFIED-ON-CHAIN | `AlgebraEternalFarming.incentives(incentiveId)`; source `_enterFarming` |

Emissions notified to the gauge inside the window (BLACK price = last vAMM-USDC/BLACK swap at or before the notification):

| Epoch start (UTC) | Notified (UTC) | Block | BLACK | BLACK/USD | USD at notification |
|---|---|---|---|---|---|
| 2026-08-06 | 2026-08-06T00:01:15Z | 92,124,343 | 5,116,245 | 0.001860 | 9,514 |
| 2026-08-13 | 2026-08-13T00:01:14Z | 92,673,067 | 4,905,330 | 0.001706 | 8,368 |
| 2026-08-20 | 2026-08-20T00:01:24Z | 93,220,468 | 4,485,507 | 0.001683 | 7,550 |
| 2026-08-27 | 2026-08-27T00:01:19Z | 93,766,318 | 4,758,801 | 0.002199 | 10,464 |
| 2026-09-03 | 2026-09-03T00:01:16Z | 94,322,917 | 4,594,096 | 0.002253 | 10,349 |
| 2026-09-10 | 2026-09-10T00:01:17Z | 94,894,498 | 4,912,813 | 0.002455 | 12,060 |
| 2026-09-17 | 2026-09-17T00:01:21Z | 95,464,428 | 5,035,634 | 0.002124 | 10,697 |
| 2026-09-24 | 2026-09-24T00:01:24Z | 96,014,364 | 5,213,796 | 0.003999 | 20,852 |
| 2026-10-01 | 2026-10-01T00:01:16Z | 96,488,970 | 4,881,624 | 0.005230 | 25,529 |
| 2026-10-08 | 2026-10-08T00:01:15Z | 96,993,101 | 4,957,018 | 0.005439 | 26,963 |
| 2026-10-15 | 2026-10-15T00:01:13Z | 97,569,684 | 4,640,490 | 0.004082 | 18,942 |
| 2026-10-22 | 2026-10-22T00:01:15Z | 98,191,279 | 4,950,529 | 0.003673 | 18,182 |
| 2026-10-29 | 2026-10-29T00:01:20Z | 98,818,233 | 4,744,703 | 0.002854 | 13,543 |
| 2026-11-05 | 2026-11-05T00:01:24Z | 99,446,965 | 4,509,932 | 0.002785 | 12,562 |
| sum of 14 | | | 67,706,518 | | 205,574 |

The epoch in force at START was notified before the window: 2026-07-30T00:01:16Z (block 91,556,322), 4,823,111 BLACK; it is in `gauge_epochs.json`, not in `blackhole_gauge_notify.csv.gz`.

## E. Depth at END, both pools, by direct quote

Method: each protocol's own `QuoterV2.quoteExactInputSingle` by archive `eth_call` at END, bisected on the input amount until the post-trade pool price is at the target (stop when the bracket is within 1e-9 of the amount). A second call with the target as price limit returns the same output within 6.8e-10 (relative). USD in = input at the pool's own pre-trade price; cost = shortfall of the average execution price against the pre-trade price, fee included. Tag for every row: VERIFIED-ON-CHAIN (`depth_at_end.json`).

**Blackhole** `0x41100C6D2c6920B10d12Cd8D59c8A9AA2eF56fC7`: pre-trade price 8.389124 USDC/WAVAX, fee 500 ppm, active liquidity 722133245316243265, balances 50,784 WAVAX + 47,951 USDC = $473,984; quoter `0x3e182bcf14Be6142b9217847ec1112e3c39Eb689`.

| Trade | Price move reached | Input | USD in | Output | Avg price (USDC/WAVAX) | Cost (bp of size) |
|---|---|---|---|---|---|---|
| sell WAVAX to -1 % | -1.0000 % | 1,256.66 WAVAX | 10,542 | 10,484.20 USDC | 8.342899 | 55.1 |
| sell WAVAX to -5 % | -5.0000 % | 3,593.21 WAVAX | 30,144 | 29,603.72 USDC | 8.238789 | 179.2 |
| sell WAVAX to -10 % | -10.0000 % | 4,205.35 WAVAX | 35,279 | 34,372.34 USDC | 8.173477 | 257.1 |
| buy WAVAX to +1 % | +1.0000 % | 16,468.75 USDC | 16,469 | 1,950.47 WAVAX | 8.443459 | 64.8 |
| buy WAVAX to +5 % | +5.0000 % | 92,125.67 USDC | 92,126 | 10,718.71 WAVAX | 8.594850 | 245.2 |
| buy WAVAX to +10 % | +10.0000 % | 152,199.18 USDC | 152,199 | 17,403.87 WAVAX | 8.745135 | 424.4 |

**Pharaoh** `0xf01449C0bA930B6e2CaCA3DEF3CCBd7a3E589534`: pre-trade price 8.392115 USDC/WAVAX, fee 850 ppm, active liquidity 12405249810747714683, balances 267,382 WAVAX + 896,794 USDC = $3,140,693; quoter `0xB7297301b7CC659BB96D51754643A0Df6eEA2138`.

| Trade | Price move reached | Input | USD in | Output | Avg price (USDC/WAVAX) | Cost (bp of size) |
|---|---|---|---|---|---|---|
| sell WAVAX to -1 % | -1.0000 % | 18,956.34 WAVAX | 159,084 | 158,182.99 USDC | 8.344597 | 56.6 |
| sell WAVAX to -5 % | -5.0000 % | 64,603.48 WAVAX | 542,160 | 529,617.32 USDC | 8.197969 | 231.3 |
| sell WAVAX to -10 % | -10.0000 % | 103,322.50 WAVAX | 867,094 | 832,039.32 USDC | 8.052838 | 404.3 |
| buy WAVAX to +1 % | +1.0000 % | 161,633.19 USDC | 161,633 | 19,155.97 WAVAX | 8.437743 | 54.4 |
| buy WAVAX to +5 % | +5.0000 % | 545,231.57 USDC | 545,232 | 63,536.34 WAVAX | 8.581413 | 225.6 |
| buy WAVAX to +10 % | +10.0000 % | 986,241.08 USDC | 986,241 | 112,403.16 WAVAX | 8.774140 | 455.2 |

| At END | Blackhole | Pharaoh | Tag | Evidence |
|---|---|---|---|---|
| Balances (TVL at pool price) | $473,984 | $3,140,693 | VERIFIED-ON-CHAIN | `balanceOf(pool)` |
| Active liquidity (L) | 722133245316243265 | 12405249810747714683 | VERIFIED-ON-CHAIN | `liquidity()` |
| Swap fee at END | 500 ppm | 850 ppm | VERIFIED-ON-CHAIN | `fee()` |
| Liquidity within +/-1% (USDC out of the sell + WAVAX out of the buy x price) | $26,847 | $318,942 | VERIFIED-ON-CHAIN | quotes |
| Liquidity within +/-5% (USDC out of the sell + WAVAX out of the buy x price) | $119,524 | $1,062,822 | VERIFIED-ON-CHAIN | quotes |
| Liquidity within +/-10% (USDC out of the sell + WAVAX out of the buy x price) | $180,376 | $1,775,340 | VERIFIED-ON-CHAIN | quotes |
| Offline re-derivation (Blackhole) | tick map rebuilt from `blackhole_wavax_usdc_liquidity.csv.gz` reproduces all six quoter amounts within 7.2e-10 (relative) | n/a | VERIFIED-ON-CHAIN | `checks.json` depth_offline_vs_quoter_blackhole |
| Quoter identity | Blackhole QuoterV2 `factory()` = Algebra factory; Pharaoh QuoterV2 `deployer()` = `0x6a4113ed0915bCf5E48e758e8f4cEBFFC07C66f9` whose `RamsesV3Factory()` = the pool's factory, and `factory.getPool(WAVAX, USDC, 10)` = the pool | | VERIFIED-ON-CHAIN | calls; both quoters return the pool's own price for a 1e-6 WAVAX quote |

## Checks run

- Structure: `black_pool_swaps.csv.gz` 19,302 rows, duplicates 0, ascending True; `blackhole_gauge_fee_claims.csv.gz` 69 rows, duplicates 0, ascending True; `blackhole_gauge_notify.csv.gz` 14 rows, duplicates 0, ascending True; `blackhole_wavax_usdc_fee_events.csv.gz` 17 rows, duplicates 0, ascending True; `blackhole_wavax_usdc_liquidity.csv.gz` 3,206,294 rows, duplicates 0, ascending True; `blackhole_wavax_usdc_swaps.csv.gz` 808,773 rows, duplicates 0, ascending True.
- Requested log ranges tile their block range: bh_pool 396 windows / 1,931,807 logs / tiling True; bh_gauge_rf 694 windows / 138 logs / tiling True; bh_black_pair 165 windows / 60,346 logs / tiling True; bh_pool_pre 64 windows / 324,222 logs / tiling True; bh_pool_pre_1 70 windows / 455,350 logs / tiling True; bh_pool_pre_1b 37 windows / 221,661 logs / tiling True; bh_pool_pre_1c 35 windows / 201,428 logs / tiling True; bh_pool_pre_1d 41 windows / 259,515 logs / tiling True; bh_pool_pre_2 72 windows / 401,735 logs / tiling True; bh_pool_pre_2b 25 windows / 161,007 logs / tiling True; bh_pool_pre_2c 19 windows / 114,792 logs / tiling True; bh_pool_pre_2d 29 windows / 170,485 logs / tiling True; bh_pool_pre_3 98 windows / 495,402 logs / tiling True; bh_pool_pre_3b 1 windows / 2,370 logs / tiling True; bh_pool_pre_3c 1 windows / 2,270 logs / tiling True; bh_pool_pre_4 82 windows / 143,631 logs / tiling True; bh_pool_pre_5 82 windows / 11,573 logs / tiling True; bh_pool_pre_6 82 windows / 84,214 logs / tiling True; bh_vault 694 windows / 281 logs / tiling True; bh_gm_distribute 694 windows / 69 logs / tiling True; pre-window segments tile creation..START-1 = True.
- Re-request bh_pool_pre (all segments): publicnode with another split identical in 5/5 ranges; Routescan identical in 5/5 ranges; records compared: 4,759.
- Re-request bh_gauge_rf: publicnode with another split identical in 5/5 ranges; Routescan identical in 5/5 ranges; records compared: 12.
- Re-request bh_vault: publicnode with another split identical in 5/5 ranges; Routescan identical in 5/5 ranges; records compared: 48.
- Re-request bh_gm_distribute: publicnode with another split identical in 5/5 ranges; Routescan identical in 5/5 ranges; records compared: 6.
- Re-request bh_black_pair: publicnode with another split identical in 5/5 ranges; Routescan identical in 5/5 ranges; records compared: 1,784.
- Re-request bh_pool: publicnode with another split identical in 5/5 ranges; Routescan identical in 5/5 ranges; records compared: 3,132.
- Last swap (block 99,924,375, log 4) versus state at END: price equal True, tick equal True, liquidity equal True; Mint/Burn rows after it: 0; liquidity equal after applying them: True.
- Liquidity file: replayed active liquidity equals `pool.liquidity()` at START-1 (True) and at END (True), and equals the liquidity field of 808,773 of 808,773 Swap events.
- Fee validation: 802,048 eligible swaps of 808,773; within 1 ppm of `fee_ppm`: 99.1105 %; within 1 ppm or within 2 base units of integer rounding: 100.0000 %; volume-weighted implied fee 500.0000 ppm (WAVAX in) and 500.0012 ppm (USDC in).
- Timestamps: every `ts` is the header timestamp of the log's block (publicnode header batches); block-hash mismatches between logs and headers: pool 0, gauge 0, black 0.
- Swap sign: Swap amounts equal the pool's net token transfers in the receipts of one swap per direction (True, 2 receipts).

