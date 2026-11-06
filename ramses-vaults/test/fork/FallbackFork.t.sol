// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { IRamsesPool } from "../../src/interfaces/IRamsesV3.sol";
import { ForkTrader } from "./AdapterFork.t.sol";
import { AnchoredFallbackOracle, IFallbackFeed, IFallbackPool, IFallbackStock, IClosureCalendar } from "../fallback/AnchoredFallbackOracle.sol";
import { FbCalendar } from "../fallback/AnchoredFallbackOracle.t.sol";

interface IUiMultiplierToken {
    function uiMultiplier() external view returns (uint256);
}

/// @notice Phase 0 integration of the TEST-ONLY anchored fallback against the REAL SPY / NVDA feed, token and Ramses pool on a mainnet fork. Nothing is
/// deployed anywhere; the oracle lives only in the fork. The closure calendar is a test double (the operator-maintained schedule does not exist).
/// Time is moved with `vm.warp`, so the real pool's `observe()` extrapolates its last tick exactly as it would with no swaps in the interval (a closed
/// market); swaps by a funded test trader stand in for an adversary. Multiplier steps are written into the token's storage slot (as in
/// StockMultiplierFork) because no real step can be forced on a fork.
///
/// FAIL-CLOSED CLASSES (no generic catch-all anywhere in the prototype): every failure listed here reverts and no operation obtains a price.
///   feed call reverts                    -> the feed's own revert bubbles (not caught)             testFeedCallFailureBubbles
///   feed returns <=0, 0, future, bad round -> InvalidAnswer                                         testMalformedFeedAnswersFailClosed
///   token paused()/oraclePaused() reverts -> bubbles; flag true -> IssuerPaused, EVEN FOR A FRESH FEED testIssuerPauseBlocksEvenAFreshFeed
///   uiMultiplier() differs from the anchor -> MultiplierChanged                                     testMultiplierStepAfterTheAnchor
///   pool observe() cannot serve the window -> NoPoolHistory (the one try/catch, which re-reverts)   testPoolHistoryFailureIsASpecificError
///   calendar call reverts                -> bubbles                                                 testCalendarFailureBubbles
///   anchor older/newer than the latest answer, expired, move beyond L, op class not allowed -> their own errors (unit tests)
abstract contract FallbackForkBase is Test {
    AnchoredFallbackOracle internal o;
    FbCalendar internal cal;
    address internal feedAddr;
    address internal stockAddr;
    address internal poolAddr;
    address internal usdgAddr;
    bool internal stockIsToken0;
    uint32 internal constant W = 1800;
    ForkTrader internal trader;

    function _pair() internal virtual;

    function setUp() public {
        _pair();
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        stockAddr = stockIsToken0 ? IRamsesPool(poolAddr).token0() : IRamsesPool(poolAddr).token1();
        usdgAddr = stockIsToken0 ? IRamsesPool(poolAddr).token1() : IRamsesPool(poolAddr).token0();
        cal = new FbCalendar();
        o = new AnchoredFallbackOracle(address(this), address(0xBEEF), _cfg());
        trader = new ForkTrader(IRamsesPool(poolAddr));
        deal(stockAddr, address(trader), 1_000_000e18);
        deal(usdgAddr, address(trader), 100_000_000e6);
    }

    function _cfg() internal view returns (AnchoredFallbackOracle.Config memory) {
        return AnchoredFallbackOracle.Config({
            feed: IFallbackFeed(feedAddr),
            stock: IFallbackStock(stockAddr),
            pool: IFallbackPool(poolAddr),
            calendar: IClosureCalendar(address(cal)),
            stockIsToken0: stockIsToken0,
            stockDecimals: 18,
            usdgDecimals: 6,
            twapWindow: W,
            maxStaleness: 12 hours,
            maxPokeDelay: 1 hours,
            maxClosureDuration: 72 hours,
            maxOpenDuration: 24 hours,
            maxMoveBps: 200,
            maxBasisBps: 300,        // looser than the unit tests: the real NVDA pool-over-feed basis has a p99 of several percent (README 16D)
            liquidationInFallback: false,
            mode: AnchoredFallbackOracle.Mode.ANCHORED
        });
    }

    function _feedState() internal view returns (uint256 answer, uint256 updatedAt) {
        (, int256 a,, uint256 u,) = IAggregatorV3(feedAddr).latestRoundData();
        return (uint256(a), u);
    }

    function _multiplierSlot() internal returns (bytes32 slot, uint256 m0) {
        m0 = IUiMultiplierToken(stockAddr).uiMultiplier();
        vm.record();
        IUiMultiplierToken(stockAddr).uiMultiplier();
        (bytes32[] memory reads,) = vm.accesses(stockAddr);
        for (uint256 i; i < reads.length; i++) {
            if (uint256(vm.load(stockAddr, reads[i])) == m0) return (reads[i], m0);
        }
        revert("multiplier slot not found");
    }

    /// @dev Mean tick over [now-ago-W, now-ago] read directly from the real pool, independent of the prototype's arithmetic.
    function _poolMeanTick(uint32 ago) internal view returns (int24) {
        uint32[] memory s = new uint32[](2);
        s[0] = ago + W;
        s[1] = ago;
        (int56[] memory c,) = IFallbackPool(poolAddr).observe(s);
        int56 d = c[1] - c[0];
        int56 w = int56(uint56(W));
        int56 t = d / w;
        if (d < 0 && d % w != 0) t--;
        return int24(t);
    }

    // ------------------------------------------------------------------ anchoring on the real contracts
    function testPokeAnchorsTheRealAnswerAtItsOwnTimestamp() public {
        (uint256 answer, uint256 updatedAt) = _feedState();
        uint256 age = block.timestamp - updatedAt;
        emit log_named_uint("real feed age at the pin (s)", age);
        assertLt(age, 12 hours, "the pin must fall inside the feed's freshness window");
        if (age > 1 hours) vm.warp(updatedAt + 1 hours - 1); // pokes must follow the answer within maxPokeDelay; only time moves, the pool is read as of then
        o.poke();
        (uint256 p, uint64 at, uint256 mult, int24 tick) = o.anchor();
        assertEq(uint256(at), updatedAt, "anchored to the feed's own timestamp");
        assertEq(p, answer * 1e10, "the real answer, 8 decimals scaled to 1e18, the multiplier not applied again");
        assertEq(mult, IUiMultiplierToken(stockAddr).uiMultiplier(), "the multiplier read once");
        assertEq(tick, _poolMeanTick(uint32(block.timestamp - updatedAt)), "the TWAP window ends at the answer, equal to the pool's own observe() arithmetic");
    }

    function testFallbackFollowsTheRealPoolAcrossSessionTransitions() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 30 minutes);
        o.poke();
        (uint256 p0,,,) = o.anchor();
        vm.warp(updatedAt + 13 hours);
        (uint256 p, AnchoredFallbackOracle.Source s) = o.price(AnchoredFallbackOracle.Op.EXIT);
        assertTrue(s == AnchoredFallbackOracle.Source.FALLBACK, "stale beyond 12 h: fallback");
        assertApproxEqRel(p, p0, 0.02e18, "no swaps in the interval: the estimate stays within the move limit of the anchor");
        // the open-session budget (24 h) ends; a scheduled closure extends it to 72 h; the closure ending puts the open budget back
        vm.warp(updatedAt + 30 hours);
        vm.expectPartialRevert(AnchoredFallbackOracle.FallbackExpired.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        cal.setClosed(true);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.warp(updatedAt + 73 hours);
        vm.expectPartialRevert(AnchoredFallbackOracle.FallbackExpired.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.warp(updatedAt + 60 hours);
        cal.setClosed(false); // the market reopens with the anchor still the latest answer: the OPEN budget applies at once
        vm.expectPartialRevert(AnchoredFallbackOracle.FallbackExpired.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    // ------------------------------------------------------------------ delayed pokes across multiplier steps
    function testMultiplierStepAfterTheAnchor() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        vm.warp(updatedAt + 13 hours);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        (bytes32 slot, uint256 m0) = _multiplierSlot();
        vm.store(stockAddr, slot, bytes32(m0 * 10033 / 10000)); // a quarterly-size step
        vm.expectRevert(abi.encodeWithSelector(AnchoredFallbackOracle.MultiplierChanged.selector, m0, m0 * 10033 / 10000));
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    /// @dev THE RESIDUAL HOLE, measured: a step that lands between the answer and a (delayed but permitted) poke is invisible on chain, because the
    /// multiplier can only be read now. The anchor then pairs a pre-step answer with the post-step multiplier and the fallback proceeds. maxPokeDelay
    /// bounds the window (1 h here); it does not close it. A poke beyond the bound is refused.
    function testDelayedPokeAcrossAMultiplierStepIsInvisibleWithinTheDelayBoundAndRefusedBeyondIt() public {
        (, uint256 updatedAt) = _feedState();
        (bytes32 slot, uint256 m0) = _multiplierSlot();
        uint256 m1 = m0 * 10033 / 10000;
        vm.warp(updatedAt + 5 minutes);
        vm.store(stockAddr, slot, bytes32(m1));       // the step happens AFTER the answer
        vm.warp(updatedAt + 45 minutes);
        o.poke();                                     // permitted: within maxPokeDelay
        (,, uint256 anchoredMult,) = o.anchor();
        assertEq(anchoredMult, m1, "the anchor records the post-step multiplier against a pre-step answer");
        vm.warp(updatedAt + 13 hours);
        o.price(AnchoredFallbackOracle.Op.EXIT);      // the fallback proceeds: the hole
        // beyond the bound the poke is refused
        AnchoredFallbackOracle fresh = new AnchoredFallbackOracle(address(this), address(0xBEEF), _cfg());
        vm.warp(updatedAt + 1 hours + 1);
        vm.expectPartialRevert(AnchoredFallbackOracle.PokeTooLate.selector);
        fresh.poke();
    }

    // ------------------------------------------------------------------ fail-closed classes
    function testFeedCallFailureBubbles() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        vm.warp(updatedAt + 13 hours);
        vm.mockCallRevert(feedAddr, abi.encodeWithSelector(IAggregatorV3.latestRoundData.selector), "FEED_DOWN");
        vm.expectRevert("FEED_DOWN");
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.expectRevert("FEED_DOWN");
        o.poke();
    }

    function testMalformedFeedAnswersFailClosed() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        vm.warp(updatedAt + 13 hours);
        bytes4 sel = IAggregatorV3.latestRoundData.selector;
        vm.mockCall(feedAddr, abi.encodeWithSelector(sel), abi.encode(uint80(5), int256(0), updatedAt, updatedAt, uint80(5)));
        vm.expectRevert(AnchoredFallbackOracle.InvalidAnswer.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.mockCall(feedAddr, abi.encodeWithSelector(sel), abi.encode(uint80(5), int256(-1), updatedAt, updatedAt, uint80(5)));
        vm.expectRevert(AnchoredFallbackOracle.InvalidAnswer.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.mockCall(feedAddr, abi.encodeWithSelector(sel), abi.encode(uint80(5), int256(100e8), block.timestamp + 1, block.timestamp + 1, uint80(5)));
        vm.expectRevert(AnchoredFallbackOracle.InvalidAnswer.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.mockCall(feedAddr, abi.encodeWithSelector(sel), abi.encode(uint80(5), int256(100e8), updatedAt, updatedAt, uint80(4)));
        vm.expectRevert(AnchoredFallbackOracle.InvalidAnswer.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        // a NEWER answer that was never anchored: a fresh-looking round for which no anchor exists
        vm.mockCall(feedAddr, abi.encodeWithSelector(sel), abi.encode(uint80(6), int256(100e8), updatedAt + 30 minutes, updatedAt + 30 minutes, uint80(6)));
        vm.expectPartialRevert(AnchoredFallbackOracle.AnchorMissing.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    function testIssuerPauseBlocksEvenAFreshFeed() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        vm.mockCall(stockAddr, abi.encodeWithSignature("oraclePaused()"), abi.encode(true));
        for (uint256 i; i < 5; i++) {
            vm.expectRevert(AnchoredFallbackOracle.IssuerPaused.selector);
            o.price(AnchoredFallbackOracle.Op(i));
        }
        vm.mockCall(stockAddr, abi.encodeWithSignature("oraclePaused()"), abi.encode(false));
        vm.mockCall(stockAddr, abi.encodeWithSignature("paused()"), abi.encode(true));
        vm.expectRevert(AnchoredFallbackOracle.IssuerPaused.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
        vm.mockCallRevert(stockAddr, abi.encodeWithSignature("paused()"), "TOKEN_DOWN");
        vm.expectRevert("TOKEN_DOWN");
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    function testPoolHistoryFailureIsASpecificError() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        vm.warp(updatedAt + 13 hours);
        vm.mockCallRevert(poolAddr, abi.encodeWithSelector(IFallbackPool.observe.selector), "OLD");
        vm.expectRevert(AnchoredFallbackOracle.NoPoolHistory.selector);
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    function testCalendarFailureBubbles() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        vm.warp(updatedAt + 13 hours);
        vm.mockCallRevert(address(cal), abi.encodeWithSelector(IClosureCalendar.isClosed.selector), "CALENDAR_DOWN");
        vm.expectRevert("CALENDAR_DOWN");
        o.price(AnchoredFallbackOracle.Op.EXIT);
    }

    // ------------------------------------------------------------------ manipulation of the real pool (no Ramses vault liquidity exists in it)
    /// @dev The pool is the real pool with only third-party liquidity, which is exactly the post-removal state. A funded trader pushes the price by real
    /// swaps and the push is held for the whole TWAP window (no arbitrage on a fork, as in a closed market). Prints the input the push consumed.
    function testSustainedPushMovesTheEstimateUpToTheLimitThenFailsClosed() public {
        (, uint256 updatedAt) = _feedState();
        vm.warp(updatedAt + 10 minutes);
        o.poke();
        (uint256 p0,,,) = o.anchor();
        vm.warp(updatedAt + 13 hours);
        (, int24 tick,,,,,) = IRamsesPool(poolAddr).slot0();
        // a push that raises the STOCK price by ~1%, then ~3% (relative to the tick at the start of the test)
        int24[2] memory pushes = [int24(100), int24(300)];
        for (uint256 i; i < 2; i++) {
            int24 offset = stockIsToken0 ? pushes[i] : -pushes[i];   // stock token0: tick up = stock up; stock token1: tick down = stock up
            uint256 b0u = IERC20(usdgAddr).balanceOf(address(trader));
            uint256 b0s = IERC20(stockAddr).balanceOf(address(trader));
            trader.move(offset < 0, TickMath.getSqrtPriceAtTick(tick + offset));
            uint256 spentUsdg = b0u - IERC20(usdgAddr).balanceOf(address(trader));
            emit log_named_int("pool tick offset applied", offset);
            emit log_named_uint("USDG-denominated input to the push (raw, positive = USDG paid in)", spentUsdg);
            emit log_named_uint("stock received (wei)", IERC20(stockAddr).balanceOf(address(trader)) - b0s);
            vm.warp(vm.getBlockTimestamp() + W);                      // hold it for the whole TWAP window (getBlockTimestamp: via-IR may cache block.timestamp across a warp)
            if (pushes[i] <= 100) {
                (uint256 p,) = o.price(AnchoredFallbackOracle.Op.EXIT);
                assertGt(p, p0, "the held push raises the estimate");
                assertLe(p, p0 * 10_200 / 10_000 + 1, "but not beyond the move limit");
                emit log_named_uint("estimate / anchor, bp", p * 10_000 / p0);
            } else {
                (bool ok, bytes memory ret) = address(o).staticcall(abi.encodeCall(AnchoredFallbackOracle.price, (AnchoredFallbackOracle.Op.EXIT)));
                emit log_named_uint("price() succeeded after the larger push (1 = yes)", ok ? 1 : 0);
                if (ok) {
                    (uint256 p2,) = abi.decode(ret, (uint256, AnchoredFallbackOracle.Source));
                    emit log_named_uint("estimate / anchor after the larger push, bp", p2 * 10_000 / p0);
                } else {
                    emit log_named_bytes32("revert selector", bytes32(bytes4(ret)));
                }
                assertFalse(ok, "a held push beyond the limit fails closed");
                assertEq(bytes4(ret), AnchoredFallbackOracle.MoveExceedsBound.selector);
            }
        }
    }
}

contract SpyFallbackForkTest is FallbackForkBase {
    function _pair() internal override {
        feedAddr = 0x319724394D3A0e3669269846abE664Cd621f9f6A;
        poolAddr = 0x38453c115607463Ac284820Ce959831042f3Df4E;
        stockIsToken0 = true;
    }
}

contract NvdaFallbackForkTest is FallbackForkBase {
    function _pair() internal override {
        feedAddr = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
        poolAddr = 0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b;
        stockIsToken0 = false;
    }
}
