// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IUsd18PriceSource {
    function assetPrices(address asset) external view returns (uint256);
}

interface IMarketUnderlying {
    function underlying() external view returns (address);
}

interface ITokenDecimals {
    function decimals() external view returns (uint8);
}

/// @notice Adapts the existing USD18 price API to Compound's 36-underlyingDecimals scale.
/// @dev No storage upgrade, pricing-policy change, or change to the margin price source.
///      The source's existing manual/cached-price trust and freshness limitations remain.
contract RobinhoodLendingPriceAdapter is IUsd18PriceSource {
    bool public constant isPriceOracle = true;
    IUsd18PriceSource public immutable source;
    address public immutable stockMarket;
    address public immutable dollarMarket;
    address public immutable stock;
    address public immutable dollar;

    error InvalidConfiguration();
    error UnsupportedMarket();
    error UnsupportedAsset();

    constructor(address source_, address stockMarket_, address dollarMarket_) {
        if (source_.code.length == 0 || stockMarket_ == dollarMarket_) {
            revert InvalidConfiguration();
        }
        address stock_ = IMarketUnderlying(stockMarket_).underlying();
        address dollar_ = IMarketUnderlying(dollarMarket_).underlying();
        if (
            stock_ == dollar_ || ITokenDecimals(stock_).decimals() != 18
                || ITokenDecimals(dollar_).decimals() != 6
        ) revert InvalidConfiguration();
        source = IUsd18PriceSource(source_);
        stockMarket = stockMarket_;
        dollarMarket = dollarMarket_;
        stock = stock_;
        dollar = dollar_;
    }

    function getUnderlyingPrice(address market) external view returns (uint256) {
        if (market == stockMarket) return source.assetPrices(stock);
        // Checked multiplication rejects malformed / overflowing source prices.
        if (market == dollarMarket) return source.assetPrices(dollar) * 1e12;
        revert UnsupportedMarket();
    }

    /// @notice Compatibility API: USD18 per whole asset, never Compound-scaled.
    function assetPrices(address asset) external view override returns (uint256) {
        if (asset != stock && asset != dollar) revert UnsupportedAsset();
        return source.assetPrices(asset);
    }
}
