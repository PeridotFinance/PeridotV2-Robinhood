// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { LendingFixture } from "../LendingFixture.sol";
import { PausableTokenDouble } from "../mocks/Phase2Double.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { PToken } from "peridot/PToken.sol";
import { AnchoredFallbackOracle, IFallbackFeed, IFallbackPool, IFallbackStock, IClosureCalendar } from "./AnchoredFallbackOracle.sol";
import { FbPool, FbCalendar } from "./AnchoredFallbackOracle.t.sol";

contract UiStockDouble is PausableTokenDouble {
    uint256 public uiMultiplier;

    constructor() PausableTokenDouble("NVDA", 18) { }

    function setMultiplier(uint256 m) external {
        uiMultiplier = m;
    }
}

/// @dev TEST-ONLY shim that lets the REAL oracle guard, vault, markets and controller consume the prototype: it presents the prototype's price for the
/// operation class the test selected as a fresh Chainlink-shaped answer. PRODUCTION WOULD NEED THE OPERATION CLASS PLUMBED through the guard and the
/// lending price source (today `RamsesOracleGuard` has one price entry point and cannot tell an exit from a borrow). That plumbing is an implementation
/// cost reported in the README; it is exactly what these tests stand in for.
contract FallbackFeedShim {
    uint8 public constant decimals = 8;
    AnchoredFallbackOracle public immutable oracle;
    AnchoredFallbackOracle.Op public op = AnchoredFallbackOracle.Op.EXIT;

    constructor(AnchoredFallbackOracle o) {
        oracle = o;
    }

    function setOp(AnchoredFallbackOracle.Op x) external {
        op = x;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        (uint256 p,) = oracle.price(op);
        return (1, int256(p / 1e10), block.timestamp, block.timestamp, 1);
    }
}

/// @notice Phase 0 ECONOMICS of the bounded fallback on the real vault, guard, markets and controller (local doubles for the pool, position manager,
/// gauge and token). What these tests establish, with numbers: the move limit L bounds the change of the POOL's TWAP since the anchor; it does not
/// bound the error against the market, it does not bound what repeated exits extract, and a max(anchor, fallback) liquidation price does not remove
/// false liquidations. Every test runs for the stock as token0 and as token1, and for both candidate modes (anchored, hold).
abstract contract FallbackEconomicsBase is LendingFixture {
    AnchoredFallbackOracle internal oracle;
    FbPool internal fbPool;
    FbCalendar internal cal;
    FallbackFeedShim internal shim;
    address internal carol = address(0xca401);
    uint256 internal t0;

    function holdMode() internal pure virtual returns (bool) {
        return false;
    }

    function crashTicks(int24 magnitude) internal view returns (int24) {
        return stockFirst() ? -magnitude : magnitude; // the sign of the pool-tick change that makes the STOCK price fall
    }

    function setUp() public override {
        super.setUp();
        UiStockDouble impl = new UiStockDouble();
        vm.etch(address(stock), address(impl).code);
        UiStockDouble(address(stock)).setMultiplier(1.001718e18);
        fbPool = new FbPool(block.timestamp - 2 days, pool.tick());
        cal = new FbCalendar();
        oracle = new AnchoredFallbackOracle(address(this), address(0xBEEF), _cfg(false));
        shim = new FallbackFeedShim(oracle);
        guard.configurePair(
            PAIR,
            RamsesOracleGuard.FeedConfig(
                address(stock), address(usdg), IAggregatorV3(address(shim)), IRamsesPool(address(pool)), IRamsesGauge(address(gauge)), 12 hours, 300, 600, 18, 6, 8, true
            )
        );
        usdg.mint(carol, 1000e6);
        vm.prank(carol);
        usdg.approve(usdgAccount, type(uint256).max);
        t0 = block.timestamp;
    }

    function _cfg(bool liq) internal view returns (AnchoredFallbackOracle.Config memory) {
        return AnchoredFallbackOracle.Config({
            feed: IFallbackFeed(address(feed)),
            stock: IFallbackStock(address(stock)),
            pool: IFallbackPool(address(fbPool)),
            calendar: IClosureCalendar(address(cal)),
            stockIsToken0: stockFirst(),
            stockDecimals: 18,
            usdgDecimals: 6,
            twapWindow: 1800,
            maxStaleness: 12 hours,
            maxPokeDelay: 1 hours,
            maxClosureDuration: 72 hours,
            maxOpenDuration: 24 hours,
            maxMoveBps: 200,
            maxBasisBps: 100,
            liquidationInFallback: liq,
            mode: holdMode() ? AnchoredFallbackOracle.Mode.HOLD : AnchoredFallbackOracle.Mode.ANCHORED
        });
    }

    // ------------------------------------------------------------------ helpers
    function _openAndAnchor() internal {
        supply();
        vm.prank(carol);
        uMarket.mint(200e6);
        recenter();
        oracle.poke();
    }

    function _goStale(uint256 hoursAfterAnchor, bool closed) internal {
        vm.warp(t0 + hoursAfterAnchor * 1 hours);
        cal.setClosed(closed);
    }

    /// @dev The market gaps by `magnitude` pool ticks (a stock fall): the feed answers and the pool re-prices. The fallback oracle's pool history follows.
    function _resolveGap(int24 magnitude) internal {
        shift(crashTicks(magnitude));
        fbPool.setTick(pool.tick());
    }

    function _usdgClaim(address who) internal view returns (uint256) {
        return uMarket.balanceOf(who) * uMarket.exchangeRateStored() / 1e18;
    }

    function _stockClaim(address who) internal view returns (uint256) {
        return sMarket.balanceOf(who) * sMarket.exchangeRateStored() / 1e18;
    }

    function _exit(address who) internal returns (uint256 got) {
        uint256 before_ = usdg.balanceOf(who);
        uint256 shares = uMarket.balanceOf(who);
        vm.prank(who);
        uMarket.redeem{ gas: 12_000_000 }(shares);
        got = usdg.balanceOf(who) - before_;
    }

    // ------------------------------------------------------------------ 1. a genuine gap with a stationary pool: L does not bound the error
    function testStationaryPoolHidesAnyRealGapFromTheMoveLimit() public {
        _openAndAnchor();
        _goStale(13, true);
        (uint256 p,) = oracle.price(AnchoredFallbackOracle.Op.EXIT);
        assertApproxEqRel(p, 100e18, 0.001e18, "with the pool stationary the estimate is the anchor");
        // the market truly gapped by -6%, -20% and -50% while the pool did not trade: the oracle cannot tell, whatever L is
        uint256[3] memory truth = [uint256(94e18), 80e18, 50e18];
        for (uint256 i; i < 3; i++) {
            uint256 errBps = (p - truth[i]) * 10_000 / truth[i];
            emit log_named_uint("true price (1e18)", truth[i]);
            emit log_named_uint("estimate error against the market, bp (L is 200 bp)", errBps);
            assertGt(errBps, 200, "the error against the market exceeds L: L bounds the pool's move since the anchor, not the market's");
        }
        // the guard's own removal check compares the pool spot with this estimate, and a stationary pool agrees with it, so exits are admitted
        (uint256 stockPrice,) = guard.pricesUSD18(PAIR);
        assertApproxEqRel(stockPrice, 100e18, 0.001e18, "the real guard accepts the stale estimate");
    }

    // ------------------------------------------------------------------ 2. losses move from the exiting supplier to the remaining ones
    function _gapRun(bool bobExits, int24 gap) internal returns (uint256 bobGot, uint256 bobStay, uint256 carolClaim, uint256 aliceStock, uint256 loss) {
        _openAndAnchor();
        _goStale(13, true);
        uint256 bobShares = uMarket.balanceOf(bob);
        if (bobExits) bobGot = _exit(bob);
        _resolveGap(gap); // the gap becomes visible: the feed answers, the pool re-prices
        vm.prank(keeper);
        vault.checkpoint(PAIR, vm.getBlockTimestamp());
        bobStay = bobShares * uMarket.exchangeRateStored() / 1e18;
        carolClaim = _usdgClaim(carol);
        aliceStock = _stockClaim(alice);
        loss = vault.ledger(PAIR).cumulativeLossUSDG;
    }

    function testExitAtTheStaleEstimateShiftsTheLossToRemainingSuppliers() public {
        uint256 snap = vm.snapshotState();
        (, uint256 bobStayA, uint256 carolA, uint256 aliceA, uint256 lossA) = _gapRun(false, 2200); // everyone stays through a -20% gap
        vm.revertToState(snap);
        (uint256 bobGotB,, uint256 carolB, uint256 aliceB, uint256 lossB) = _gapRun(true, 2200); // bob exits during the stale window
        emit log_named_uint("bob would hold after the gap if he stayed (raw USDG)", bobStayA);
        emit log_named_uint("bob received by exiting at the stale estimate (raw USDG)", bobGotB);
        emit log_named_uint("carol's claim after the gap, bob stayed", carolA);
        emit log_named_uint("carol's claim after the gap, bob exited", carolB);
        emit log_named_uint("alice stock claim, bob stayed", aliceA);
        emit log_named_uint("alice stock claim, bob exited", aliceB);
        emit log_named_uint("cumulative recognized loss, bob stayed (USD 1e18)", lossA);
        emit log_named_uint("cumulative recognized loss, bob exited (USD 1e18)", lossB);
        assertGt(bobGotB, bobStayA, "the exiting supplier is paid more than the post-gap value of the same position");
        assertLt(carolB, carolA, "the remaining USDG supplier bears more because the exiting one left before the loss was visible");
        assertEq(lossA, lossB, "the pair's total recognized loss is the same: the exit changed WHO bears it, not how much there is");
        uint256 avoided = bobGotB - bobStayA;
        uint256 carolExtra = carolA - carolB;
        assertApproxEqRel(carolExtra, avoided, 0.02e18, "what the exiting supplier avoided is borne by the remaining supplier");
        emit log_named_uint("avoided by the exiter, bp of its 200 USDG (gap of about -20%)", avoided * 10_000 / 200e6);
    }

    // ------------------------------------------------------------------ 3. repeated exits and refills
    function testRepeatedExitAndRefillAcrossGapsAccumulates() public {
        _openAndAnchor();
        uint256 cumulativeAvoided;
        uint256 carolStart = _usdgClaim(carol);
        for (uint256 i; i < 3; i++) {
            if (i > 0) {
                // a new fresh answer re-anchors for the next closure
                vm.prank(keeper);
                try vault.recenter(PAIR, vm.getBlockTimestamp()) { } catch { }
                vm.warp(vm.getBlockTimestamp() + 1 hours);
                feed.set(feed.answer());
                oracle.poke();
            }
            uint256 tAnchor = vm.getBlockTimestamp();
            uint256 rateBefore = uMarket.exchangeRateStored();
            vm.warp(tAnchor + 13 hours);
            cal.setClosed(true);
            uint256 got = _exit(bob);
            _resolveGap(600); // each closure ends with a -6% gap that the pool did not show
            // shift() set the feed to the new price; bring the LP back to a deployed state for the next round
            uint256 stayValue = got * uMarket.exchangeRateStored() / rateBefore;
            uint256 avoided = got > stayValue ? got - stayValue : 0;
            cumulativeAvoided += avoided;
            emit log_named_uint("cycle", i);
            emit log_named_uint("  exit proceeds (raw USDG)", got);
            emit log_named_uint("  value of the same position had it stayed (raw USDG)", stayValue);
            emit log_named_uint("  loss avoided this cycle (raw USDG)", avoided);
            // refill after the gap is visible, at the post-gap exchange rate
            usdg.mint(bob, got);
            vm.prank(bob);
            uMarket.mint(got);
            cal.setClosed(false);
            t0 = vm.getBlockTimestamp();
        }
        emit log_named_uint("cumulative loss avoided over three closures (raw USDG)", cumulativeAvoided);
        emit log_named_uint("carol claim at the start", carolStart);
        emit log_named_uint("carol claim at the end", _usdgClaim(carol));
        assertGt(cumulativeAvoided, 0, "repeating the exit/refill pattern avoids loss each time");
    }
    // ------------------------------------------------------------------ 4. both borrowing directions: collateral redemption priced by the fallback
    /// @dev Stock collateral, USDG debt. The market truly gaps down while the pool sits still; a redemption priced as an EXIT passes at the stale
    /// price and leaves the account under water once the gap shows. Priced as BORROW (the class a debt-bearing liquidity check should use) it fails closed.
    function testStockCollateralRedemptionAtTheStalePriceLeavesTheAccountUnderwater() public {
        _openAndAnchor();
        enableBorrow();
        vm.prank(alice);
        uMarket.borrow(50e6);
        _goStale(13, true);
        shim.setOp(AnchoredFallbackOracle.Op.BORROW);
        vm.prank(alice);
        vm.expectRevert();
        sMarket.redeemUnderlying{ gas: 12_000_000 }(0.33e18);          // fail-closed when classed as a borrow-risk check
        shim.setOp(AnchoredFallbackOracle.Op.EXIT);
        vm.prank(alice);
        sMarket.redeemUnderlying{ gas: 12_000_000 }(0.33e18);          // admitted at the stale price
        (,, uint256 shortfallBefore) = controller.getAccountLiquidity(alice);
        assertEq(shortfallBefore, 0, "healthy at the stale estimate");
        _resolveGap(2200);                                              // the market had gapped down about 20%
        (,, uint256 shortfallAfter) = controller.getAccountLiquidity(alice);
        emit log_named_uint("shortfall after the gap (USD 1e18)", shortfallAfter);
        assertGt(shortfallAfter, 0, "the redemption was only possible at the stale price: the account is now under water by the amount of the stale excess");
    }

    /// @dev USDG collateral, stock debt: the market truly gaps UP while the pool sits still, which undervalues the debt.
    function testUsdgCollateralRedemptionAtTheStalePriceLeavesTheAccountUnderwater() public {
        _openAndAnchor();
        enableBorrow();
        address[] memory ms = new address[](1);
        ms[0] = usdgAccount;
        vm.prank(bob);
        controller.enterMarkets(ms);
        vm.prank(bob);
        sMarket.borrow(0.2e18);                                         // 20 USD of stock at the anchor
        _goStale(13, true);
        shim.setOp(AnchoredFallbackOracle.Op.BORROW);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 12_000_000 }(130e6);
        shim.setOp(AnchoredFallbackOracle.Op.EXIT);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 12_000_000 }(130e6);
        (,, uint256 before_) = controller.getAccountLiquidity(bob);
        assertEq(before_, 0);
        _resolveGap(-2231);                                             // the stock truly rose about 25%
        (,, uint256 after_) = controller.getAccountLiquidity(bob);
        emit log_named_uint("shortfall after the gap (USD 1e18)", after_);
        assertGt(after_, 0, "under water: the debt was valued at the stale price");
    }

    // ------------------------------------------------------------------ 5. liquidation: max(anchor, fallback) is one-directional
    function _pushPool(int24 stockUpTicks) internal {
        pool.setTick(pool.tick() + (stockFirst() ? stockUpTicks : -stockUpTicks));
        fbPool.setTick(pool.tick());
    }

    function _liqOracle() internal returns (FallbackFeedShim s2) {
        AnchoredFallbackOracle o2 = new AnchoredFallbackOracle(address(this), address(0xBEEF), _cfg(true));
        o2.poke();
        s2 = new FallbackFeedShim(o2);
        s2.setOp(AnchoredFallbackOracle.Op.LIQUIDATE);
        guard.configurePair(
            PAIR,
            RamsesOracleGuard.FeedConfig(
                address(stock), address(usdg), IAggregatorV3(address(s2)), IRamsesPool(address(pool)), IRamsesGauge(address(gauge)), 12 hours, 300, 600, 18, 6, 8, true
            )
        );
    }

    function testFlooringAtTheAnchorProtectsStockCollateralFromADownwardPushButNotStockDebtFromAnUpwardOne() public {
        _openAndAnchor();
        enableBorrow();
        vm.prank(alice);
        uMarket.borrow(59e6);                                            // stock collateral, close to the 60 limit
        address[] memory ms = new address[](1);
        ms[0] = usdgAccount;
        vm.prank(bob);
        controller.enterMarkets(ms);
        vm.prank(bob);
        sMarket.borrow(0.59e18);                                         // stock DEBT worth 59 against 60 of limit
        FallbackFeedShim s2 = _liqOracle();
        _goStale(13, true);
        // (a) the pool is pushed DOWN 1.9% for the whole window while the market is unchanged
        _pushPool(-190);
        vm.warp(vm.getBlockTimestamp() + 1800);
        s2.setOp(AnchoredFallbackOracle.Op.EXIT);
        (,, uint256 aliceExit) = controller.getAccountLiquidity(alice);
        s2.setOp(AnchoredFallbackOracle.Op.LIQUIDATE);
        (,, uint256 aliceLiq) = controller.getAccountLiquidity(alice);
        emit log_named_uint("stock-collateral borrower shortfall at the unfloored estimate", aliceExit);
        emit log_named_uint("stock-collateral borrower shortfall at max(anchor, fallback)", aliceLiq);
        assertEq(aliceLiq, 0, "the floor protects stock collateral from a downward push");
        if (!holdMode()) assertGt(aliceExit, 0, "without the floor the same push would make the borrower liquidatable");
        // (b) the pool is pushed UP 3.8% in total (1.9% beyond the first): the stock DEBT is now priced above the anchor and no floor applies
        _pushPool(380);
        vm.warp(vm.getBlockTimestamp() + 1800);
        (,, uint256 bobLiq) = controller.getAccountLiquidity(bob);
        emit log_named_uint("stock-debt borrower shortfall at max(anchor, fallback), market unchanged", bobLiq);
        if (holdMode()) assertEq(bobLiq, 0, "hold mode never moves the price");
        else assertGt(bobLiq, 0, "the floor does NOT prevent a false liquidation of stock debt: an upward push makes a healthy borrower liquidatable");
    }
}

contract FallbackEconomicsStockSecondAnchoredTest is FallbackEconomicsBase { }

contract FallbackEconomicsStockFirstAnchoredTest is FallbackEconomicsBase {
    function stockFirst() internal pure override returns (bool) {
        return true;
    }
}

contract FallbackEconomicsStockSecondHoldTest is FallbackEconomicsBase {
    function holdMode() internal pure override returns (bool) {
        return true;
    }
}

contract FallbackEconomicsStockFirstHoldTest is FallbackEconomicsBase {
    function stockFirst() internal pure override returns (bool) {
        return true;
    }

    function holdMode() internal pure override returns (bool) {
        return true;
    }
}
