// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import {InterestRateModel} from "peridot/InterestRateModel.sol";

/// @notice Immutable kink model. Replace through each market's timelock-only rate setter.
/// Parameters are per native (L1-derived) block, not sequencer blocks; annualization is an estimate.
contract RamsesInterestRateModel is InterestRateModel {
    uint256 public constant BLOCKS_PER_YEAR = 2_628_000; // assumed 12 seconds
    uint256 public constant BASE = 0.02e18 / BLOCKS_PER_YEAR;
    uint256 public constant SLOPE = 0.10e18 / BLOCKS_PER_YEAR;
    uint256 public constant JUMP = 1e18 / BLOCKS_PER_YEAR;
    uint256 public constant KINK = 0.8e18;
    function utilization(uint256 cash, uint256 borrows, uint256 reserves) public pure returns (uint256) {
        if (borrows == 0) return 0;
        uint256 gross = cash + borrows;
        if (gross <= reserves) return 1e18;
        uint256 u = borrows * 1e18 / (gross - reserves);
        return u > 1e18 ? 1e18 : u;
    }
    function getBorrowRate(uint256 cash, uint256 borrows, uint256 reserves) public pure override returns (uint256) {
        uint256 u = utilization(cash, borrows, reserves);
        return BASE + (u <= KINK ? u * SLOPE / 1e18 : KINK * SLOPE / 1e18 + (u - KINK) * JUMP / 1e18);
    }
    function getSupplyRate(uint256 cash, uint256 borrows, uint256 reserves, uint256 factor) external pure override returns (uint256) {
        return getBorrowRate(cash, borrows, reserves) * (1e18 - factor) / 1e18 * utilization(cash, borrows, reserves) / 1e18;
    }
}
