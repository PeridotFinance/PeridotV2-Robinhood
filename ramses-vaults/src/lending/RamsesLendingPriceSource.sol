// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import {RamsesOracleGuard} from "../RamsesOracleGuard.sol";
import {RobinhoodLendingPriceAdapter} from "../../../remediation/src/RobinhoodLendingPriceAdapter.sol";

/// @notice Fresh feed source for the unchanged, decimal-correct lending price adapter.
/// Even USDG pricing validates the stock feed so borrowing either asset fails closed.
contract RamsesLendingPriceSource {
    RamsesOracleGuard public immutable guard;
    bytes32 public immutable pair;
    constructor(RamsesOracleGuard g, bytes32 p) { guard = g; pair = p; }
    function assetPrices(address asset) external view returns (uint256) {
        RamsesOracleGuard.FeedConfig memory c = guard.feedConfig(pair);
        (uint256 stockPrice, uint256 dollarPrice) = guard.pricesUSD18(pair);
        require(asset == c.stockToken || asset == c.usdg, "UNSUPPORTED_ASSET");
        return asset == c.stockToken ? stockPrice : dollarPrice;
    }
}
