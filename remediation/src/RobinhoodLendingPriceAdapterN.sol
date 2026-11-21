// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IUsd18PriceSourceN {
    function assetPrices(address asset) external view returns (uint256);
}

interface IMarketUnderlyingN {
    function underlying() external view returns (address);
}

interface ITokenDecimalsN {
    function decimals() external view returns (uint8);
}

/**
 * @title RobinhoodLendingPriceAdapterN
 * @notice Controller price oracle for Peridottroller 0x6148 with an arbitrary, fixed set of markets.
 * @dev Same arithmetic as the live `RobinhoodLendingPriceAdapter` (0xe4e03C2F) and `RobinhoodLendingPriceAdapter3`
 *      (0x74f1ea31): an 18-decimal underlying returns `source.assetPrices(asset)` (USD 1e18 is already Compound's
 *      1e36 / 10**18 scale) and a 6-decimal underlying returns `source.assetPrices(asset) * 1e12`. Any other decimals are
 *      refused at construction. Unlike adapter3, two markets MAY share one underlying (plain pNVDA and the opt-in
 *      upNVDA both price NVDA). Every listed market must be configured here BEFORE `_supportMarket`: an unpriceable
 *      listed market makes every account that enters it revert in the controller's liquidity loop (unliquidatable).
 *      The market set is written once in the constructor; there is no setter, no owner and no upgrade path.
 */
contract RobinhoodLendingPriceAdapterN is IUsd18PriceSourceN {
    bool public constant isPriceOracle = true;
    uint256 public constant MAX_MARKETS = 16;

    IUsd18PriceSourceN public immutable source;

    struct Entry {
        address asset;
        uint64 scale; // 1 for 18 decimals, 1e12 for 6 decimals; 0 = not configured
    }

    mapping(address => Entry) private _entries;
    mapping(address => bool) public isAsset;
    address[] private _markets;

    error InvalidConfiguration();
    error UnsupportedMarket();
    error UnsupportedAsset();

    constructor(address source_, address[] memory markets_) {
        if (source_.code.length == 0 || markets_.length == 0 || markets_.length > MAX_MARKETS) {
            revert InvalidConfiguration();
        }
        source = IUsd18PriceSourceN(source_);
        for (uint256 i = 0; i < markets_.length; i++) {
            address market = markets_[i];
            if (market == address(0) || _entries[market].scale != 0) revert InvalidConfiguration();
            address asset = IMarketUnderlyingN(market).underlying();
            if (asset == address(0)) revert InvalidConfiguration();
            uint8 decimals = ITokenDecimalsN(asset).decimals();
            uint64 scale;
            if (decimals == 18) scale = 1;
            else if (decimals == 6) scale = 1e12;
            else revert InvalidConfiguration();
            _entries[market] = Entry(asset, scale);
            isAsset[asset] = true;
            _markets.push(market);
        }
    }

    function getUnderlyingPrice(address market) external view returns (uint256) {
        Entry memory e = _entries[market];
        if (e.scale == 0) revert UnsupportedMarket();
        return source.assetPrices(e.asset) * e.scale;
    }

    function assetPrices(address asset) external view override returns (uint256) {
        if (!isAsset[asset]) revert UnsupportedAsset();
        return source.assetPrices(asset);
    }

    function markets() external view returns (address[] memory) {
        return _markets;
    }

    function marketAsset(address market) external view returns (address asset, uint256 scale) {
        Entry memory e = _entries[market];
        return (e.asset, e.scale);
    }
}
