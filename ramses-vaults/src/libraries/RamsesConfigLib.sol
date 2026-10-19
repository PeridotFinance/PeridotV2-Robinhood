// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";

library RamsesConfigLib {
    error InvalidConfiguration();

    function validatePairConfig(bytes32 pairId, PairConfig calldata config) external view {
        if (
            pairId == bytes32(0) || config.stockToken == address(0) || config.usdg == address(0)
                || config.stockAccount == address(0) || config.usdgAccount == address(0)
                || config.stockToken == config.usdg || config.maxSettlementSwapUSDG == 0
                || config.maxCheckpointAge == 0 || config.deprecatedMinDeadlineDelay != 0
                || config.maxDeadlineDelay == 0 || config.maxDeadlineDelay > 30 minutes
                || config.reserveFeeBps > 5_000 || config.maxSwapSlippageBps == 0
                || config.maxSwapSlippageBps > 500 || config.withdrawOverUnwindBps > 2_000
                || config.stockDecimals > 36 || config.usdgDecimals > 36
        ) revert InvalidConfiguration();
        if (config.stockDecimals != IERC20Metadata(config.stockToken).decimals()) {
            revert InvalidConfiguration();
        }
        if (config.usdgDecimals != IERC20Metadata(config.usdg).decimals()) {
            revert InvalidConfiguration();
        }
    }
}
