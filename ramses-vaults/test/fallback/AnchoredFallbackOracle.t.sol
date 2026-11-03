// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { AnchoredFallbackOracle, IFallbackFeed, IFallbackPool, IFallbackStock, IClosureCalendar } from "./AnchoredFallbackOracle.sol";

contract FbFeed {
    uint8 public constant decimals = 8;
    int256 public answer;
    uint256 public updatedAt;
    uint80 public round = 1;
    uint80 public answered = 1;

    function set(int256 a) external {
        answer = a;
        updatedAt = block.timestamp;
        round++;
        answered = round;
    }

    function setRounds(uint80 r, uint80 a) external {
        round = r;
        answered = a;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return (round, answer, updatedAt, updatedAt, answered);
    }
}

contract FbStock {
    bool public paused;
    bool public oraclePaused;
    uint256 public uiMultiplier = 1.001718e18;

    function setPaused(bool p, bool o) external {
        paused = p;
        oraclePaused = o;
    }

    function setMultiplier(uint256 m) external {
        uiMultiplier = m;
    }
}

contract FbCalendar {
    bool public closed;

    function setClosed(bool c) external {
        closed = c;
    }

    function isClosed(uint256) external view returns (bool) {
        return closed;
    }
}

/// @dev Piecewise-constant tick path with an observation at every change; observe() integrates it, as the pool's oracle does.
contract FbPool {
    struct Snap {
        uint256 ts;
        int256 cum;
        int24 tick;
    }

    Snap[] public snaps;

    constructor(uint256 ts, int24 tick) {
        snaps.push(Snap(ts, 0, tick));
    }

    function setTick(int24 tick) external {
        Snap memory l = snaps[snaps.length - 1];
        snaps.push(Snap(block.timestamp, l.cum + int256(l.tick) * int256(block.timestamp - l.ts), tick));
    }

    function observe(uint32[] calldata secondsAgos) external view returns (int56[] memory cum, uint160[] memory spl) {
        cum = new int56[](secondsAgos.length);
        spl = new uint160[](secondsAgos.length);
        for (uint256 i; i < secondsAgos.length; i++) {
            uint256 target = block.timestamp - secondsAgos[i];
            require(target >= snaps[0].ts, "OLD");
            uint256 k = snaps.length;
            while (snaps[k - 1].ts > target) k--;
            Snap memory s = snaps[k - 1];
            cum[i] = int56(s.cum + int256(s.tick) * int256(target - s.ts));
        }
    }
}

/// @notice Unit tests of the TEST-ONLY anchored fallback prototype. Orientation-agnostic: the same suite runs with the stock as token0 and token1.
abstract contract AnchoredFallbackBase is Test {
    uint256 constant T0 = 1_800_000_000; // arbitrary
    uint32 constant H = 3600;

    FbFeed feed;
    FbStock stock;
    FbPool pool;
    FbCalendar cal;
    AnchoredFallbackOracle o;
    int24 base; // pool tick at a $200 price in the chosen orientation

    function stockIsToken0() internal pure virtual returns (bool);

    function holdMode() internal pure virtual returns (bool) {
        return false;
    }

    function sgn(int24 t) internal pure returns (int24) {
        return stockIsToken0() ? t : -t; // a stock price rise is a tick rise when the stock is token0, a fall when it is token1
    }

    function setUp() public {
        vm.warp(T0 - 6 * H);
        base = stockIsToken0() ? int24(-223_338) : int24(223_338);
        feed = new FbFeed();
        stock = new FbStock();
        cal = new FbCalendar();
        pool = new FbPool(block.timestamp, base);
        AnchoredFallbackOracle.Config memory c = AnchoredFallbackOracle.Config({
            feed: IFallbackFeed(address(feed)),
            stock: IFallbackStock(address(stock)),
            pool: IFallbackPool(address(pool)),
            calendar: IClosureCalendar(address(cal)),
            stockIsToken0: stockIsToken0(),
            stockDecimals: 18,
            usdgDecimals: 6,
            twapWindow: 1800,
            maxStaleness: 12 hours,
            maxPokeDelay: 4 hours,
            maxClosureDuration: 72 hours,
            maxOpenDuration: 24 hours,
            maxMoveBps: 200,
            maxBasisBps: 100,
            liquidationInFallback: false,
            mode: holdMode() ? AnchoredFallbackOracle.Mode.HOLD : AnchoredFallbackOracle.Mode.ANCHORED
        });
        o = new AnchoredFallbackOracle(address(this), address(0xBEEF), c);
        vm.warp(T0);
        feed.set(200e8);
    }

    function _anchor() internal {
        o.poke();
    }

    function _staleBy(uint256 age) internal {
        vm.warp(T0 + age);
    }

    function _move(int24 stockTicks) internal {
        pool.setTick(base + sgn(stockTicks));
    }

    // ------------------------------------------------------------------ primary behaviour
    function testFreshFeedIsTheOnlySourceAndTheMultiplierIsNotAppliedAgain() public {
        (uint256 p, AnchoredFallbackOracle.Source s) = o.price(AnchoredFallbackOracle.Op.EXIT);
        assertEq(p, 200e18, "feed answer, 8 decimals scaled to 1e18, untouched by uiMultiplier");
        assertTrue(s == AnchoredFallbackOracle.Source.FEED);
        stock.setMultiplier(1.5e18);
        (p,) = o.price(AnchoredFallbackOracle.Op.BORROW); // fresh feed: every class is allowed
        assertEq(p, 200e18, "a changed multiplier does not alter a fresh feed price");
    }

    function testAnchorRecordsOriginalTimestampAndPoolTwapAtThatTime() public {
        vm.warp(T0 + 2 * H);
        _move(150); // the pool moves AFTER the answer
        vm.warp(T0 + 3 * H);
        _anchor();
        (uint256 price18, uint64 at, uint256 mult, int24 tick) = o.anchor();
        assertEq(price18, 200e18);
        assertEq(uint256(at), T0, "anchored to the feed's own timestamp, not to the poke time");
        assertEq(mult, 1.001718e18);
        assertEq(tick, base, "TWAP window ends at the answer's timestamp, so the later move is excluded");
    }

    function testPokeRefusesStaleAnswerAndDuplicates() public {
        _anchor();
        vm.expectRevert(AnchoredFallbackOracle.AnchorCurrent.selector);
        o.poke();
        _staleBy(12 hours + 1);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.StaleForAnchor.selector, T0));
        o.poke();
    }

    function testPokeRefusesWhenThePoolDoesNotTrackTheFeed() public {
        vm.warp(T0 + 1);
        pool.setTick(base + sgn(300)); // pool 3% away from the feed for the whole window
        vm.warp(T0 + 2 hours);
        feed.set(200e8);
        vm.warp(T0 + 2 hours + 1800);
        vm.expectPartialRevert(AnchoredFallbackOracle.BasisTooWide.selector);
        o.poke();
    }

    function testPokeRevertsWhenThePoolHistoryIsTooShort() public {
        // a pool whose oldest observation is only 100 s old, like GLD/r33 with cardinality 1
        FbPool shortPool = new FbPool(block.timestamp - 100, base);
        AnchoredFallbackOracle.Config memory c = _cfgWith(address(shortPool));
        AnchoredFallbackOracle x = new AnchoredFallbackOracle(address(this), address(0xBEEF), c);
        vm.expectRevert(AnchoredFallbackOracle.NoPoolHistory.selector);
        x.poke();
    }

    function _cfgWith(address p) internal view returns (AnchoredFallbackOracle.Config memory c) {
        (IFallbackFeed f, IFallbackStock s,, IClosureCalendar k,,,,,,,,,,,,) = o.cfg();
        c = AnchoredFallbackOracle.Config(f, s, IFallbackPool(p), k, stockIsToken0(), 18, 6, 1800, 12 hours, 4 hours, 72 hours, 24 hours, 200, 100, false, holdMode() ? AnchoredFallbackOracle.Mode.HOLD : AnchoredFallbackOracle.Mode.ANCHORED);
    }

    // ------------------------------------------------------------------ fallback
    function testFallbackIsAnchorTimesTheRelativePoolMove() public {
        _anchor();
        vm.warp(T0 + 8 hours);
        _move(100); // +1.005% in the stock price
        _staleBy(13 hours);
        (uint256 p, AnchoredFallbackOracle.Source s) = o.price(AnchoredFallbackOracle.Op.EXIT);
        assertTrue(s == AnchoredFallbackOracle.Source.FALLBACK);
        if (holdMode()) {
            assertEq(p, 200e18, "hold mode: the anchor price flat; the pool move only gates");
            (p,) = o.price(AnchoredFallbackOracle.Op.SETTLE);
            assertEq(p, 200e18);
            return;
        }
        assertApproxEqRel(p, 200e18 * 1.0100496620928754e18 / 1e18, 1e12, "anchor x 1.0001^100");
        (p,) = o.price(AnchoredFallbackOracle.Op.SETTLE);
        assertApproxEqRel(p, 202.00993e18, 1e13);
    }

    function testWithoutAMoveTheFallbackEqualsTheAnchor() public {
        _anchor();
        _staleBy(30 hours);
        cal.setClosed(true);
        (uint256 p,) = o.price(AnchoredFallbackOracle.Op.EXIT);
        assertEq(p, 200e18, "the pool's static basis cancels out of the relative move");
    }

    function testFallbackNeverReanchorsFromItself() public {
        _anchor();
        (uint256 p0, uint64 t0,, int24 k0) = o.anchor();
        vm.warp(T0 + 8 hours);
        _move(50);
        _staleBy(14 hours);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.StaleForAnchor.selector, T0));
        o.poke(); // no path writes an anchor from a stale feed
        (uint256 p1, uint64 t1,, int24 k1) = o.anchor();
        assertEq(p1, p0);
        assertEq(uint256(t1), uint256(t0));
        assertEq(k1, k0);
    }

    function testAMissedPokeMeansNoFallback() public {
        // a newer answer arrived and was never anchored (for example, the pool history was too short when it was fresh)
        _anchor();
        vm.warp(T0 + 10 hours);
        feed.set(201e8);
        _staleBy(24 hours); // the new answer is 14 h old; the stored anchor is for the older one
        vm.expectPartialRevert(AnchoredFallbackOracle.AnchorMissing.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    function testDurationBoundsDifferByClosure() public {
        _anchor();
        _staleBy(25 hours);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.FallbackExpired.selector, 25 hours, 24 hours));
        o.price(AnchoredFallbackOracle.Op.EXIT); // open session: 24 h
        cal.setClosed(true);
        o.price(AnchoredFallbackOracle.Op.EXIT); // scheduled closure: up to 72 h
        _staleBy(73 hours);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.FallbackExpired.selector, 73 hours, 72 hours));
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    function testDeviationBoundIsEnforcedBothWays() public {
        _anchor();
        vm.warp(T0 + 8 hours);
        _move(250); // +2.53% > 2%
        _staleBy(13 hours);
        vm.expectPartialRevert(AnchoredFallbackOracle.MoveExceedsBound.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.warp(T0 + 14 hours);
        _move(-250);
        _staleBy(16 hours);
        vm.expectPartialRevert(AnchoredFallbackOracle.MoveExceedsBound.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.warp(T0 + 17 hours);
        _move(190); // +1.92% inside the bound
        _staleBy(19 hours);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    // ------------------------------------------------------------------ pauses, corporate actions
    function testIssuerPausesBlockEverythingEvenWithAFreshFeed() public {
        _anchor();
        stock.setPaused(false, true);
        for (uint256 i; i < 5; i++) {
            vm.expectRevert(AnchoredFallbackOracle.IssuerPaused.selector);
            o.price(AnchoredFallbackOracle.Op(i));
        }
        stock.setPaused(true, false);
        vm.expectRevert(AnchoredFallbackOracle.IssuerPaused.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        stock.setPaused(false, false);
        _staleBy(14 hours);
        stock.setPaused(false, true);
        vm.expectRevert(AnchoredFallbackOracle.IssuerPaused.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT); // a pause during the fallback ends it at once
        vm.expectRevert(AnchoredFallbackOracle.IssuerPaused.selector);
        o.poke();
    }

    function testMultiplierChangeAfterTheAnchorHaltsTheFallback() public {
        _anchor();
        _staleBy(14 hours);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        stock.setMultiplier(2 * 1.001718e18); // a split-style change; the old feed answer is now in the wrong units
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.MultiplierChanged.selector, 1.001718e18, 2 * 1.001718e18));
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    // ------------------------------------------------------------------ operation classes
    function testNewBorrowingAndAllocationNeverUseTheFallback() public {
        _anchor();
        _staleBy(14 hours);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.FallbackNotAllowedFor.selector, AnchoredFallbackOracle.Op.BORROW));
        o.price(AnchoredFallbackOracle.Op.BORROW);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.FallbackNotAllowedFor.selector, AnchoredFallbackOracle.Op.ALLOCATE));
        o.price(AnchoredFallbackOracle.Op.ALLOCATE);
    }

    function testLiquidationIsOffByDefaultAndWhenEnabledNeverPricesBelowTheAnchor() public {
        _anchor();
        vm.warp(T0 + 8 hours);
        _move(-150); // the pool falls 1.5%: a real fall and a manipulated one look the same
        _staleBy(14 hours);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.FallbackNotAllowedFor.selector, AnchoredFallbackOracle.Op.LIQUIDATE));
        o.price(AnchoredFallbackOracle.Op.LIQUIDATE);

        AnchoredFallbackOracle.Config memory c = _cfgWith(address(pool));
        c.liquidationInFallback = true;
        AnchoredFallbackOracle x = new AnchoredFallbackOracle(address(this), address(0xBEEF), c);
        vm.warp(T0);
        // re-create the same history for x: its anchor is taken from the same fresh answer
        vm.warp(T0 + 0);
        x.poke();
        _staleBy(14 hours);
        (uint256 liq,) = x.price(AnchoredFallbackOracle.Op.LIQUIDATE);
        (uint256 exitP,) = x.price(AnchoredFallbackOracle.Op.EXIT);
        assertEq(liq, 200e18, "liquidation sees max(anchor, fallback): a fall cannot cause a liquidation");
        if (holdMode()) assertEq(exitP, 200e18, "hold mode never adjusts the price");
        else assertLt(exitP, 200e18, "an exit does take the lower estimate");
        // the cost of that choice is explicit: a REAL fall is not liquidated against until the feed or the duration limit resolves it
    }

    function testPokeAfterTheMaximumDelayIsRefused() public {
        vm.warp(T0 + 4 hours + 1);
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.PokeTooLate.selector, 4 hours + 1, 4 hours));
        o.poke();
        vm.warp(T0 + 4 hours);
        o.poke();
    }

    function testGuardianCanOnlyDisableTheFallback() public {
        _anchor();
        _staleBy(14 hours);
        vm.prank(address(0xBEEF));
        o.disableFallback();
        vm.expectRevert(AnchoredFallbackOracle.FallbackDisabled.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.prank(address(0xBEEF));
        vm.expectRevert(AnchoredFallbackOracle.NotAuthorized.selector);
        o.enableFallback();
        o.enableFallback(); // admin (this contract)
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    // ------------------------------------------------------------------ manipulation: a spot spike barely moves the TWAP
    function testShortSpikeBarelyMovesTheWindowAverage() public {
        _anchor();
        vm.warp(T0 + 13 hours - 60);
        _move(1000); // +10.5% for 12 seconds only (one L2 block-scale manipulation held briefly)
        vm.warp(T0 + 13 hours - 48);
        _move(0);
        vm.warp(T0 + 13 hours);
        (uint256 p,) = o.price(AnchoredFallbackOracle.Op.EXIT);
        // spike contributes 1000 ticks x 12 s / 1800 s = 6.7 ticks of the window mean (about 0.07%)
        assertApproxEqRel(p, 200e18, 0.0015e18);
        // while a sustained manipulation that lasts the window is bounded by the deviation limit and reverts
        _move(1000);
        vm.warp(T0 + 13 hours + 1800);
        vm.expectPartialRevert(AnchoredFallbackOracle.MoveExceedsBound.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    function testInvalidFeedAnswersFailClosed() public {
        feed.setRounds(5, 4);
        vm.expectRevert(AnchoredFallbackOracle.InvalidAnswer.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }
}

contract AnchoredFallbackStockToken0Test is AnchoredFallbackBase {
    function stockIsToken0() internal pure override returns (bool) {
        return true;
    }
}

contract AnchoredFallbackStockToken1Test is AnchoredFallbackBase {
    function stockIsToken0() internal pure override returns (bool) {
        return false;
    }
}

contract AnchoredFallbackHoldToken0Test is AnchoredFallbackBase {
    function stockIsToken0() internal pure override returns (bool) {
        return true;
    }

    function holdMode() internal pure override returns (bool) {
        return true;
    }
}

contract AnchoredFallbackHoldToken1Test is AnchoredFallbackBase {
    function stockIsToken0() internal pure override returns (bool) {
        return false;
    }

    function holdMode() internal pure override returns (bool) {
        return true;
    }
}
