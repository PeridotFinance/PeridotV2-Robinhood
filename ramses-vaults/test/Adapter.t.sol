// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { RamsesV3PairedAdapter, IRamsesReferenceOracle } from "../src/RamsesV3PairedAdapter.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import {
    TokenDouble,
    OracleDouble,
    FactoryVoterDouble,
    PoolDouble,
    NfpDouble,
    GaugeDouble
} from "./mocks/RamsesV3Double.sol";

abstract contract AdapterFixture is Test {
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    RamsesV3PairedAdapter internal adapter;
    TokenDouble internal stock;
    TokenDouble internal usdg;
    TokenDouble internal ram;
    OracleDouble internal oracle;
    FactoryVoterDouble internal factory;
    PoolDouble internal pool;
    NfpDouble internal pm;
    GaugeDouble internal gauge;
    int24 internal center;
    uint160 internal ref;
    function stockFirst() internal pure virtual returns (bool);

    function setUp() public virtual {
        vm.warp(2930 * 1 weeks + 1 days);
        TokenDouble s = new TokenDouble("NVDA", 18);
        TokenDouble u = new TokenDouble("USDG", 6);
        stock = TokenDouble(stockFirst() ? address(0x100) : address(0x200));
        usdg = TokenDouble(stockFirst() ? address(0x200) : address(0x100));
        vm.etch(address(stock), address(s).code);
        vm.etch(address(usdg), address(u).code);
        ram = new TokenDouble("RAM", 18);
        factory = new FactoryVoterDouble();
        center = stockFirst() ? int24(-222680) : int24(222680);
        ref = TickMath.getSqrtPriceAtTick(center);
        pool = new PoolDouble(
            stockFirst() ? address(stock) : address(usdg),
            stockFirst() ? address(usdg) : address(stock),
            address(factory),
            center
        );
        pm = new NfpDouble(pool, address(factory));
        pool.setManager(address(pm));
        address[] memory ts = new address[](3);
        ts[0] = address(stock);
        ts[1] = address(usdg);
        ts[2] = address(ram);
        gauge = new GaugeDouble(pool, pm, ts);
        pm.setGauge(gauge);
        factory.set(address(pool), address(gauge));
        oracle = new OracleDouble();
        oracle.set(ref);
        adapter = new RamsesV3PairedAdapter(address(this), IRamsesNfp(address(pm)), oracle);
        adapter.registerPair(PAIR, _params());
        adapter.setRange(PAIR, center - 100, center + 100);
        stock.mint(address(this), 100e18);
        usdg.mint(address(this), 100_000e6);
        stock.approve(address(adapter), type(uint256).max);
        usdg.approve(address(adapter), type(uint256).max);
        // The existing pool's LP assets back movement across the price band in shock tests.
        stock.mint(address(pool), 1_000_000e18);
        usdg.mint(address(pool), 100_000_000e6);
        for (uint256 i; i < 3; i++) {
            gauge.fund(ts[i], block.timestamp / 1 weeks, i == 1 ? 1000e6 : 1000e18);
        }
    }

    function _params() internal view returns (RamsesV3PairedAdapter.RegisterPairParams memory) {
        return RamsesV3PairedAdapter.RegisterPairParams(
            address(stock),
            address(usdg),
            IRamsesPool(address(pool)),
            IRamsesGauge(address(gauge)),
            500,
            50
        );
    }

    function _add() internal returns (uint128 l) {
        (,, l) = adapter.addLiquidity(PAIR, 1e18, 220e6, block.timestamp);
    }

    function _refresh() internal {
        oracle.set(ref);
    }
}

abstract contract AdapterTestBase is AdapterFixture {
    function reenter() external {
        // Model an upstream callback through the authorized vault itself.
        adapter.collectFees(PAIR, block.timestamp);
    }

    function test_reentrantHarvestCannotEnterVaultActions() public {
        uint128 l = _add();
        gauge.setCallback(address(this));
        vm.expectRevert(RamsesV3PairedAdapter.ReentrantCall.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, l);
        adapter.setAllowRewardForfeiture(PAIR, true);
        vm.expectEmit(true, true, false, false, address(adapter));
        emit RamsesV3PairedAdapter.ExitHarvestFailed(PAIR, 1);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function test_forfeitureFlagAndSelfCallAccess() public {
        assertFalse(adapter.allowRewardForfeiture(PAIR));
        vm.prank(address(9));
        vm.expectRevert(RamsesV3PairedAdapter.NotVault.selector);
        adapter.setAllowRewardForfeiture(PAIR, true);
        vm.expectRevert(RamsesV3PairedAdapter.NotSelf.selector);
        adapter.harvestForExit(PAIR);
        vm.expectRevert(RamsesV3PairedAdapter.UnknownPair.selector);
        adapter.setAllowRewardForfeiture(bytes32(uint256(1)), true);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.setAllowRewardForfeiture(PAIR, false);
        assertFalse(adapter.allowRewardForfeiture(PAIR));
    }

    function test_forfeitureRevertingHarvestExitsAndBurns() public {
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        uint128 l = _add();
        bytes memory callData = abi.encodeWithSelector(IRamsesGauge.getRewardTokens.selector);
        vm.mockCallRevert(address(gauge), callData, abi.encodeWithSignature("GaugeUnavailable()"));
        vm.expectRevert(abi.encodeWithSignature("GaugeUnavailable()"));
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, l);
        adapter.setAllowRewardForfeiture(PAIR, true);
        vm.expectEmit(true, true, false, false, address(adapter));
        emit RamsesV3PairedAdapter.ExitHarvestFailed(PAIR, 1);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.setAllowRewardForfeiture(PAIR, false);
        vm.expectRevert(abi.encodeWithSignature("GaugeUnavailable()"));
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        adapter.setAllowRewardForfeiture(PAIR, true);
        vm.expectEmit(true, true, false, false, address(adapter));
        emit RamsesV3PairedAdapter.ExitHarvestFailed(PAIR, 1);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
        assertApproxEqAbs(stock.balanceOf(address(this)), s, 2);
        assertApproxEqAbs(usdg.balanceOf(address(this)), u, 2);
    }

    function test_forfeitureMismatchRollsBackAllRewards() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        adapter.decreaseLiquidity(PAIR, l / 2, ref, block.timestamp);
        uint256 pending = adapter.pendingRewards(PAIR, address(ram));
        assertGt(pending, 0);
        uint256 claimed = gauge.claimed(block.timestamp / 1 weeks, 1, address(ram));
        uint256 gaugeBalance = ram.balanceOf(address(gauge));
        vm.warp(vm.getBlockTimestamp() + 3600);
        // The last reward token fails after native rewards have already been transferred/credited.
        ram.setTax(true, address(gauge));
        adapter.setAllowRewardForfeiture(PAIR, true);
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        (uint256 outS, uint256 outU,) = adapter.decreaseLiquidity(PAIR, l - l / 2, ref, block.timestamp);
        assertEq(stock.balanceOf(address(this)), s + outS);
        assertEq(usdg.balanceOf(address(this)), u + outU);
        assertEq(gauge.claimed(block.timestamp / 1 weeks, 1, address(ram)), claimed);
        assertEq(ram.balanceOf(address(gauge)), gaugeBalance);
        assertEq(adapter.pendingRewards(PAIR, address(ram)), pending);
        assertEq(stock.balanceOf(address(adapter)), adapter.pendingRewards(PAIR, address(stock)));
        assertEq(usdg.balanceOf(address(adapter)), adapter.pendingRewards(PAIR, address(usdg)));
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        adapter.claimPendingRewards(PAIR);
        assertEq(ram.balanceOf(address(this)), pending);
    }

    function test_forfeitureStillClaimsHealthyRewards() public {
        adapter.setAllowRewardForfeiture(PAIR, true);
        test_rewardsSeparateAndHarvestBeforeDecreaseAndBurn();
    }

    function test_forfeitureDoesNotBypassExitChecks() public {
        uint128 l = _add();
        adapter.setAllowRewardForfeiture(PAIR, true);
        oracle.setFail(true);
        vm.expectRevert(OracleDouble.StaleOracle.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        oracle.setFail(false);
        pool.setTick(center + 2000);
        vm.expectRevert(RamsesV3PairedAdapter.SlippageExceeded.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        pool.setTick(center);
        stock.setTax(true, address(pool));
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        stock.setTax(false, address(pool));
        pm.setFaults(true, false, false);
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, l);
    }

    function test_roundTripAndApprovals() public {
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        uint128 l = _add();
        l += _add();
        assertEq(stock.allowance(address(adapter), address(pm)), 0);
        assertEq(usdg.allowance(address(adapter), address(pm)), 0);
        assertEq(stock.balanceOf(address(adapter)), 0);
        assertEq(usdg.balanceOf(address(adapter)), 0);
        adapter.collectFees(PAIR, block.timestamp);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertApproxEqAbs(stock.balanceOf(address(this)), s, 4);
        assertApproxEqAbs(usdg.balanceOf(address(this)), u, 4);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
    }

    function test_rewardsSeparateAndHarvestBeforeDecreaseAndBurn() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        uint256 expected = gauge.earned(address(ram), 1);
        assertGt(expected, 0);
        uint256 stockBefore = stock.balanceOf(address(this));
        (uint256 principal,,) = adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertEq(stock.balanceOf(address(this)) - stockBefore, principal);
        assertEq(ram.balanceOf(address(this)), 0);
        assertEq(adapter.pendingRewards(PAIR, address(ram)), expected);
        assertEq(pm.lastHarvest() + 1, pm.lastDecrease());
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(pm.lastHarvest() + 1, pm.lastBurn());
        (address[] memory ts, uint256[] memory amounts) = adapter.harvest(PAIR, block.timestamp);
        assertEq(ts[2], address(ram));
        assertEq(amounts[2], expected);
        assertEq(ram.balanceOf(address(this)), expected);
        assertEq(adapter.pendingRewards(PAIR, address(ram)), 0);
    }

    function test_feeSplitAndNoDoubleCollection() public {
        _add();
        TokenDouble(pool.token0()).mint(address(pool), 1000e6);
        TokenDouble(pool.token1()).mint(address(pool), 1e18);
        pool.accrueFees(1000e6, 1e18);
        IRamsesNfp.Position memory p = pm.positions(1);
        assertGt(p.tokensOwed0, 0);
        assertGt(p.tokensOwed1, 0);
        (uint256 s, uint256 u) = adapter.collectFees(PAIR, block.timestamp);
        assertEq(s, stockFirst() ? p.tokensOwed0 : p.tokensOwed1);
        assertEq(u, stockFirst() ? p.tokensOwed1 : p.tokensOwed0);
        (s, u) = adapter.collectFees(PAIR, block.timestamp);
        assertEq(s + u, 0);
        assertEq(pool.protocol0(), 500e6);
    }

    function test_staleOracleBlocksAddAndRemovalButNotHarvest() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.expectRevert(OracleDouble.StaleOracle.selector);
        adapter.addLiquidity(PAIR, 1e18, 220e6, block.timestamp);
        vm.expectRevert(OracleDouble.StaleOracle.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.harvest(PAIR, block.timestamp);
        assertGt(ram.balanceOf(address(this)), 0);
    }

    function test_invalidReferenceRejectedEvenWhenEmpty() public {
        vm.expectRevert(RamsesV3PairedAdapter.InvalidReferencePrice.selector);
        adapter.positionStateAt(PAIR, 0);
        oracle.set(0);
        vm.expectRevert(RamsesV3PairedAdapter.InvalidReferencePrice.selector);
        adapter.addLiquidity(PAIR, 1e18, 220e6, block.timestamp);
    }

    function test_referenceCannotBeCallerChosen() public {
        uint128 l = _add();
        vm.expectRevert(RamsesV3PairedAdapter.InvalidReferencePrice.selector);
        adapter.decreaseLiquidity(PAIR, l, ref + 1, block.timestamp);
    }

    function test_spotCannotMoveReferenceValuation() public {
        _add();
        RamsesV3PairedAdapter.PositionState memory s = adapter.positionStateAt(PAIR, ref);
        pool.setTick(center + 90);
        RamsesV3PairedAdapter.PositionState memory at = adapter.positionStateAt(PAIR, ref);
        assertEq(at.stockAmount, s.stockAmount);
        assertEq(at.usdgAmount, s.usdgAmount);
        assertTrue(adapter.positionState(PAIR).stockAmount != s.stockAmount);
    }

    function test_rangeLocksUntilNftBurned() public {
        uint128 l = _add();
        vm.expectRevert(RamsesV3PairedAdapter.RangeLocked.selector);
        adapter.setRange(PAIR, center - 10, center + 10);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        vm.expectRevert(RamsesV3PairedAdapter.RangeLocked.selector);
        adapter.clearRange(PAIR);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        adapter.clearRange(PAIR);
        (,, bool ranged) = adapter.positionTicks(PAIR);
        assertFalse(ranged);
    }

    function test_badRangeAndExpiredDeadline() public {
        vm.expectRevert(RamsesV3PairedAdapter.InvalidRange.selector);
        adapter.setRange(PAIR, center - 1, center + 10);
        vm.expectRevert(RamsesV3PairedAdapter.InvalidDeadline.selector);
        adapter.addLiquidity(PAIR, 1e18, 220e6, block.timestamp - 1);
    }

    function test_onlyVault() public {
        vm.prank(address(9));
        vm.expectRevert(RamsesV3PairedAdapter.NotVault.selector);
        adapter.harvest(PAIR, block.timestamp);
    }

    function test_silentRewardSlashingRevertsAtomically() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        gauge.setSlash(true);
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, l);
        assertEq(ram.balanceOf(address(0xdead)), 0);
    }

    function test_taxedVaultPullRejected() public {
        stock.setTax(true, address(this));
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        _add();
    }

    function test_taxedRefundRejected() public {
        stock.setTax(true, address(adapter));
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        adapter.addLiquidity(PAIR, 10e18, 220e6, block.timestamp);
    }

    function test_taxedCollectRejected() public {
        uint128 l = _add();
        stock.setTax(true, address(pool));
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
    }

    function test_taxedManagerPaymentRejected() public {
        stock.setTax(true, address(adapter));
        stock.setTaxRecipient(address(pool));
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        adapter.addLiquidity(PAIR, 1e18, 1000e6, block.timestamp);
    }

    function test_silentBurnRejected() public {
        uint128 l = _add();
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        pm.setFaults(false, false, true);
        vm.expectRevert(RamsesV3PairedAdapter.InvalidPosition.selector);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 1);
    }

    function test_pendingRewardsRemainAccessibleWhenGaugeFails() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        adapter.decreaseLiquidity(PAIR, l / 2, ref, block.timestamp);
        uint256 pending = adapter.pendingRewards(PAIR, address(ram));
        assertGt(pending, 0);
        vm.warp(vm.getBlockTimestamp() + 3600);
        gauge.setSlash(true);
        adapter.claimPendingRewards(PAIR);
        assertEq(ram.balanceOf(address(this)), pending);
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        adapter.harvest(PAIR, block.timestamp);
    }

    function test_wrongManagerReturnRejected() public {
        pm.setFaults(false, true, false);
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        _add();
    }

    function test_wrongCollectReturnRejected() public {
        _add();
        pm.setFaults(true, false, false);
        vm.expectRevert(RamsesV3PairedAdapter.BalanceDeltaMismatch.selector);
        adapter.collectFees(PAIR, block.timestamp);
    }

    function test_newRewardTokenAndRemovedHistoricalToken() public {
        _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        TokenDouble extra = new TokenDouble("EXTRA", 18);
        address[] memory ts = new address[](1);
        ts[0] = address(extra);
        gauge.setTokens(ts);
        gauge.fund(address(extra), block.timestamp / 1 weeks, 100e18);
        adapter.harvest(PAIR, block.timestamp);
        assertGt(ram.balanceOf(address(this)), 0);
        assertGt(extra.balanceOf(address(this)), 0);
    }

    function test_periodBoundaryDoesNotAwardPreMintTime() public {
        vm.warp((vm.getBlockTimestamp() / 1 weeks + 1) * 1 weeks - 1800);
        _refresh();
        _add();
        uint256 next = vm.getBlockTimestamp() / 1 weeks + 1;
        gauge.fund(address(ram), next, 1000e18);
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        uint256 previous = gauge.periodEarned(next - 1, address(ram), 1);
        uint256 current = gauge.periodEarned(next, address(ram), 1);
        assertGt(previous, 0);
        assertApproxEqAbs(previous, current, 1);
        adapter.harvest(PAIR, block.timestamp);
        assertEq(ram.balanceOf(address(this)), previous + current);
    }

    function test_outOfRangeEarnsNoRewards() public {
        adapter.setRange(PAIR, center + 100, center + 200);
        _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        adapter.harvest(PAIR, block.timestamp);
        assertEq(ram.balanceOf(address(this)), 0);
    }

    function test_increaseDoesNotRetroactivelyEarn() public {
        _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        uint256 earned = gauge.earned(address(ram), 1);
        _add();
        assertEq(gauge.earned(address(ram), 1), earned);
    }

    function test_allocationManipulationRejected() public {
        pool.setTick(center + 100);
        vm.expectRevert(RamsesV3PairedAdapter.SlippageExceeded.selector);
        _add();
    }

    function test_removalOutsideBandRejectedEvenIfBothMinimumsZero() public {
        uint128 l = _add();
        pool.setTick(center + 2000);
        vm.expectRevert(RamsesV3PairedAdapter.SlippageExceeded.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
    }

    function test_inverseRemovalBandFarEndpoint() public {
        // 8.2% stock-price envelope: inverse pool high endpoint is +8.93%, not +8.2%.
        uint128 l = _add();
        int24 move = stockFirst() ? int24(-850) : int24(850);
        pool.setTick(center + move);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
    }

    function test_fullRangeRoundTrip() public {
        adapter.clearRange(PAIR);
        uint128 l = _add();
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testFuzz_roundTrip(uint96 stockN, uint64 usdgN, int16 widthSeed) public {
        uint256 s = bound(stockN, 1e12, 10e18);
        uint256 u = bound(usdgN, 1e3, 10_000e6);
        int24 w = int24(int256(bound(uint16(widthSeed), 1, 500))) * 10;
        adapter.setRange(PAIR, center - w, center + w);
        uint256 beforeS = stock.balanceOf(address(this));
        uint256 beforeU = usdg.balanceOf(address(this));
        (,, uint128 l) = adapter.addLiquidity(PAIR, s, u, block.timestamp);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertApproxEqAbs(stock.balanceOf(address(this)), beforeS, 2);
        assertApproxEqAbs(usdg.balanceOf(address(this)), beforeU, 2);
    }

    function testFuzz_outOfRangeBothSides(int16 offsetSeed, bool above) public {
        int24 offset = int24(int256(bound(uint16(offsetSeed), 1, 100))) * 10;
        adapter.setRange(
            PAIR,
            above ? center + offset : center - offset - 100,
            above ? center + offset + 100 : center - offset
        );
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        uint128 l = _add();
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertApproxEqAbs(stock.balanceOf(address(this)), s, 2);
        assertApproxEqAbs(usdg.balanceOf(address(this)), u, 2);
    }

    function testFuzz_removalBand(uint16 moveSeed) public {
        uint128 l = _add();
        int24 move = int24(int256(bound(moveSeed, 0, 820)));
        pool.setTick(center + (stockFirst() ? -move : move));
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
    }
}

contract AdapterStock0Test is AdapterTestBase {
    function stockFirst() internal pure override returns (bool) {
        return true;
    }
}

contract AdapterUsdg0Test is AdapterTestBase {
    function stockFirst() internal pure override returns (bool) {
        return false;
    }
}
