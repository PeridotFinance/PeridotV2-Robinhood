// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { IRamsesPool } from "../interfaces/IRamsesV3.sol";

/// @notice Exact-input settlement only; called under the adapter's shared reentrancy lock.
library RamsesSwapLib {
    using SafeERC20 for IERC20;

    struct CallbackState {
        address pool;
        address token;
        uint256 remaining;
        bool zeroForOne;
    }
    error InvalidSwap();
    error InvalidCallback();
    error BalanceDeltaMismatch();

    function execute(
        CallbackState storage state,
        IRamsesPool pool,
        address vault,
        address tokenIn,
        uint256 amount,
        uint256 minOut
    ) external returns (uint256 used, uint256 output) {
        address t0 = pool.token0();
        address t1 = pool.token1();
        if (
            (tokenIn != t0 && tokenIn != t1) || amount == 0 || amount > uint256(type(int256).max)
                || minOut == 0
        ) revert InvalidSwap();
        bool zero = tokenIn == t0;
        IERC20 input = IERC20(tokenIn);
        IERC20 out = IERC20(zero ? t1 : t0);
        uint256 initialIn = input.balanceOf(address(this));
        uint256 initialOut = out.balanceOf(address(this));
        uint256 vaultIn = input.balanceOf(vault);
        input.safeTransferFrom(vault, address(this), amount);
        if (
            input.balanceOf(address(this)) != initialIn + amount
                || input.balanceOf(vault) + amount != vaultIn
        ) revert BalanceDeltaMismatch();
        state.pool = address(pool);
        state.token = tokenIn;
        state.remaining = amount;
        state.zeroForOne = zero;
        (int256 a, int256 b) = pool.swap(
            address(this),
            zero,
            int256(amount),
            zero ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1,
            ""
        );
        if ((zero ? a : b) != int256(amount) || (zero ? b : a) >= 0 || state.remaining != 0) {
            revert InvalidSwap();
        }
        output = uint256(-(zero ? b : a));
        used = amount;
        delete state.pool;
        delete state.token;
        delete state.remaining;
        if (
            output < minOut || input.balanceOf(address(this)) != initialIn
                || out.balanceOf(address(this)) != initialOut + output
        ) revert BalanceDeltaMismatch();
        uint256 recipientBefore = out.balanceOf(vault);
        out.safeTransfer(vault, output);
        if (
            out.balanceOf(address(this)) != initialOut
                || out.balanceOf(vault) != recipientBefore + output
        ) revert BalanceDeltaMismatch();
    }

    function callback(CallbackState storage state, int256 a, int256 b) internal {
        if (msg.sender != state.pool || state.pool == address(0)) revert InvalidCallback();
        int256 owed = state.zeroForOne ? a : b;
        if (owed <= 0 || (state.zeroForOne ? b : a) >= 0 || uint256(owed) > state.remaining) {
            revert InvalidCallback();
        }
        state.remaining -= uint256(owed);
        IERC20 token = IERC20(state.token);
        uint256 before_ = token.balanceOf(msg.sender);
        token.safeTransfer(msg.sender, uint256(owed));
        if (token.balanceOf(msg.sender) != before_ + uint256(owed)) revert BalanceDeltaMismatch();
    }
}
