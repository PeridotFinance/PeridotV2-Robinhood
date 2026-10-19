// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { CallbackRelay } from "./mocks/Phase2Double.sol";
import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../src/RamsesLossReserve.sol";
import { RamsesVaultAdapter } from "../src/RamsesVaultAdapter.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { RamsesRangeLib } from "../src/libraries/RamsesRangeLib.sol";
import { RamsesSwapLib } from "../src/libraries/RamsesSwapLib.sol";
import { RangePolicy } from "../src/libraries/RangeTypes.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import {
    TokenDouble,
    FactoryVoterDouble,
    NfpDouble,
    GaugeDouble
} from "./mocks/RamsesV3Double.sol";
import { PausableTokenDouble, FeedDouble, SwapPoolDouble } from "./mocks/Phase2Double.sol";

abstract contract Phase2Fixture is Test {
    bytes32 internal constant PAIR = keccak256("NVDA/USDG");
    address internal keeper = address(0x123);
    address internal guardian = address(0x456);
    address internal stockAccount = address(0x789);
    address internal usdgAccount = address(0xabc);
    address internal receiver = address(0xdef);
    PausableTokenDouble internal stock;
    TokenDouble internal usdg;
    TokenDouble internal ram;
    FeedDouble internal feed;
    SwapPoolDouble internal pool;
    FactoryVoterDouble internal factory;
    NfpDouble internal manager;
    GaugeDouble internal gauge;
    RamsesOracleGuard internal guard;
    RamsesVaultAdapter internal adapter;
    RamsesBoostedVault internal vault;
    RamsesLossReserve internal reserve;

    function stockFirst() internal pure virtual returns (bool) {
        return false;
    }

    function setUp() public virtual {
        vm.warp(100 weeks + 1 days);
        PausableTokenDouble si = new PausableTokenDouble("NVDA", 18);
        TokenDouble ui = new TokenDouble("USDG", 6);
        stock = PausableTokenDouble(stockFirst() ? address(0x1000) : address(0x2000));
        usdg = TokenDouble(stockFirst() ? address(0x2000) : address(0x1000));
        vm.etch(address(stock), address(si).code);
        vm.etch(address(usdg), address(ui).code);
        ram = new TokenDouble("RAM", 18);
        feed = new FeedDouble(8, 100e8);
        factory = new FactoryVoterDouble();
        pool = new SwapPoolDouble(
            address(0x1000),
            address(0x2000),
            address(factory),
            stockFirst() ? -230270 : int24(230270)
        );
        manager = new NfpDouble(pool, address(factory));
        pool.setManager(address(manager));
        address[] memory tokens = new address[](3);
        tokens[0] = address(usdg);
        tokens[1] = address(stock);
        tokens[2] = address(ram);
        gauge = new GaugeDouble(pool, manager, tokens);
        manager.setGauge(gauge);
        factory.set(address(pool), address(gauge));
        guard = new RamsesOracleGuard(address(this));
        guard.configurePair(
            PAIR,
            RamsesOracleGuard.FeedConfig(
                address(stock),
                address(usdg),
                IAggregatorV3(address(feed)),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                12 hours,
                300,
                600,
                18,
                6,
                8,
                true
            )
        );
        vault = new RamsesBoostedVault();
        reserve = new RamsesLossReserve();
        adapter = new RamsesVaultAdapter(address(vault), IRamsesNfp(address(manager)), guard);
        reserve.initialize(address(this), address(vault));
        vault.initialize(address(this), keeper, guardian, guard, reserve, adapter);
        reserve.configurePair(
            PAIR,
            RamsesLossReserve.ReserveConfig(
                address(stock), address(usdg), 1000e18, 10000e18, 10000, false, true
            )
        );
        vault.registerPair(
            PAIR,
            config(),
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock),
                address(usdg),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                400,
                300
            )
        );
        vault.setDeployLossBps(PAIR, 10);
        vault.setRangePolicy(PAIR, policy());
        stock.mint(stockAccount, 100e18);
        usdg.mint(usdgAccount, 100000e6);
        stock.mint(address(pool), 100000e18);
        usdg.mint(address(pool), 10000000e6);
        vm.prank(stockAccount);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(usdgAccount);
        usdg.approve(address(vault), type(uint256).max);
    }

    function config() internal view returns (PairConfig memory c) {
        c = PairConfig(
            address(stock),
            address(usdg),
            stockAccount,
            usdgAccount,
            1000e18,
            1000e18,
            1 days,
            0,
            300,
            2000,
            100,
            200,
            18,
            6,
            false,
            false,
            false,
            true
        );
    }

    function policy() internal pure returns (RangePolicy memory) {
        return RangePolicy(true, 2400, 600, 1 hours, 4, 10, type(uint128).max);
    }

    function deposit(uint256 s, uint256 u) internal {
        vm.prank(stockAccount);
        vault.depositForPair(PAIR, address(stock), s);
        vm.prank(usdgAccount);
        vault.depositForPair(PAIR, address(usdg), u);
    }

    function recenter() internal {
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
    }

    function fresh() internal {
        feed.set(feed.answer());
    }

    function shift(int24 ticks) internal {
        pool.setTick(pool.tick() + ticks);
        uint256 quote = VaultMath.quoteAtTick(pool.tick(), 1e18, address(stock), address(usdg));
        feed.set(int256(quote * 100));
    }

    function fundRewards() internal {
        gauge.fund(address(stock), block.timestamp / 1 weeks, 100e18);
        gauge.fund(address(usdg), block.timestamp / 1 weeks, 10000e6);
        gauge.fund(address(ram), block.timestamp / 1 weeks, 10000e18);
    }

    function noApprovals() internal view {
        assertEq(stock.allowance(address(vault), address(adapter)), 0);
        assertEq(usdg.allowance(address(vault), address(adapter)), 0);
        assertEq(stock.allowance(address(adapter), address(manager)), 0);
        assertEq(usdg.allowance(address(adapter), address(manager)), 0);
        assertEq(stock.allowance(address(vault), address(reserve)), 0);
        assertEq(usdg.allowance(address(vault), address(reserve)), 0);
    }
}

contract Phase2Test is Phase2Fixture {
    function testDefaultPolicyAndCaps() public view {
        PairConfig memory c = vault.pairConfig(PAIR);
        assertEq(c.maxPairValueUSDG, 1000e18);
        (
            bool enabled,
            uint16 half,
            uint16 trigger,
            uint32 interval,
            uint8 count,
            uint16 loss,
            uint128 cap
        ) = vault.rangePolicy(PAIR);
        assertTrue(enabled);
        assertEq(half, 2400);
        assertEq(trigger, 600);
        assertEq(interval, 1 hours);
        assertEq(count, 4);
        assertEq(loss, 10);
        assertEq(cap, type(uint128).max);
        assertEq(vault.deployLossBps(PAIR), 10);
        assertFalse(adapter.allowRewardForfeiture(PAIR));
    }

    function testMintRecenterBothDirectionsAndEmergency() public {
        deposit(2e18, 200e6);
        recenter();
        uint256 first = adapter.positionState(PAIR).tokenId;
        assertEq(manager.ownerOf(first), address(adapter));
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(700);
        recenter();
        assertGt(adapter.positionState(PAIR).tokenId, first);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(-1400);
        recenter();
        uint128 liquidity = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, liquidity, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        assertTrue(vault.pairConfig(PAIR).emergencyMode);
        noApprovals();
    }

    function testOneSidedInventoryFinishesIdle() public {
        deposit(2e18, 0);
        recenter();
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        assertEq(vault.ledger(PAIR).stockIdle, 2e18);
    }

    function testOutOfRangePositionRecentersToOneSidedIdleThenSettles() public {
        deposit(2e18, 200e6);
        recenter();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(3000);
        assertEq(adapter.positionState(PAIR).usdgAmount, 0);
        recenter();
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
        assertGt(vault.ledger(PAIR).stockIdle, 0);
        uint256 claim = vault.ledger(PAIR).usdgPrincipal;
        vm.prank(usdgAccount);
        (uint256 returned,) =
            vault.withdrawForSide(PAIR, address(usdg), claim, receiver, block.timestamp);
        assertGt(returned, 0);
        noApprovals();
    }

    function testFaithfulPoolCheckpointSharesLossProRata() public {
        deposit(2e18, 200e6);
        recenter();
        PairLedger memory before_ = vault.ledger(PAIR);
        shift(700);
        vm.prank(keeper);
        int256 pnl = vault.checkpoint(PAIR, block.timestamp);
        PairLedger memory after_ = vault.ledger(PAIR);
        assertLt(pnl, 0);
        assertLt(after_.stockPrincipal, before_.stockPrincipal);
        assertLt(after_.usdgPrincipal, before_.usdgPrincipal);
        assertApproxEqAbs(
            after_.stockPrincipal * before_.usdgPrincipal,
            after_.usdgPrincipal * before_.stockPrincipal,
            2e18
        );
        assertEq(vault.aggregateUsdgPrincipal(address(usdg)), after_.usdgPrincipal);
    }

    function testFaithfulPoolFeesCreditNativeClaimsWithReserveCut() public {
        deposit(2e18, 200e6);
        recenter();
        PairLedger memory before_ = vault.ledger(PAIR);
        pool.accrueFees(100e6, 1e18);
        vm.prank(keeper);
        (uint256 s, uint256 u) = vault.collectFees(PAIR, block.timestamp);
        assertGt(s, 0);
        assertGt(u, 0);
        assertEq(vault.ledger(PAIR).stockPrincipal, before_.stockPrincipal + s - s * 2000 / 10000);
        assertEq(vault.ledger(PAIR).usdgPrincipal, before_.usdgPrincipal + u - u * 2000 / 10000);
        assertEq(reserve.available(PAIR, address(stock)), s * 2000 / 10000);
        assertEq(reserve.available(PAIR, address(usdg)), u * 2000 / 10000);
        noApprovals();
    }

    function testDustFinishesIdle() public {
        deposit(1, 1);
        recenter();
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        noApprovals();
    }

    function testCooldownAndClearCannotResetHistory() public {
        deposit(2e18, 200e6);
        recenter();
        uint128 liquidity = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, liquidity, block.timestamp);
        vm.prank(keeper);
        vault.burnEmptyPosition(PAIR, block.timestamp);
        vm.prank(keeper);
        vault.clearRange(PAIR);
        vault.setPairPause(PAIR, false, false, false);
        vm.prank(keeper);
        vm.expectRevert(RamsesRangeLib.RecenterCooldown.selector);
        vault.recenter(PAIR, block.timestamp);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        fresh();
        recenter();
    }

    function testRollingBudgetAndNotNeeded() public {
        deposit(2e18, 200e6);
        recenter();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        fresh();
        vm.prank(keeper);
        vm.expectRevert(RamsesRangeLib.RecenterNotNeeded.selector);
        vault.recenter(PAIR, block.timestamp);
        for (uint256 i; i < 3; ++i) {
            shift(i % 2 == 0 ? int24(700) : int24(-700));
            recenter();
            vm.warp(vm.getBlockTimestamp() + 1 hours);
        }
        shift(-700);
        vm.prank(keeper);
        vm.expectRevert(RamsesRangeLib.RecenterRateLimited.selector);
        vault.recenter(PAIR, block.timestamp);
        vm.warp(vm.getBlockTimestamp() + 1 days);
        fresh();
        recenter();
    }

    function testNativeRewardsReserveCutRamZeroNavAndExplicitForward() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        pool._advancePeriod();
        uint256 id = adapter.positionState(PAIR).tokenId;
        uint256 s = gauge.earned(address(stock), id);
        uint256 u = gauge.earned(address(usdg), id);
        uint256 r = gauge.earned(address(ram), id);
        assertGt(s, 0);
        assertGt(u, 0);
        assertGt(r, 0);
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        PairLedger memory before_ = vault.ledger(PAIR);
        vm.prank(keeper);
        vault.harvest(PAIR, block.timestamp);
        PairLedger memory after_ = vault.ledger(PAIR);
        assertEq(after_.stockPrincipal, before_.stockPrincipal + s - s * 2000 / 10000);
        assertEq(after_.usdgPrincipal, before_.usdgPrincipal + u - u * 2000 / 10000);
        assertEq(after_.stockIdle, before_.stockIdle + s - s * 2000 / 10000);
        assertEq(after_.usdgIdle, before_.usdgIdle + u - u * 2000 / 10000);
        assertEq(reserve.available(PAIR, address(stock)), s * 2000 / 10000);
        assertEq(reserve.available(PAIR, address(usdg)), u * 2000 / 10000);
        assertEq(vault.externalRewards(PAIR, address(ram)), r);
        assertEq(ram.balanceOf(address(vault)), r);
        (uint256 assetsS, uint256 assetsU) = vault.totalPairAssets(PAIR);
        vault.forwardExternalReward(PAIR, address(ram), receiver, r);
        (uint256 endS, uint256 endU) = vault.totalPairAssets(PAIR);
        assertEq(assetsS, endS);
        assertEq(assetsU, endU);
        assertEq(ram.balanceOf(receiver), r);
        noApprovals();
    }

    function testRecenterHarvestsOldNftBeforeBurn() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(700);
        recenter();
        assertGt(vault.externalRewards(PAIR, address(ram)), 0);
        assertLt(manager.lastHarvest(), manager.lastBurn());
    }

    function testForfeitureOnlyConfigAndStillOracleGated() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        gauge.setSlash(true);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(keeper);
        vm.expectRevert();
        vault.setAllowRewardForfeiture(PAIR, true);
        vm.prank(guardian);
        vm.expectRevert();
        vault.setAllowRewardForfeiture(PAIR, true);
        vm.prank(guardian);
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        assertFalse(vault.pairConfig(PAIR).emergencyMode);
        vault.setAllowRewardForfeiture(PAIR, true);
        feed.setTimestamp(block.timestamp - 13 hours);
        vm.prank(guardian);
        vm.expectRevert();
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        fresh();
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        assertEq(ram.balanceOf(address(adapter)), 0);
        vault.setAllowRewardForfeiture(PAIR, false);
        assertFalse(adapter.allowRewardForfeiture(PAIR));
    }

    function testKeeperCannotForwardOrConfigureAndGuardianOnlyPauses() public {
        vm.prank(keeper);
        vm.expectRevert();
        vault.forwardExternalReward(PAIR, address(ram), receiver, 0);
        vm.prank(guardian);
        vm.expectRevert();
        vault.forwardExternalReward(PAIR, address(ram), receiver, 0);
        vm.prank(keeper);
        vm.expectRevert();
        vault.setDeployLossBps(PAIR, 100);
        vm.prank(guardian);
        vault.setPairPause(PAIR, true, true, true);
        vm.prank(guardian);
        vm.expectRevert(RamsesBoostedVault.GuardianCannotUnpause.selector);
        vault.setPairPause(PAIR, false, false, false);
        vm.expectRevert(RamsesBoostedVault.InvalidConfiguration.selector);
        vault.forwardExternalReward(PAIR, address(stock), receiver, 1);
    }

    function testStaleWeekendAtFivePercentBlocksEveryLpExit() public {
        deposit(2e18, 200e6);
        recenter();
        pool.setTick(pool.tick() + 513);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.prank(keeper);
        vm.expectRevert();
        vault.recenter(PAIR, block.timestamp);
        vm.prank(stockAccount);
        vm.expectRevert();
        vault.withdrawForSide(PAIR, address(stock), 1e18, receiver, block.timestamp);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vm.expectRevert();
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, l);
    }

    function testActiveSwapRejectsCallbackFromWrongCaller() public {
        CallbackRelay relay = new CallbackRelay();
        pool.setCallbackRelay(address(relay));
        stock.mint(address(vault), 1e18);
        vm.startPrank(address(vault));
        stock.approve(address(adapter), 1e18);
        vm.expectRevert(RamsesSwapLib.InvalidCallback.selector);
        adapter.swapExactInput(PAIR, address(stock), 1e18, 99e6, block.timestamp);
        vm.stopPrank();
        assertEq(stock.balanceOf(address(relay)), 0);
    }

    function testDirectSwapCallbackRejected() public {
        vm.expectRevert(RamsesSwapLib.InvalidCallback.selector);
        adapter.uniswapV3SwapCallback(1, -1, "");
    }

    function testSettlementSwapBothDirectionsExactDeltasAndMinimum() public {
        // Exercise the extension under its actual vault identity without keeper NFT authority.
        stock.mint(address(vault), 1e18);
        usdg.mint(address(vault), 100e6);
        vm.startPrank(address(vault));
        stock.approve(address(adapter), 1e18);
        usdg.approve(address(adapter), 100e6);
        (uint256 used, uint256 out) =
            adapter.swapExactInput(PAIR, address(stock), 1e18, 99e6, block.timestamp);
        assertEq(used, 1e18);
        assertGt(out, 99e6);
        (used, out) = adapter.swapExactInput(PAIR, address(usdg), 100e6, 0.99e18, block.timestamp);
        assertEq(used, 100e6);
        assertGt(out, 0.99e18);
        stock.mint(address(vault), 1e18);
        stock.approve(address(adapter), 1e18);
        pool.setSwapFault(9000, false);
        vm.expectRevert(RamsesSwapLib.BalanceDeltaMismatch.selector);
        adapter.swapExactInput(PAIR, address(stock), 1e18, 99e6, block.timestamp);
        vm.stopPrank();
    }

    function testRecenterAlsoEnforcesSeparateDeployLossPolicy() public {
        deposit(2e18, 200e6);
        vault.setDeployLossBps(PAIR, 0);
        vm.prank(keeper);
        vm.expectRevert(RamsesRangeLib.DeployLossTooHigh.selector);
        vault.recenter(PAIR, block.timestamp);
        vault.setDeployLossBps(PAIR, 10);
        recenter();
    }

    function testWithdrawalSettlementMinimumAndAtomicity() public {
        deposit(2e18, 200e6);
        recenter();
        shift(700);
        pool.setSwapFault(9000, false);
        PairLedger memory before_ = vault.ledger(PAIR);
        vm.prank(usdgAccount);
        vm.expectRevert(RamsesSwapLib.BalanceDeltaMismatch.selector);
        vault.withdrawForSide(PAIR, address(usdg), before_.usdgPrincipal, receiver, block.timestamp);
        assertEq(abi.encode(vault.ledger(PAIR)), abi.encode(before_));
        noApprovals();
    }

    function testSettlementAdapterOnlyVault() public {
        vm.prank(keeper);
        vm.expectRevert(RamsesV3PairedAdapter.NotVault.selector);
        adapter.swapExactInput(PAIR, address(stock), 1e18, 1, block.timestamp);
    }

    function testEmergencyCollectsFeesWithReserveCut() public {
        deposit(2e18, 200e6);
        recenter();
        pool.accrueFees(100e6, 1e18);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        assertGt(reserve.available(PAIR, address(stock)), 0);
        assertGt(reserve.available(PAIR, address(usdg)), 0);
    }

    function testHarvestCapStopsRecenterBeforeOldNftBurn() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        PairConfig memory c = vault.pairConfig(PAIR);
        c.maxPairValueUSDG = 400e18;
        vault.updatePairRisk(PAIR, c);
        uint256 id = adapter.positionState(PAIR).tokenId;
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        // Native rewards take claims past the cap even without a price movement.
        recenter();
        assertTrue(vault.pairConfig(PAIR).allocationPaused);
        assertEq(adapter.positionState(PAIR).tokenId, id);
        assertGt(vault.externalRewards(PAIR, address(ram)), 0);
    }

    function testToxicPendingRamCannotBrickPrincipalSettlement() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(700);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        uint256 pending = adapter.pendingRewards(PAIR, address(ram));
        assertGt(pending, 0);
        ram.setTax(true, address(adapter));
        vm.prank(keeper);
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        vault.claimPendingRewards(PAIR);
        vault.setPairPause(PAIR, true, false, false);
        PairConfig memory c = vault.pairConfig(PAIR);
        c.maxCheckpointAge = 1;
        vault.updatePairRisk(PAIR, c);
        vm.warp(vm.getBlockTimestamp() + 2);
        fresh();
        uint256 claim = vault.ledger(PAIR).usdgPrincipal;
        vm.prank(usdgAccount);
        (uint256 returned,) =
            vault.withdrawForSide(PAIR, address(usdg), claim, receiver, block.timestamp);
        assertGt(returned, 0);
        assertEq(adapter.pendingRewards(PAIR, address(ram)), pending);
        assertEq(vault.externalRewards(PAIR, address(ram)), 0);
    }

    function testEmergencyPendingRewardsCanBeClaimedWithStaleFeedAndBrokenGauge() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        uint256 r = adapter.pendingRewards(PAIR, address(ram));
        assertGt(r, 0);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        gauge.setSlash(true);
        vm.prank(keeper);
        vault.claimPendingRewards(PAIR);
        assertEq(vault.externalRewards(PAIR, address(ram)), r);
        assertEq(adapter.pendingRewards(PAIR, address(ram)), 0);
        assertGt(reserve.available(PAIR, address(stock)), 0);
    }

    function testNativeRewardReserveTransferTaxRollsBackEverything() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        PairLedger memory before_ = vault.ledger(PAIR);
        stock.setTax(true, address(vault));
        stock.setTaxRecipient(address(reserve));
        vm.prank(keeper);
        vm.expectRevert();
        vault.harvest(PAIR, block.timestamp);
        assertEq(abi.encode(vault.ledger(PAIR)), abi.encode(before_));
        assertEq(vault.externalRewards(PAIR, address(ram)), 0);
        assertEq(ram.balanceOf(address(vault)), 0);
    }

    function testVaultReentrancyFromAuthorizedKeeperCallbackRollsBack() public {
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        ReenterVault callback = new ReenterVault(vault, PAIR);
        vault.grantRole(vault.KEEPER_ROLE(), address(callback));
        gauge.setCallback(address(callback));
        vm.prank(keeper);
        vm.expectRevert(bytes4(keccak256("ReentrancyGuardReentrantCall()")));
        vault.harvest(PAIR, block.timestamp);
        assertEq(ram.balanceOf(address(vault)), 0);
        assertEq(vault.externalRewards(PAIR, address(ram)), 0);
    }

    function testVaultInitializationCannotBeStolenOrRepeated() public {
        RamsesBoostedVault freshVault = new RamsesBoostedVault();
        RamsesVaultAdapter freshAdapter =
            new RamsesVaultAdapter(address(freshVault), IRamsesNfp(address(manager)), guard);
        vm.prank(keeper);
        vm.expectRevert(RamsesBoostedVault.InvalidConfiguration.selector);
        freshVault.initialize(keeper, keeper, keeper, guard, reserve, freshAdapter);
        vm.expectRevert();
        vault.initialize(address(this), keeper, guardian, guard, reserve, adapter);
        bytes32 configRole = vault.CONFIG_ROLE();
        vm.prank(keeper);
        vm.expectRevert();
        vault.grantRole(configRole, keeper);
    }

    function testPairCapAndDeadline() public {
        vm.prank(usdgAccount);
        vm.expectRevert(RamsesBoostedVault.PairCapExceeded.selector);
        vault.depositForPair(PAIR, address(usdg), 1001e6);
        vm.prank(keeper);
        vm.expectRevert(RamsesBoostedVault.InvalidDeadline.selector);
        vault.recenter(PAIR, block.timestamp + 301);
        vm.prank(keeper);
        vm.expectRevert(RamsesBoostedVault.InvalidDeadline.selector);
        vault.recenter(PAIR, block.timestamp - 1);
    }
}

contract GuardTest is Phase2Fixture {
    function testOracleReferenceAndFixedUsdg() public view {
        (uint256 s, uint256 u) = guard.pricesUSD18(PAIR);
        assertEq(s, 100e18);
        assertEq(u, 1e18);
        uint160 ref = guard.referenceSqrtPriceX96(PAIR, true);
        assertApproxEqAbs(
            TickMath.getTickAtSqrtPrice(ref), stockFirst() ? int24(-230271) : int24(230270), 1
        );
    }

    function testTokenOraclePauseBlocksAllocationAndRemoval() public {
        assertFalse(stock.paused());
        stock.setOraclePaused(true);
        vm.expectRevert(RamsesOracleGuard.StockOraclePaused.selector);
        guard.referenceSqrtPriceX96(PAIR, true);
        vm.expectRevert(RamsesOracleGuard.StockOraclePaused.selector);
        guard.referenceSqrtPriceX96(PAIR, false);
        stock.setOraclePaused(false);
        guard.referenceSqrtPriceX96(PAIR, true);
    }

    function testPausedDisabledInvalidFutureIncompleteRevert() public {
        stock.setPaused(true);
        vm.expectRevert(RamsesOracleGuard.StockOraclePaused.selector);
        guard.referenceSqrtPriceX96(PAIR, false);
        stock.setPaused(false);
        feed.set(0);
        vm.expectRevert(
            abi.encodeWithSelector(RamsesOracleGuard.InvalidOracleAnswer.selector, address(feed))
        );
        guard.pricesUSD18(PAIR);
        feed.set(-1);
        vm.expectRevert(
            abi.encodeWithSelector(RamsesOracleGuard.InvalidOracleAnswer.selector, address(feed))
        );
        guard.pricesUSD18(PAIR);
        feed.set(100e8);
        feed.setTimestamp(block.timestamp + 1);
        vm.expectRevert(
            abi.encodeWithSelector(RamsesOracleGuard.InvalidOracleAnswer.selector, address(feed))
        );
        guard.pricesUSD18(PAIR);
        feed.setTimestamp(0);
        vm.expectRevert(
            abi.encodeWithSelector(RamsesOracleGuard.InvalidOracleAnswer.selector, address(feed))
        );
        guard.pricesUSD18(PAIR);
        fresh();
        feed.setRound(2, 1);
        vm.expectRevert();
        guard.pricesUSD18(PAIR);
        feed.setRound(2, 2);
        feed.setFail(true);
        vm.expectRevert(bytes("FEED"));
        guard.pricesUSD18(PAIR);
        feed.setFail(false);
        guard.setEnabled(PAIR, false);
        vm.expectRevert(RamsesOracleGuard.PairNotEnabled.selector);
        guard.pricesUSD18(PAIR);
    }

    function testTwelveHourBoundaryAndWeekendFreshFivePercent() public {
        vm.warp(vm.getBlockTimestamp() + 12 hours);
        guard.referenceSqrtPriceX96(PAIR, false);
        vm.warp(vm.getBlockTimestamp() + 1);
        vm.expectRevert();
        guard.referenceSqrtPriceX96(PAIR, false);
        fresh();
        int24 start = pool.tick();
        pool.setTick(start + 500);
        vm.expectRevert();
        guard.referenceSqrtPriceX96(PAIR, true);
        guard.referenceSqrtPriceX96(PAIR, false);
        pool.setTick(start - 500);
        vm.expectRevert();
        guard.referenceSqrtPriceX96(PAIR, true);
        guard.referenceSqrtPriceX96(PAIR, false);
        pool.setTick(start + 700);
        vm.expectRevert();
        guard.referenceSqrtPriceX96(PAIR, false);
        pool.setTick(start - 700);
        vm.expectRevert();
        guard.referenceSqrtPriceX96(PAIR, false);
    }

    function testConfigurationIdentityDecimalsAndRole() public {
        RamsesOracleGuard.FeedConfig memory c = guard.feedConfig(PAIR);
        vm.prank(keeper);
        vm.expectRevert();
        guard.configurePair(PAIR, c);
        c.stockDecimals = 6;
        vm.expectRevert();
        guard.configurePair(PAIR, c);
        c = guard.feedConfig(PAIR);
        c.maxStaleness = 13 hours;
        vm.expectRevert();
        guard.configurePair(PAIR, c);
        c = guard.feedConfig(PAIR);
        c.pool = IRamsesPool(address(0xdead));
        vm.expectRevert();
        guard.configurePair(PAIR, c);
        vm.expectRevert(RamsesOracleGuard.PoolMismatch.selector);
        guard.validateRemovalPrice(PAIR, IRamsesPool(address(0xdead)));
    }

    function testFuzzOraclePriceAndBothDirectionGate(uint32 priceSeed, int16 tickOffset) public {
        uint256 price = bound(uint256(priceSeed), 1e8, 100000e8);
        feed.set(int256(price));
        // Move pool to the feed-derived reference using the same independently reproduced ratio math.
        uint256 raw = stockFirst() ? price / 100 : 1e38 / price;
        uint160 ref = uint160(Math.sqrt(Math.mulDiv(raw, 1 << 128, 1e18)) << 32);
        int24 center = TickMath.getTickAtSqrtPrice(ref);
        int24 offset = int24(bound(int256(tickOffset), -800, 800));
        pool.setTick(center + offset);
        if (offset > -280 && offset < 280) {
            guard.referenceSqrtPriceX96(PAIR, true);
        } else if (offset > 650 || offset < -650) {
            vm.expectRevert();
            guard.referenceSqrtPriceX96(PAIR, false);
        }
    }
}

contract GuardReverseOrderTest is GuardTest {
    function stockFirst() internal pure override returns (bool) {
        return true;
    }
}

contract ReenterVault {
    RamsesBoostedVault immutable vault;
    bytes32 immutable pair;

    constructor(RamsesBoostedVault v, bytes32 p) {
        vault = v;
        pair = p;
    }

    function reenter() external {
        vault.checkpoint(pair, block.timestamp);
    }
}
