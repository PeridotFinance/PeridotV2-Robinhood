// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";

import { IStockOracleGuard } from "baseline/src/interfaces/IStockOracleGuard.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";

import { IUniswapV4PairedAdapterV3 } from "../interfaces/IUniswapV4PairedAdapterV3.sol";
import { RangePolicy, RangeState } from "./RangeTypes.sol";

/// @notice Range management for the paired vault, reached by DELEGATECALL so storage and
/// `address(this)` are the vault's. It exists to keep the vault under EIP-170, like SettlementLib.
library RangeLib {
    using SafeERC20 for IERC20;

    uint256 internal constant BPS = 10_000;
    uint16 internal constant MIN_HALF_WIDTH_TICKS = 600; // about +/-6%
    uint16 internal constant MAX_HALF_WIDTH_TICKS = 4_800; // about +/-61%
    uint32 internal constant MIN_RECENTER_INTERVAL = 1 hours;
    uint32 internal constant MAX_RECENTER_INTERVAL = 7 days;
    uint16 internal constant MAX_RECENTER_LOSS_BPS = 100;

    error InvalidRangePolicy();
    error RecenterNotNeeded();
    error RecenterCooldown();
    error RecenterRateLimited();
    error RecenterLossTooHigh();
    error InsufficientLiquidity();
    error BalanceDeltaMismatch();

    struct Ctx {
        bytes32 pairId;
        uint256 stockPrice;
        uint256 usdgPrice;
        uint160 ref; // oracle-derived reference sqrt price, pool order
        uint256 deadline;
        IUniswapV4PairedAdapter adapter;
        IStockOracleGuard guard;
    }

    struct Result {
        int24 oldLower;
        int24 oldUpper;
        int24 newLower;
        int24 newUpper;
        int24 centerTick;
        uint128 liquidity;
    }

    function validatePolicy(RangePolicy calldata policy, int24 spacing) external pure {
        if (!policy.enabled) return;
        if (
            policy.halfWidthTicks < MIN_HALF_WIDTH_TICKS
                || policy.halfWidthTicks > MAX_HALF_WIDTH_TICKS
                || int24(uint24(policy.halfWidthTicks)) % spacing != 0
                || policy.triggerTicks < uint16(uint24(spacing))
                || policy.triggerTicks > policy.halfWidthTicks / 2
                || policy.minInterval < MIN_RECENTER_INTERVAL
                || policy.minInterval > MAX_RECENTER_INTERVAL || policy.maxPerDay == 0
                || policy.maxPerDay > 24 || policy.maxLossBps > MAX_RECENTER_LOSS_BPS
                || policy.maxRangedValueUsd == 0
        ) revert InvalidRangePolicy();
    }

    /// @notice Adds liquidity from idle balances, at most `capPerSide` of oracle value per side.
    function deploy(
        PairConfig storage config,
        PairLedger storage pairLedger,
        Ctx memory c,
        uint256 capPerSide
    ) public returns (uint128 liquidityAdded) {
        uint256 stockValue = VaultMath.valueUSD18(
            pairLedger.stockIdle, config.stockDecimals, c.stockPrice, Math.Rounding.Floor
        );
        uint256 usdgValue = VaultMath.valueUSD18(
            pairLedger.usdgIdle, config.usdgDecimals, c.usdgPrice, Math.Rounding.Floor
        );
        uint256 matched = Math.min(Math.min(stockValue, usdgValue), capPerSide);
        if (matched == 0) revert InsufficientLiquidity();
        uint256 stockToPair = VaultMath.amountFromValueUSD18(
            matched, config.stockDecimals, c.stockPrice, Math.Rounding.Floor
        );
        uint256 usdgToPair = VaultMath.amountFromValueUSD18(
            matched, config.usdgDecimals, c.usdgPrice, Math.Rounding.Floor
        );

        IERC20 stock = IERC20(config.stockToken);
        IERC20 usdg = IERC20(config.usdg);
        uint256 stockBefore = stock.balanceOf(address(this));
        uint256 usdgBefore = usdg.balanceOf(address(this));
        stock.forceApprove(address(c.adapter), stockToPair);
        usdg.forceApprove(address(c.adapter), usdgToPair);
        (uint256 stockUsed, uint256 usdgUsed, uint128 added) =
            c.adapter.addLiquidity(c.pairId, stockToPair, usdgToPair, c.deadline);
        stock.forceApprove(address(c.adapter), 0);
        usdg.forceApprove(address(c.adapter), 0);
        if (
            stock.balanceOf(address(this)) + stockUsed != stockBefore
                || usdg.balanceOf(address(this)) + usdgUsed != usdgBefore
        ) revert BalanceDeltaMismatch();
        if (stockUsed > pairLedger.stockIdle || usdgUsed > pairLedger.usdgIdle) {
            revert InsufficientLiquidity();
        }
        pairLedger.stockIdle -= stockUsed;
        pairLedger.usdgIdle -= usdgUsed;
        liquidityAdded = added;
    }

    /// @notice Room left under the ranged-position value cap, per side.
    function remainingCapPerSide(
        PairConfig storage config,
        RangePolicy storage policy,
        Ctx memory c
    ) external view returns (uint256) {
        if (!policy.enabled) return type(uint256).max;
        IUniswapV4PairedAdapter.PositionState memory position =
            c.adapter.positionStateAt(c.pairId, c.ref);
        uint256 lpValue = VaultMath.valueUSD18(
            position.stockAmount, config.stockDecimals, c.stockPrice, Math.Rounding.Floor
        ) + VaultMath.valueUSD18(
            position.usdgAmount, config.usdgDecimals, c.usdgPrice, Math.Rounding.Floor
        );
        uint256 total = policy.maxRangedValueUsd;
        return lpValue >= total ? 0 : (total - lpValue) / 2;
    }

    /// @notice Everything after the vault has checkpointed at `c.ref`: eligibility, remove,
    /// burn, set the new range, redeploy, and verify the outcome.
    function recenter(
        PairConfig storage config,
        PairLedger storage pairLedger,
        RangePolicy storage policy,
        RangeState storage state,
        Ctx memory c
    ) external returns (Result memory r) {
        IUniswapV4PairedAdapterV3 adapter = IUniswapV4PairedAdapterV3(address(c.adapter));
        bool ranged;
        (r.oldLower, r.oldUpper, ranged) = adapter.positionTicks(c.pairId);
        r.centerTick = TickMath.getTickAtSqrtPrice(c.ref);
        _requireDue(policy, state, ranged, r);
        _consumeBudget(state, policy.maxPerDay);

        (uint256 stockBefore, uint256 usdgBefore) = _assets(pairLedger, c);
        uint256 valueBefore = _value(config, stockBefore, usdgBefore, c);

        _removeAndBurn(config, pairLedger, c);

        PoolKey memory key = c.adapter.poolKey(c.pairId);
        (r.newLower, r.newUpper) = newRange(r.centerTick, policy.halfWidthTicks, key.tickSpacing);
        adapter.setRange(c.pairId, r.newLower, r.newUpper);
        r.liquidity = deploy(config, pairLedger, c, uint256(policy.maxRangedValueUsd) / 2);

        (uint256 stockAfter, uint256 usdgAfter) = _assets(pairLedger, c);
        uint256 valueAfter = _value(config, stockAfter, usdgAfter, c);
        if (valueAfter + Math.mulDiv(valueBefore, policy.maxLossBps, BPS) < valueBefore) {
            revert RecenterLossTooHigh();
        }
        c.guard.validatePoolPrice(c.pairId, key);

        state.centerTick = r.centerTick;
        state.initialized = true;
        state.lastRecenter = uint64(block.timestamp);
    }

    /// @notice Aligned range of exactly `2 * half + spacing` ticks that contains `centerTick`.
    function newRange(int24 centerTick, uint16 halfWidthTicks, int24 spacing)
        public
        pure
        returns (int24 lower, int24 upper)
    {
        int24 half = int24(uint24(halfWidthTicks));
        int24 floorCenter = centerTick / spacing * spacing;
        if (centerTick < 0 && centerTick % spacing != 0) floorCenter -= spacing;
        lower = floorCenter - half;
        upper = floorCenter + half + spacing;
    }

    function _requireDue(
        RangePolicy storage policy,
        RangeState storage state,
        bool ranged,
        Result memory r
    ) private view {
        if (!ranged || !state.initialized) return; // first conversion to a ranged position
        if (block.timestamp < uint256(state.lastRecenter) + policy.minInterval) {
            revert RecenterCooldown();
        }
        int256 moved = int256(r.centerTick) - int256(state.centerTick);
        if (moved < 0) moved = -moved;
        bool outOfRange = r.centerTick <= r.oldLower || r.centerTick >= r.oldUpper;
        if (moved < int256(uint256(policy.triggerTicks)) && !outOfRange) revert RecenterNotNeeded();
    }

    function _consumeBudget(RangeState storage state, uint8 maxPerDay) private {
        if (block.timestamp >= uint256(state.windowStart) + 1 days) {
            state.windowStart = uint64(block.timestamp);
            state.windowCount = 0;
        }
        if (state.windowCount >= maxPerDay) revert RecenterRateLimited();
        state.windowCount += 1;
    }

    function _removeAndBurn(PairConfig storage config, PairLedger storage pairLedger, Ctx memory c)
        private
    {
        IUniswapV4PairedAdapter.PositionState memory position = c.adapter.positionState(c.pairId);
        IERC20 stock = IERC20(config.stockToken);
        IERC20 usdg = IERC20(config.usdg);
        if (position.liquidity != 0) {
            uint256 stockBefore = stock.balanceOf(address(this));
            uint256 usdgBefore = usdg.balanceOf(address(this));
            (uint256 stockReceived, uint256 usdgReceived,) =
                c.adapter.decreaseLiquidity(c.pairId, position.liquidity, c.ref, c.deadline);
            if (
                stock.balanceOf(address(this)) != stockBefore + stockReceived
                    || usdg.balanceOf(address(this)) != usdgBefore + usdgReceived
            ) revert BalanceDeltaMismatch();
            pairLedger.stockIdle += stockReceived;
            pairLedger.usdgIdle += usdgReceived;
        }
        if (position.tokenId != 0) {
            uint256 stockBefore = stock.balanceOf(address(this));
            uint256 usdgBefore = usdg.balanceOf(address(this));
            (uint256 stockReceived, uint256 usdgReceived) =
                c.adapter.burnEmptyPosition(c.pairId, c.deadline);
            if (
                stock.balanceOf(address(this)) != stockBefore + stockReceived
                    || usdg.balanceOf(address(this)) != usdgBefore + usdgReceived
            ) revert BalanceDeltaMismatch();
            pairLedger.stockIdle += stockReceived;
            pairLedger.usdgIdle += usdgReceived;
        }
    }

    function _assets(PairLedger storage pairLedger, Ctx memory c)
        private
        view
        returns (uint256 stockAssets, uint256 usdgAssets)
    {
        IUniswapV4PairedAdapter.PositionState memory position =
            c.adapter.positionStateAt(c.pairId, c.ref);
        stockAssets = pairLedger.stockIdle + position.stockAmount;
        usdgAssets = pairLedger.usdgIdle + position.usdgAmount;
    }

    function _value(PairConfig storage config, uint256 stockAmount, uint256 usdgAmount, Ctx memory c)
        private
        view
        returns (uint256)
    {
        return VaultMath.valueUSD18(stockAmount, config.stockDecimals, c.stockPrice, Math.Rounding.Floor)
            + VaultMath.valueUSD18(usdgAmount, config.usdgDecimals, c.usdgPrice, Math.Rounding.Floor);
    }
}
