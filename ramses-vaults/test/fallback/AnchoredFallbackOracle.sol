// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { FullMath } from "@uniswap/v4-core/src/libraries/FullMath.sol";

interface IFallbackFeed {
    function decimals() external view returns (uint8);
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
}

interface IFallbackPool {
    function observe(uint32[] calldata secondsAgos)
        external
        view
        returns (int56[] memory tickCumulatives, uint160[] memory secondsPerLiquidityCumulativeX128s);
}

interface IFallbackStock {
    function paused() external view returns (bool);
    function oraclePaused() external view returns (bool);
    function uiMultiplier() external view returns (uint256);
}

/// @dev Operator-maintained schedule of scheduled market closures (weekends, exchange holidays). Trust cost: a wrong entry widens the window.
interface IClosureCalendar {
    function isClosed(uint256 timestamp) external view returns (bool);
}

/// @notice PHASE 0 PROTOTYPE, TEST-ONLY. NOT a production contract, NOT deployed, NOT wired into any guard, vault or market. It exists to test
/// the design that `evidence/oracle/fallback-study.json` supports and to make its failure modes executable.
///
/// Design (one stock, USDG valued separately at $1 by the caller, exactly as `RamsesOracleGuard` does):
///  * The stock's OWN Chainlink feed is the primary and, while it is fresh, the ONLY source. Its answer is already `equity price x uiMultiplier`
///    (Chainlink's tokenized-equity methodology), so the multiplier is never applied here. It is only READ, to detect a change since the anchor.
///  * The ANCHOR is a feed answer recorded while that answer was fresh: price, the feed's original `updatedAt`, the multiplier then in force, and the
///    pool's time-weighted mean tick over `twapWindow` ending AT `updatedAt`. It is written ONLY from a fresh feed answer newer than the stored one.
///    A fallback value never writes anything (the view cannot), so the fallback can never re-anchor itself.
///  * Fallback value = anchor price x (pool TWAP now / pool TWAP at the anchor): the pool contributes only its RELATIVE move since the anchor, which
///    cancels the pool's static basis to the feed. It is allowed only while ALL hold: not paused by the issuer; the multiplier is unchanged; the
///    anchor is the feed's latest answer; the age is within the closure or open-session duration; the relative move is within `maxMoveBps`.
///  * Operation classes: EXIT and SETTLE may use the fallback; LIQUIDATE only if enabled (it is not, in every configuration studied) and then
///    floored at the anchor; ALLOCATE and BORROW (new borrowing or leverage) NEVER use it. EXPIRY OF THE DURATION LIMIT DOES NOT RESTORE ANY
///    OPERATION: it makes the fallback UNAVAILABLE (FallbackExpired) and the existing StaleOracle behaviour applies.
///  * What the move limit bounds: the change of the POOL's TWAP since the anchor. It does NOT bound the error against the current market price (a
///    stationary or manipulated pool can sit still across an arbitrarily large real gap) and it does NOT bound what repeated exits can extract.
contract AnchoredFallbackOracle {
    enum Op { EXIT, SETTLE, LIQUIDATE, ALLOCATE, BORROW }
    enum Source { FEED, FALLBACK }
    /// @dev ANCHORED: anchor price x the pool's relative move (the pool also gates). HOLD: the anchor price held flat; the pool is ONLY a move detector
    /// that refuses (MoveExceedsBound) and never adjusts the price. Both are candidates; neither has been shown safer than the other.
    enum Mode { ANCHORED, HOLD }

    struct Config {
        IFallbackFeed feed;
        IFallbackStock stock;
        IFallbackPool pool;
        IClosureCalendar calendar;
        bool stockIsToken0;
        uint8 stockDecimals;
        uint8 usdgDecimals;
        uint32 twapWindow; // seconds
        uint32 maxStaleness; // the existing guard's bound (<= 12 h); the fallback starts exactly where the feed would revert
        uint32 maxPokeDelay; // an answer older than this is not anchored: the multiplier can only be read NOW, so a late poke could pair a pre-step answer with a post-step multiplier
        uint32 maxClosureDuration; // anchor age allowed while a scheduled closure is in force
        uint32 maxOpenDuration; // anchor age allowed otherwise (quiet market or feed outage in a trading session)
        uint16 maxMoveBps; // |relative pool move since the anchor|
        uint16 maxBasisBps; // |pool price at the anchor / feed price - 1| required to ACCEPT an anchor
        bool liquidationInFallback;
        Mode mode;
    }

    struct Anchor {
        uint256 price18;
        uint64 updatedAt;
        uint256 multiplier;
        int24 avgTick;
    }

    Config public cfg;
    Anchor public anchor;
    address public immutable admin;
    address public immutable guardian;
    bool public fallbackDisabled;

    error NotAuthorized();
    error IssuerPaused();
    error InvalidAnswer();
    error StaleForAnchor(uint256 updatedAt);
    error PokeTooLate(uint256 delay, uint256 maxDelay);
    error AnchorCurrent();
    error NoPoolHistory();
    error BasisTooWide(uint256 poolPrice18, uint256 feedPrice18);
    error FallbackDisabled();
    error FallbackNotAllowedFor(Op op);
    error AnchorMissing(uint256 feedUpdatedAt, uint256 anchorUpdatedAt);
    error MultiplierChanged(uint256 anchored, uint256 current);
    error FallbackExpired(uint256 age, uint256 maxAge);
    error MoveExceedsBound(uint256 moveBps, uint256 maxBps);
    event Anchored(uint256 price18, uint64 updatedAt, int24 avgTick, uint256 multiplier);

    constructor(address admin_, address guardian_, Config memory c) {
        require(c.twapWindow >= 60 && c.maxStaleness > 0 && c.maxStaleness <= 12 hours && c.maxPokeDelay > 0 && c.maxPokeDelay <= c.maxStaleness, "CFG");
        require(c.maxMoveBps > 0 && c.maxMoveBps <= 500 && c.maxBasisBps > 0, "CFG");
        require(c.maxClosureDuration >= c.maxOpenDuration && c.maxOpenDuration >= c.maxStaleness, "CFG");
        admin = admin_;
        guardian = guardian_;
        cfg = c;
    }

    // ---------------------------------------------------------------- administration (timelock / one-way guardian)
    function disableFallback() external {
        if (msg.sender != guardian && msg.sender != admin) revert NotAuthorized();
        fallbackDisabled = true;
    }

    function enableFallback() external {
        if (msg.sender != admin) revert NotAuthorized();
        fallbackDisabled = false;
    }

    // ---------------------------------------------------------------- anchoring: permissionless, fresh feed answers only
    function poke() external {
        Config memory c = cfg;
        _requireNotPaused(c);
        (uint256 p, uint256 updatedAt) = _feed(c);
        if (block.timestamp - updatedAt > c.maxStaleness) revert StaleForAnchor(updatedAt);
        if (updatedAt <= anchor.updatedAt) revert AnchorCurrent();
        if (block.timestamp - updatedAt > c.maxPokeDelay) revert PokeTooLate(block.timestamp - updatedAt, c.maxPokeDelay);
        // the TWAP window must END at the answer's own timestamp, not at now
        int24 tickAt = _avgTick(c, uint32(block.timestamp - updatedAt));
        uint256 poolPrice = _usd18(c, tickAt);
        uint256 basis = poolPrice > p ? ((poolPrice - p) * 10_000) / p : ((p - poolPrice) * 10_000) / p;
        if (basis > c.maxBasisBps) revert BasisTooWide(poolPrice, p);
        anchor = Anchor(p, uint64(updatedAt), c.stock.uiMultiplier(), tickAt);
        emit Anchored(p, uint64(updatedAt), tickAt, anchor.multiplier);
    }

    // ---------------------------------------------------------------- pricing
    function price(Op op) external view returns (uint256 price18, Source source) {
        Config memory c = cfg;
        _requireNotPaused(c);
        (uint256 p, uint256 updatedAt) = _feed(c);
        uint256 age = block.timestamp - updatedAt;
        if (age <= c.maxStaleness) return (p, Source.FEED);

        if (fallbackDisabled) revert FallbackDisabled();
        if (op == Op.ALLOCATE || op == Op.BORROW || (op == Op.LIQUIDATE && !c.liquidationInFallback)) {
            revert FallbackNotAllowedFor(op);
        }
        Anchor memory a = anchor;
        if (a.updatedAt != updatedAt) revert AnchorMissing(updatedAt, a.updatedAt);
        uint256 m = c.stock.uiMultiplier();
        if (m != a.multiplier) revert MultiplierChanged(a.multiplier, m);
        uint256 maxAge = c.calendar.isClosed(block.timestamp) ? c.maxClosureDuration : c.maxOpenDuration;
        if (age > maxAge) revert FallbackExpired(age, maxAge);

        int24 tickNow = _avgTick(c, 0);
        uint256 ratio18 = _ratio18(c, tickNow, a.avgTick);
        uint256 moveBps = ratio18 > 1e18 ? ((ratio18 - 1e18) * 10_000) / 1e18 : ((1e18 - ratio18) * 10_000) / 1e18;
        if (moveBps > c.maxMoveBps) revert MoveExceedsBound(moveBps, c.maxMoveBps);

        price18 = c.mode == Mode.HOLD ? a.price18 : FullMath.mulDiv(a.price18, ratio18, 1e18);
        // Liquidation is DISABLED by default. If enabled, this floors the estimate at the anchor. That is a bias for a borrower whose COLLATERAL is the
        // stock; it is the WRONG direction for a borrower whose DEBT is the stock, and it does not remove false liquidations in general (a true price
        // above the anchor with a lagging pool still prices the collateral below the truth). See FallbackEconomics.t.sol.
        if (op == Op.LIQUIDATE && price18 < a.price18) price18 = a.price18;
        return (price18, Source.FALLBACK);
    }

    // ---------------------------------------------------------------- internals
    function _requireNotPaused(Config memory c) internal view {
        if (c.stock.paused() || c.stock.oraclePaused()) revert IssuerPaused();
    }

    function _feed(Config memory c) internal view returns (uint256 p, uint256 updatedAt) {
        (uint80 round, int256 answer,, uint256 u, uint80 answered) = c.feed.latestRoundData();
        if (answer <= 0 || u == 0 || u > block.timestamp || answered < round) revert InvalidAnswer();
        uint8 d = c.feed.decimals();
        p = d <= 18 ? uint256(answer) * 10 ** (18 - d) : uint256(answer) / 10 ** (d - 18);
        if (p == 0) revert InvalidAnswer();
        updatedAt = u;
    }

    /// @dev Mean tick over [now - ago - W, now - ago], rounded to negative infinity like the pool library.
    function _avgTick(Config memory c, uint32 ago) internal view returns (int24) {
        uint32[] memory s = new uint32[](2);
        s[0] = ago + c.twapWindow;
        s[1] = ago;
        try c.pool.observe(s) returns (int56[] memory cum, uint160[] memory) {
            int56 d = cum[1] - cum[0];
            int56 w = int56(uint56(c.twapWindow));
            int56 t = d / w;
            if (d < 0 && d % w != 0) t--;
            return int24(t);
        } catch {
            revert NoPoolHistory();
        }
    }

    /// @dev USD-per-stock (1e18) implied by a tick, orientation and decimals aware.
    function _usd18(Config memory c, int24 tick) internal pure returns (uint256) {
        uint160 sq = TickMath.getSqrtPriceAtTick(tick);
        uint256 raw = FullMath.mulDiv(uint256(sq), uint256(sq), 1 << 64); // token1/token0 raw price x 2^128
        if (c.stockIsToken0) {
            // USDG per stock = raw x 10^sd / 10^ud
            return FullMath.mulDiv(FullMath.mulDiv(raw, 1e18, 1 << 128), 10 ** c.stockDecimals, 10 ** c.usdgDecimals);
        }
        // stock is token1: USDG per stock = 10^sd / (raw x 10^ud)
        return FullMath.mulDiv(FullMath.mulDiv(1 << 128, 1e18, raw), 10 ** c.stockDecimals, 10 ** c.usdgDecimals);
    }

    /// @dev Stock price at `tickNow` over its price at `tickAnchor`, 1e18.
    function _ratio18(Config memory c, int24 tickNow, int24 tickAnchor) internal pure returns (uint256) {
        uint256 sn = TickMath.getSqrtPriceAtTick(tickNow);
        uint256 sa = TickMath.getSqrtPriceAtTick(tickAnchor);
        uint256 r = c.stockIsToken0 ? FullMath.mulDiv(sn, 1e18, sa) : FullMath.mulDiv(sa, 1e18, sn);
        return FullMath.mulDiv(r, r, 1e18);
    }
}
