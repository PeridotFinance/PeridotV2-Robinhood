// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { InterestRateModel } from "peridot/InterestRateModel.sol";

/// @notice Immutable kink model. Replace through each market's timelock-only rate setter.
/// Parameters are per native (L1-derived) block, not sequencer blocks; annualization is an estimate.
contract RamsesInterestRateModel is InterestRateModel {
    uint256 public constant BLOCKS_PER_YEAR = 2_628_000; // assumed 12 seconds
    uint256 public immutable BASE;
    uint256 public immutable SLOPE;
    uint256 public immutable JUMP;
    uint256 public immutable KINK;

    constructor(uint256 base, uint256 slope, uint256 jump, uint256 kink) {
        require(kink > 0 && kink <= 1e18, "INVALID_KINK");
        BASE = base;
        SLOPE = slope;
        JUMP = jump;
        KINK = kink;
    }

    function utilization(uint256 cash, uint256 borrows, uint256 reserves)
        public
        pure
        returns (uint256)
    {
        if (borrows == 0) return 0;
        uint256 gross = cash + borrows;
        if (gross <= reserves) return 1e18;
        uint256 u = borrows * 1e18 / (gross - reserves);
        return u > 1e18 ? 1e18 : u;
    }

    function getBorrowRate(uint256 cash, uint256 borrows, uint256 reserves)
        public
        view
        override
        returns (uint256)
    {
        uint256 u = utilization(cash, borrows, reserves);
        return
            BASE + (u <= KINK ? u * SLOPE / 1e18 : KINK * SLOPE / 1e18 + (u - KINK) * JUMP / 1e18);
    }

    function getSupplyRate(uint256 cash, uint256 borrows, uint256 reserves, uint256 factor)
        external
        view
        override
        returns (uint256)
    {
        return getBorrowRate(cash, borrows, reserves) * (1e18 - factor) / 1e18
            * utilization(cash, borrows, reserves) / 1e18;
    }
}
