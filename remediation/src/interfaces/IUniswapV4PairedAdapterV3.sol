// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";

/// @notice Adapter surface for a ranged (concentrated) position. An unset range means the
/// legacy full range, so existing pairs keep their behaviour until the vault sets one.
interface IUniswapV4PairedAdapterV3 is IUniswapV4PairedAdapter {
    /// @dev Only while the pair has no position NFT. Ticks must be aligned to the pool's spacing.
    function setRange(bytes32 pairId, int24 tickLower, int24 tickUpper) external;

    /// @dev Return to the full range. Only while the pair has no position NFT.
    function clearRange(bytes32 pairId) external;

    function positionTicks(bytes32 pairId)
        external
        view
        returns (int24 tickLower, int24 tickUpper, bool ranged);
}
