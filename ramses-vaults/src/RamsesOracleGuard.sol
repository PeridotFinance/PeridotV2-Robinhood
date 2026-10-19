// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { AccessControl } from "@openzeppelin/contracts/access/AccessControl.sol";
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { SafeCast } from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { IRamsesPool, IRamsesGauge } from "./interfaces/IRamsesV3.sol";
import { IRamsesReferenceOracle } from "./RamsesV3PairedAdapter.sol";
interface IPausableStock { function paused() external view returns (bool); }

/// @notice Feed-backed stock/USD guard. USDG is deliberately valued at one dollar.
/// Configuration authority must be the operator's timelock. No stale-price exit bypass.
contract RamsesOracleGuard is AccessControl, IRamsesReferenceOracle {
    using SafeCast for uint256;
    bytes32 public constant CONFIG_ROLE = keccak256("CONFIG_ROLE");
    struct FeedConfig {
        address stockToken;
        address usdg;
        IAggregatorV3 stockFeed;
        IRamsesPool pool;
        IRamsesGauge gauge;
        uint64 maxStaleness;
        uint16 maxPriceDeviationBps;
        uint16 maxRemovalDeviationBps;
        uint8 stockDecimals;
        uint8 usdgDecimals;
        uint8 stockFeedDecimals;
        bool enabled;
    }
    mapping(bytes32 => FeedConfig) private _feeds;
    error InvalidConfiguration();
    error PairNotEnabled();
    error StockOraclePaused();
    error InvalidOracleAnswer(address feed);
    error IncompleteOracleRound(address feed, uint80 roundId, uint80 answeredInRound);
    error StaleOracle(address feed, uint256 updatedAt);
    error PoolMismatch();
    error PriceDeviation(uint256 oraclePrice, uint256 poolPrice);
    error InvalidReferencePrice();
    event FeedConfigured(bytes32 indexed pairId, FeedConfig config);
    event FeedEnabled(bytes32 indexed pairId, bool enabled);
    constructor(address timelock) {
        if (timelock == address(0)) revert InvalidConfiguration();
        _grantRole(DEFAULT_ADMIN_ROLE, timelock);
        _grantRole(CONFIG_ROLE, timelock);
    }
    function configurePair(bytes32 id, FeedConfig calldata c) external onlyRole(CONFIG_ROLE) {
        if (id == 0 || c.stockToken == c.usdg || c.stockToken == address(0) || c.usdg == address(0)
            || c.maxStaleness == 0 || c.maxStaleness > 12 hours
            || c.maxPriceDeviationBps == 0 || c.maxPriceDeviationBps > 500
            || c.maxRemovalDeviationBps < c.maxPriceDeviationBps || c.maxRemovalDeviationBps > 2000
            || c.stockDecimals > 36 || c.usdgDecimals > 36 || c.stockFeedDecimals > 36
            || IERC20Metadata(c.stockToken).decimals() != c.stockDecimals
            || IERC20Metadata(c.usdg).decimals() != c.usdgDecimals
            || c.stockFeed.decimals() != c.stockFeedDecimals) revert InvalidConfiguration();
        address t0 = c.pool.token0(); address t1 = c.pool.token1();
        if (t0 >= t1 || !((t0 == c.usdg && t1 == c.stockToken) || (t0 == c.stockToken && t1 == c.usdg))
            || c.gauge.pool() != address(c.pool)) revert PoolMismatch();
        FeedConfig storage old = _feeds[id];
        // Once associated, identity cannot drift away from the adapter's immutable registration.
        if (old.stockToken != address(0) && (old.stockToken != c.stockToken || old.usdg != c.usdg
            || address(old.pool) != address(c.pool) || address(old.gauge) != address(c.gauge))) revert PoolMismatch();
        IPausableStock(c.stockToken).paused(); // ABI absence fails closed at configuration time too.
        _feeds[id] = c;
        emit FeedConfigured(id, c);
    }
    function setEnabled(bytes32 id, bool enabled) external onlyRole(CONFIG_ROLE) {
        if (_feeds[id].stockToken == address(0)) revert PairNotEnabled();
        _feeds[id].enabled = enabled;
        emit FeedEnabled(id, enabled);
    }
    function feedConfig(bytes32 id) external view returns (FeedConfig memory) { return _feeds[id]; }
    function poolOf(bytes32 id) external view returns (IRamsesPool) { return _feeds[id].pool; }
    function maxPriceDeviationBps(bytes32 id) external view returns (uint16) { return _feeds[id].maxPriceDeviationBps; }
    function maxRemovalDeviationBps(bytes32 id) external view returns (uint16) { return _feeds[id].maxRemovalDeviationBps; }
    function pricesUSD18(bytes32 id) public view returns (uint256 stockPrice, uint256 usdgPrice) {
        FeedConfig storage c = _feeds[id];
        if (!c.enabled) revert PairNotEnabled();
        if (IPausableStock(c.stockToken).paused()) revert StockOraclePaused();
        (uint80 round, int256 answer,, uint256 updated, uint80 answered) = c.stockFeed.latestRoundData();
        if (answer <= 0 || updated == 0 || updated > block.timestamp) revert InvalidOracleAnswer(address(c.stockFeed));
        if (answered < round) revert IncompleteOracleRound(address(c.stockFeed), round, answered);
        if (block.timestamp - updated > c.maxStaleness) revert StaleOracle(address(c.stockFeed), updated);
        stockPrice = VaultMath.scaleToWad(uint256(answer), c.stockFeedDecimals, Math.Rounding.Floor);
        if (stockPrice == 0) revert InvalidOracleAnswer(address(c.stockFeed));
        usdgPrice = 1e18;
    }
    function referenceSqrtPriceX96(bytes32 id, bool allocating) external view returns (uint160 ref) {
        (,, ref) = _validate(id, _feeds[id].pool, allocating);
    }
    function validatePoolPrice(bytes32 id, IRamsesPool pool) external view returns (uint256, uint256, uint160) {
        return _validate(id, pool, true);
    }
    function validateRemovalPrice(bytes32 id, IRamsesPool pool) external view returns (uint256, uint256, uint160) {
        return _validate(id, pool, false);
    }
    function _validate(bytes32 id, IRamsesPool pool, bool allocating) private view returns (uint256 price, uint256 spotPrice, uint160 ref) {
        FeedConfig storage c = _feeds[id];
        (price,) = pricesUSD18(id);
        if (address(pool) != address(c.pool)) revert PoolMismatch();
        (uint160 sqrtPrice,,,,,,) = pool.slot0();
        if (sqrtPrice < TickMath.MIN_SQRT_PRICE || sqrtPrice >= TickMath.MAX_SQRT_PRICE) revert InvalidReferencePrice();
        // Derive the quote from slot0's sqrt price, not its separately reported tick.
        uint256 quoted;
        uint256 amount = 10 ** c.stockDecimals;
        if (sqrtPrice <= type(uint128).max) {
            uint256 ratio = uint256(sqrtPrice) * sqrtPrice;
            quoted = pool.token0() == c.stockToken ? Math.mulDiv(ratio, amount, 1 << 192) : Math.mulDiv(1 << 192, amount, ratio);
        } else {
            uint256 ratio = Math.mulDiv(sqrtPrice, sqrtPrice, 1 << 64);
            quoted = pool.token0() == c.stockToken ? Math.mulDiv(ratio, amount, 1 << 128) : Math.mulDiv(1 << 128, amount, ratio);
        }
        spotPrice = VaultMath.scaleToWad(quoted, c.usdgDecimals, Math.Rounding.Floor);
        uint256 difference = spotPrice > price ? spotPrice - price : price - spotPrice;
        uint16 bound = allocating ? c.maxPriceDeviationBps : c.maxRemovalDeviationBps;
        if (difference > Math.mulDiv(price, bound, 10_000)) revert PriceDeviation(price, spotPrice);
        ref = _referenceSqrtPriceX96(c, pool.token0(), price);
    }
    function _referenceSqrtPriceX96(
        FeedConfig storage config,
        address currency0,
        uint256 oracleStockInUsdg
    ) internal view returns (uint160 referenceSqrtPriceX96) {
        uint256 stockScale = 10 ** config.stockDecimals;
        uint256 usdgScale = 10 ** config.usdgDecimals;
        uint256 rawRatioWad;
        if (currency0 == config.stockToken) {
            rawRatioWad = Math.mulDiv(oracleStockInUsdg, usdgScale, stockScale);
        } else {
            uint256 inversePriceWad = Math.mulDiv(1e18, 1e18, oracleStockInUsdg);
            rawRatioWad = Math.mulDiv(inversePriceWad, stockScale, usdgScale);
        }
        if (rawRatioWad == 0) revert InvalidReferencePrice();

        uint256 maxRawRatioWad = Math.mulDiv(type(uint256).max, 1e18, uint256(1) << 128);
        if (rawRatioWad > maxRawRatioWad) revert InvalidReferencePrice();
        uint256 ratioX128 = Math.mulDiv(rawRatioWad, uint256(1) << 128, 1e18);
        if (ratioX128 == 0) revert InvalidReferencePrice();
        uint256 sqrtPrice = Math.sqrt(ratioX128) << 32;
        if (
            sqrtPrice < TickMath.MIN_SQRT_PRICE || sqrtPrice >= TickMath.MAX_SQRT_PRICE
                || sqrtPrice > type(uint160).max
        ) revert InvalidReferencePrice();
        referenceSqrtPriceX96 = sqrtPrice.toUint160();
    }

}
