// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { RamsesV3PairedAdapter, IRamsesReferenceOracle } from "./RamsesV3PairedAdapter.sol";
import { RamsesOracleGuard } from "./RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesNfp } from "./interfaces/IRamsesV3.sol";
import { RamsesSwapLib } from "./libraries/RamsesSwapLib.sol";

/// @notice Phase 1 custody adapter plus a vault-only, feed-gated exact-input settlement route.
contract RamsesVaultAdapter is RamsesV3PairedAdapter {
    RamsesSwapLib.CallbackState private _swap;
    constructor(address vault_, IRamsesNfp manager_, RamsesOracleGuard guard_)
        RamsesV3PairedAdapter(vault_, manager_, IRamsesReferenceOracle(address(guard_)))
    { }

    function poolOf(bytes32 id) public view returns (IRamsesPool) {
        this.removalTolerance(id); // require an adapter registration, not only a guard configuration
        return RamsesOracleGuard(address(oracle)).poolOf(id);
    }

    function swapExactInput(
        bytes32 id,
        address tokenIn,
        uint256 amount,
        uint256 minOut,
        uint256 deadline
    ) external onlyVault nonReentrant returns (uint256 used, uint256 output) {
        if (deadline < block.timestamp || deadline > block.timestamp + 30 minutes) {
            revert InvalidDeadline();
        }
        IRamsesPool pool = poolOf(id);
        oracle.referenceSqrtPriceX96(id, false);
        (used, output) = RamsesSwapLib.execute(_swap, pool, vault, tokenIn, amount, minOut);
        oracle.referenceSqrtPriceX96(id, false);
    }

    function uniswapV3SwapCallback(int256 a, int256 b, bytes calldata) external {
        RamsesSwapLib.callback(_swap, a, b);
    }
}
