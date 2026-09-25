// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { IStockOracleGuard } from "../src/interfaces/IStockOracleGuard.sol";
import { IAssetPriceSource } from "peridot/margin/RobinhoodMarginPriceOracle.sol";

/// @notice Same asset prices as lending, available only while the vault's guard validates them.
/// @dev The deployed lending oracle can return a cached price after staleness.
///      This gate keeps that fallback out of margin without changing lending or vault policy.
contract GuardedMarginPriceSource is IAssetPriceSource {
    IAssetPriceSource public immutable lendingSource;
    IStockOracleGuard public immutable guard;
    bytes32 public immutable pairId;
    address public immutable stock;
    address public immutable usd;

    constructor(address source_, address guard_, bytes32 pairId_, address stock_, address usd_) {
        require(source_.code.length > 0 && guard_.code.length > 0, "PRICE_SOURCE_CODE");
        require(stock_ != usd_ && stock_.code.length > 0 && usd_.code.length > 0, "ASSET_CODE");
        require(pairId_ != bytes32(0), "PAIR_ID");
        lendingSource = IAssetPriceSource(source_);
        guard = IStockOracleGuard(guard_);
        pairId = pairId_;
        stock = stock_;
        usd = usd_;
    }

    function assetPrices(address asset) external view returns (uint256) {
        if (asset != stock && asset != usd) return 0;
        try guard.pricesUSD18(pairId) returns (uint256 stockPrice, uint256 usdPrice) {
            uint256 guarded = asset == stock ? stockPrice : usdPrice;
            if (guarded == 0) return 0;
            try lendingSource.assetPrices(asset) returns (uint256 lendingPrice) {
                // Fail closed on disagreement as well as stale/paused/incomplete feed rounds.
                return lendingPrice == guarded ? guarded : 0;
            } catch {
                return 0;
            }
        } catch {
            return 0;
        }
    }
}
