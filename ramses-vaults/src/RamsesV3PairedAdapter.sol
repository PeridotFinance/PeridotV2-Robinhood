// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { LiquidityAmounts } from "@uniswap/v4-periphery/src/libraries/LiquidityAmounts.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import {
    IRamsesPool,
    IRamsesNfp,
    IRamsesGauge,
    IRamsesFactory,
    IRamsesVoter,
    IRamsesDeployer
} from "./interfaces/IRamsesV3.sol";

/// @notice Phase 2 must supply a feed-backed, freshness-checked guard. Never substitute pool spot.
interface IRamsesReferenceOracle {
    function referenceSqrtPriceX96(bytes32 pairId, bool allocating) external view returns (uint160);
}

/// @notice Isolated, non-upgradeable Ramses CL custody adapter. The configured vault owns accounting.
/// @dev Rewards are accrued in a separate per-pair/token bucket, never mixed with LP principal/fees.
/// Call harvest to drain that bucket, including rewards automatically claimed before removal/burn.
contract RamsesV3PairedAdapter {
    uint256 private _entered;
    error ReentrantCall();
    modifier nonReentrant() {
        if (_entered != 0) revert ReentrantCall();
        _entered = 1;
        _;
        _entered = 0;
    }
    using SafeERC20 for IERC20;
    uint256 private constant BPS = 10_000;
    uint256 private constant BUFFER = 100;
    uint256 private constant QUANTISATION = 20;
    uint256 private constant MAX_REWARDS = 8;

    struct RegisterPairParams {
        address stockToken;
        address usdg;
        IRamsesPool pool;
        IRamsesGauge gauge;
        uint16 removalToleranceBps;
        uint16 allocationDeviationBps;
    }

    struct PositionState {
        uint256 tokenId;
        uint128 liquidity;
        uint256 stockAmount;
        uint256 usdgAmount;
    }

    struct PairState {
        address stockToken;
        address usdg;
        IRamsesPool pool;
        IRamsesGauge gauge;
        bool stockIs0;
        int24 spacing;
        int24 lower;
        int24 upper;
        bool ranged;
        uint16 tolerance;
        uint16 allocationDeviation;
        uint256 tokenId;
        uint128 liquidity;
        address[] rewards;
    }

    struct AddState {
        uint256 start0;
        uint256 start1;
        uint256 desired0;
        uint256 desired1;
        uint256 min0;
        uint256 min1;
        uint256 used0;
        uint256 used1;
        uint128 expected;
    }
    address public immutable vault;
    IRamsesNfp public immutable positionManager;
    IRamsesReferenceOracle public immutable oracle;
    mapping(bytes32 => PairState) private _pairs;
    mapping(bytes32 => mapping(address => uint256)) public pendingRewards;
    mapping(address => bool) private _registeredPools;

    error NotVault();
    error InvalidConfiguration();
    error UnknownPair();
    error InvalidReferencePrice();
    error InvalidDeadline();
    error InvalidRange();
    error RangeLocked();
    error InvalidPosition();
    error InsufficientLiquidity();
    error BalanceDeltaMismatch();
    error SlippageExceeded();
    error RewardClaimMismatch();
    error RewardSetChanged();

    event PairRegistered(bytes32 indexed pairId, address indexed pool, address gauge);
    event RangeSet(bytes32 indexed pairId, int24 lower, int24 upper);
    event LiquidityAdded(
        bytes32 indexed pairId, uint256 tokenId, uint128 liquidity, uint256 stock, uint256 usdg
    );
    event LiquidityDecreased(
        bytes32 indexed pairId, uint128 liquidity, uint256 stock, uint256 usdg
    );
    event FeesCollected(bytes32 indexed pairId, uint256 stock, uint256 usdg);
    event RewardAccrued(bytes32 indexed pairId, address indexed token, uint256 amount);
    event RewardPaid(bytes32 indexed pairId, address indexed token, uint256 amount);
    event PositionBurned(bytes32 indexed pairId, uint256 tokenId);

    modifier onlyVault() {
        if (msg.sender != vault) revert NotVault();
        _;
    }

    constructor(address vault_, IRamsesNfp manager_, IRamsesReferenceOracle oracle_) {
        if (
            vault_ == address(0) || address(manager_).code.length == 0
                || address(oracle_).code.length == 0
        ) {
            revert InvalidConfiguration();
        }
        vault = vault_;
        positionManager = manager_;
        oracle = oracle_;
    }

    function registerPair(bytes32 id, RegisterPairParams calldata p)
        external
        onlyVault
        nonReentrant
    {
        if (
            id == 0 || address(_pairs[id].pool) != address(0) || _registeredPools[address(p.pool)]
                || p.stockToken == p.usdg || p.stockToken.code.length == 0
                || p.usdg.code.length == 0 || p.removalToleranceBps < BUFFER
                || p.removalToleranceBps > 2000 || p.allocationDeviationBps > 500
        ) revert InvalidConfiguration();
        address t0 = p.pool.token0();
        address t1 = p.pool.token1();
        if (
            t0 >= t1
                || !((t0 == p.stockToken && t1 == p.usdg) || (t1 == p.stockToken && t0 == p.usdg))
                || p.pool.factory() != IRamsesDeployer(positionManager.deployer()).RamsesV3Factory()
                || IRamsesFactory(p.pool.factory()).getPool(t0, t1, p.pool.tickSpacing())
                    != address(p.pool) || p.gauge.pool() != address(p.pool)
                || p.gauge.nfpManager() != address(positionManager)
                || IRamsesVoter(p.gauge.voter()).gaugeForPool(address(p.pool)) != address(p.gauge)
        ) {
            revert InvalidConfiguration();
        }
        int24 spacing = p.pool.tickSpacing();
        if (spacing <= 0 || spacing > 32767) revert InvalidConfiguration();
        (uint160 spot,,,,,,) = p.pool.slot0();
        _validPrice(spot);
        PairState storage pair = _pairs[id];
        pair.stockToken = p.stockToken;
        pair.usdg = p.usdg;
        pair.pool = p.pool;
        pair.gauge = p.gauge;
        pair.stockIs0 = t0 == p.stockToken;
        pair.spacing = spacing;
        pair.lower = TickMath.minUsableTick(spacing);
        pair.upper = TickMath.maxUsableTick(spacing);
        pair.tolerance = p.removalToleranceBps;
        pair.allocationDeviation = p.allocationDeviationBps;
        _registeredPools[address(p.pool)] = true;
        _syncRewards(pair);
        emit PairRegistered(id, address(p.pool), address(p.gauge));
    }

    function positionState(bytes32 id) external view returns (PositionState memory) {
        PairState storage p = _pair(id);
        (uint160 spot,,,,,,) = p.pool.slot0();
        _validPrice(spot);
        return _state(p, spot);
    }

    function positionStateAt(bytes32 id, uint160 ref) external view returns (PositionState memory) {
        _validPrice(ref);
        return _state(_pair(id), ref);
    }

    function _state(PairState storage p, uint160 ref)
        private
        view
        returns (PositionState memory s)
    {
        s.tokenId = p.tokenId;
        s.liquidity = p.liquidity;
        (uint256 a0, uint256 a1) = _amounts(p, ref, p.liquidity);
        (s.stockAmount, s.usdgAmount) = _fromOrder(p, a0, a1);
    }

    function positionTicks(bytes32 id) external view returns (int24, int24, bool) {
        PairState storage p = _pair(id);
        return (p.lower, p.upper, p.ranged);
    }

    function removalTolerance(bytes32 id) external view returns (uint16) {
        return _pair(id).tolerance;
    }

    function rewardTokens(bytes32 id) external view returns (address[] memory) {
        return _pair(id).rewards;
    }

    function setRange(bytes32 id, int24 lower, int24 upper) external onlyVault nonReentrant {
        PairState storage p = _pair(id);
        if (p.tokenId != 0) revert RangeLocked();
        if (
            lower >= upper || lower < TickMath.MIN_TICK || upper > TickMath.MAX_TICK
                || lower % p.spacing != 0 || upper % p.spacing != 0
        ) revert InvalidRange();
        p.lower = lower;
        p.upper = upper;
        p.ranged = true;
        emit RangeSet(id, lower, upper);
    }

    function clearRange(bytes32 id) external onlyVault nonReentrant {
        PairState storage p = _pair(id);
        if (p.tokenId != 0) revert RangeLocked();
        p.lower = TickMath.minUsableTick(p.spacing);
        p.upper = TickMath.maxUsableTick(p.spacing);
        p.ranged = false;
        emit RangeSet(id, p.lower, p.upper);
    }

    function addLiquidity(bytes32 id, uint256 stockDesired, uint256 usdgDesired, uint256 deadline)
        external
        onlyVault
        nonReentrant
        returns (uint256 stockUsed, uint256 usdgUsed, uint128 added)
    {
        _deadline(deadline);
        PairState storage p = _pair(id);
        if (stockDesired == 0 && usdgDesired == 0) revert InsufficientLiquidity();
        uint160 ref = oracle.referenceSqrtPriceX96(id, true);
        _validPrice(ref);
        _checkBand(p, ref, p.allocationDeviation + QUANTISATION);
        AddState memory a;
        (a.desired0, a.desired1) = _toOrder(p, stockDesired, usdgDesired);
        a.expected = LiquidityAmounts.getLiquidityForAmounts(
            ref,
            TickMath.getSqrtPriceAtTick(p.lower),
            TickMath.getSqrtPriceAtTick(p.upper),
            a.desired0,
            a.desired1
        );
        if (a.expected == 0) revert InsufficientLiquidity();
        (a.min0, a.min1) = _minimums(p, a.expected, ref, p.allocationDeviation + QUANTISATION);
        (address t0, address t1) = _tokens(p);
        a.start0 = IERC20(t0).balanceOf(address(this));
        a.start1 = IERC20(t1).balanceOf(address(this));
        _pull(t0, a.desired0);
        _pull(t1, a.desired1);
        IERC20(t0).forceApprove(address(positionManager), a.desired0);
        IERC20(t1).forceApprove(address(positionManager), a.desired1);
        if (p.tokenId == 0) {
            (p.tokenId, added, a.used0, a.used1) = positionManager.mint(
                IRamsesNfp.MintParams(
                    t0,
                    t1,
                    p.spacing,
                    p.lower,
                    p.upper,
                    a.desired0,
                    a.desired1,
                    a.min0,
                    a.min1,
                    address(this),
                    deadline
                )
            );
            if (p.tokenId == 0) revert InvalidPosition();
        } else {
            _validatePosition(p);
            (added, a.used0, a.used1) = positionManager.increaseLiquidity(
                IRamsesNfp.IncreaseLiquidityParams(
                    p.tokenId, a.desired0, a.desired1, a.min0, a.min1, deadline
                )
            );
        }
        _revoke(t0);
        _revoke(t1);
        if (added == 0 || added < Math.mulDiv(a.expected, BPS - p.tolerance, BPS)) {
            revert SlippageExceeded();
        }
        p.liquidity += added;
        _validatePosition(p);
        _checkSpent(t0, a.start0, a.desired0, a.used0);
        _checkSpent(t1, a.start1, a.desired1, a.used1);
        if (a.used0 < a.min0 || a.used1 < a.min1) revert SlippageExceeded();
        _push(t0, a.desired0 - a.used0);
        _push(t1, a.desired1 - a.used1);
        (stockUsed, usdgUsed) = _fromOrder(p, a.used0, a.used1);
        emit LiquidityAdded(id, p.tokenId, added, stockUsed, usdgUsed);
    }

    function decreaseLiquidity(bytes32 id, uint128 liquidity, uint160 ref, uint256 deadline)
        external
        onlyVault
        nonReentrant
        returns (uint256 stock, uint256 usdg, uint128 removed)
    {
        _deadline(deadline);
        PairState storage p = _pair(id);
        if (liquidity == 0 || liquidity > p.liquidity) revert InsufficientLiquidity();
        _validPrice(ref);
        if (oracle.referenceSqrtPriceX96(id, false) != ref) revert InvalidReferencePrice();
        uint256 d = 2 * (p.tolerance - BUFFER) + QUANTISATION;
        _checkBand(p, ref, d);
        _validatePosition(p);
        (uint256 min0, uint256 min1) = _minimums(p, liquidity, ref, d);
        _harvest(id, p); // Must precede every non-zero decrease and NFT burn.
        (uint256 principal0, uint256 principal1) = positionManager.decreaseLiquidity(
            IRamsesNfp.DecreaseLiquidityParams(p.tokenId, liquidity, min0, min1, deadline)
        );
        if (principal0 < min0 || principal1 < min1) revert SlippageExceeded();
        p.liquidity -= liquidity;
        _validatePosition(p);
        (uint256 got0, uint256 got1) = _collect(p);
        // Core can round down one raw unit per token; fees must not disguise a principal shortfall.
        if (got0 + 1 < principal0 || got1 + 1 < principal1) revert BalanceDeltaMismatch();
        (stock, usdg) = _fromOrder(p, got0, got1);
        removed = liquidity;
        emit LiquidityDecreased(id, liquidity, stock, usdg);
    }

    function collectFees(bytes32 id, uint256 deadline)
        external
        onlyVault
        nonReentrant
        returns (uint256 stock, uint256 usdg)
    {
        _deadline(deadline);
        PairState storage p = _pair(id);
        if (p.tokenId == 0) return (0, 0);
        _validatePosition(p);
        (uint256 a0, uint256 a1) = _collect(p);
        (stock, usdg) = _fromOrder(p, a0, a1);
        emit FeesCollected(id, stock, usdg);
    }

    function burnEmptyPosition(bytes32 id, uint256 deadline)
        external
        onlyVault
        nonReentrant
        returns (uint256 stock, uint256 usdg)
    {
        _deadline(deadline);
        PairState storage p = _pair(id);
        if (p.tokenId == 0 || p.liquidity != 0) revert InvalidPosition();
        _validatePosition(p);
        _harvest(id, p);
        (uint256 a0, uint256 a1) = _collect(p);
        uint256 tokenId = p.tokenId;
        positionManager.burn(tokenId);
        p.tokenId = 0;
        (stock, usdg) = _fromOrder(p, a0, a1);
        emit PositionBurned(id, tokenId);
    }

    /// @notice Claim and transfer *only* segregated rewards. RAM is not valued or swapped here.
    /// @dev Automatic claims during removal/burn remain claimable even after the NFT is gone.
    function harvest(bytes32 id, uint256 deadline)
        external
        onlyVault
        nonReentrant
        returns (address[] memory tokens, uint256[] memory amounts)
    {
        _deadline(deadline);
        PairState storage p = _pair(id);
        if (p.tokenId != 0) {
            _validatePosition(p);
            _harvest(id, p);
        }
        tokens = p.rewards;
        amounts = new uint256[](tokens.length);
        for (uint256 i; i < tokens.length; ++i) {
            amounts[i] = pendingRewards[id][tokens[i]];
            pendingRewards[id][tokens[i]] = 0;
            _push(tokens[i], amounts[i]);
            emit RewardPaid(id, tokens[i], amounts[i]);
        }
    }

    /// @dev Retain removed tokens: their historical periods can still be owed. No arbitrary token sweep.
    function _syncRewards(PairState storage p) private {
        address[] memory live = p.gauge.getRewardTokens();
        if (live.length > MAX_REWARDS) revert RewardSetChanged();
        for (uint256 i; i < live.length; ++i) {
            bool found;
            for (uint256 j; j < p.rewards.length; ++j) {
                if (p.rewards[j] == live[i]) found = true;
            }
            if (!found) {
                if (live[i].code.length == 0 || p.rewards.length == MAX_REWARDS) {
                    revert RewardSetChanged();
                }
                p.rewards.push(live[i]);
            }
        }
    }

    function _harvest(bytes32 id, PairState storage p) private {
        _syncRewards(p);
        address[] memory tokens = p.rewards;
        uint256[] memory beforeBalances = new uint256[](tokens.length);
        uint256[] memory gaugeBalances = new uint256[](tokens.length);
        uint256[] memory expected = new uint256[](tokens.length);
        // Advance first: the gauge also does this, and earned can otherwise hide an unadvanced period.
        p.pool._advancePeriod();
        for (uint256 i; i < tokens.length; ++i) {
            beforeBalances[i] = IERC20(tokens[i]).balanceOf(address(this));
            gaugeBalances[i] = IERC20(tokens[i]).balanceOf(address(p.gauge));
            expected[i] = p.gauge.earned(tokens[i], p.tokenId);
        }
        // Direct gauge.getReward is restricted to protocol-authorized claimers on this deployment.
        positionManager.getReward(p.tokenId, tokens);
        for (uint256 i; i < tokens.length; ++i) {
            uint256 afterBalance = IERC20(tokens[i]).balanceOf(address(this));
            uint256 gaugeAfter = IERC20(tokens[i]).balanceOf(address(p.gauge));
            if (afterBalance < beforeBalances[i] || gaugeAfter > gaugeBalances[i]) {
                revert BalanceDeltaMismatch();
            }
            uint256 received = afterBalance - beforeBalances[i];
            // Reject silent slashing/forfeiture and non-exact reward tokens atomically.
            if (received != expected[i] || gaugeBalances[i] - gaugeAfter != received) {
                revert RewardClaimMismatch();
            }
            pendingRewards[id][tokens[i]] += received;
            emit RewardAccrued(id, tokens[i], received);
        }
    }

    function _collect(PairState storage p) private returns (uint256 a0, uint256 a1) {
        (address t0, address t1) = _tokens(p);
        uint256 b0 = IERC20(t0).balanceOf(address(this));
        uint256 b1 = IERC20(t1).balanceOf(address(this));
        uint256 pool0 = IERC20(t0).balanceOf(address(p.pool));
        uint256 pool1 = IERC20(t1).balanceOf(address(p.pool));
        (a0, a1) = positionManager.collect(
            IRamsesNfp.CollectParams(p.tokenId, address(this), type(uint128).max, type(uint128).max)
        );
        if (
            IERC20(t0).balanceOf(address(this)) != b0 + a0
                || IERC20(t1).balanceOf(address(this)) != b1 + a1
                || IERC20(t0).balanceOf(address(p.pool)) + a0 != pool0
                || IERC20(t1).balanceOf(address(p.pool)) + a1 != pool1
        ) {
            revert BalanceDeltaMismatch();
        }
        _push(t0, a0);
        _push(t1, a1);
    }

    function _validatePosition(PairState storage p) private view {
        IRamsesNfp.Position memory n = positionManager.positions(p.tokenId);
        (address t0, address t1) = _tokens(p);
        if (
            positionManager.ownerOf(p.tokenId) != address(this) || n.token0 != t0 || n.token1 != t1
                || n.tickSpacing != p.spacing || n.tickLower != p.lower || n.tickUpper != p.upper
                || n.liquidity != p.liquidity
        ) {
            revert InvalidPosition();
        }
    }

    function _minimums(PairState storage p, uint128 l, uint160 ref, uint256 d)
        private
        view
        returns (uint256 a0, uint256 a1)
    {
        (uint160 low, uint160 high) = _band(p, ref, d);
        (a0,) = _amounts(p, high, l);
        (, a1) = _amounts(p, low, l);
        a0 = Math.mulDiv(a0, BPS - BUFFER, BPS);
        a1 = Math.mulDiv(a1, BPS - BUFFER, BPS);
    }

    function _checkBand(PairState storage p, uint160 ref, uint256 d) private view {
        (uint160 low, uint160 high) = _band(p, ref, d);
        (uint160 spot,,,,,,) = p.pool.slot0();
        if (spot < low || spot > high) revert SlippageExceeded();
    }

    function _band(PairState storage p, uint160 ref, uint256 d)
        private
        view
        returns (uint160 low, uint160 high)
    {
        // The bound is on stock/USDG, so invert both endpoints when USDG is token0.
        if (p.stockIs0) return (_scale(ref, BPS - d, BPS), _scale(ref, BPS + d, BPS));
        return (_scale(ref, BPS, BPS + d), _scale(ref, BPS, BPS - d));
    }

    function _scale(uint160 ref, uint256 n, uint256 d) private pure returns (uint160) {
        uint256 v = Math.mulDiv(ref, Math.sqrt(Math.mulDiv(n, 1e36, d)), 1e18);
        if (v < TickMath.MIN_SQRT_PRICE) return TickMath.MIN_SQRT_PRICE;
        if (v >= TickMath.MAX_SQRT_PRICE) return TickMath.MAX_SQRT_PRICE - 1;
        return uint160(v);
    }

    function _amounts(PairState storage p, uint160 ref, uint128 l)
        private
        view
        returns (uint256, uint256)
    {
        return VaultMath.amountsForLiquidity(
            ref, TickMath.getSqrtPriceAtTick(p.lower), TickMath.getSqrtPriceAtTick(p.upper), l
        );
    }

    function _checkSpent(address token, uint256 start, uint256 desired, uint256 used) private view {
        if (used > desired || IERC20(token).balanceOf(address(this)) != start + desired - used) {
            revert BalanceDeltaMismatch();
        }
    }

    function _pull(address token, uint256 amount) private {
        if (amount == 0) return;
        IERC20 t = IERC20(token);
        uint256 a = t.balanceOf(vault);
        uint256 b = t.balanceOf(address(this));
        t.safeTransferFrom(vault, address(this), amount);
        if (t.balanceOf(vault) + amount != a || t.balanceOf(address(this)) != b + amount) {
            revert BalanceDeltaMismatch();
        }
    }

    function _push(address token, uint256 amount) private {
        if (amount == 0) return;
        IERC20 t = IERC20(token);
        uint256 a = t.balanceOf(address(this));
        uint256 b = t.balanceOf(vault);
        t.safeTransfer(vault, amount);
        if (t.balanceOf(address(this)) + amount != a || t.balanceOf(vault) != b + amount) {
            revert BalanceDeltaMismatch();
        }
    }

    function _revoke(address token) private {
        IERC20(token).forceApprove(address(positionManager), 0);
        if (IERC20(token).allowance(address(this), address(positionManager)) != 0) {
            revert BalanceDeltaMismatch();
        }
    }

    function _tokens(PairState storage p) private view returns (address, address) {
        return p.stockIs0 ? (p.stockToken, p.usdg) : (p.usdg, p.stockToken);
    }

    function _toOrder(PairState storage p, uint256 s, uint256 u)
        private
        view
        returns (uint256, uint256)
    {
        return p.stockIs0 ? (s, u) : (u, s);
    }

    function _fromOrder(PairState storage p, uint256 a, uint256 b)
        private
        view
        returns (uint256, uint256)
    {
        return p.stockIs0 ? (a, b) : (b, a);
    }

    function _pair(bytes32 id) private view returns (PairState storage p) {
        p = _pairs[id];
        if (address(p.pool) == address(0)) revert UnknownPair();
    }

    function _validPrice(uint160 ref) private pure {
        if (ref < TickMath.MIN_SQRT_PRICE || ref >= TickMath.MAX_SQRT_PRICE) {
            revert InvalidReferencePrice();
        }
    }

    function _deadline(uint256 d) private view {
        if (d < block.timestamp || d > type(uint48).max) revert InvalidDeadline();
    }
}
