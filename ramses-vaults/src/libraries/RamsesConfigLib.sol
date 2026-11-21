// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";

library RamsesConfigLib {
    error InvalidConfiguration();

    event PairRiskUpdated(bytes32 indexed pairId, PairConfig config);

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

    /// @notice Applies a risk update. Identity fields cannot change, and the pause flags are NEVER taken
    /// from the update: they move only through `setPairPause`, so a queued update cannot undo a guardian
    /// pause that happened while it waited in the timelock.
    function applyRisk(bytes32 pairId, PairConfig storage current, PairConfig calldata config)
        external
    {
        if (
            config.stockToken != current.stockToken || config.usdg != current.usdg
                || config.stockAccount != current.stockAccount
                || config.usdgAccount != current.usdgAccount
                || config.stockDecimals != current.stockDecimals
                || config.usdgDecimals != current.usdgDecimals
        ) revert InvalidConfiguration();
        bool allocationPaused = current.allocationPaused;
        bool swapsPaused = current.swapsPaused;
        bool emergencyMode = current.emergencyMode;
        current.stockToken = config.stockToken;
        current.usdg = config.usdg;
        current.stockAccount = config.stockAccount;
        current.usdgAccount = config.usdgAccount;
        current.maxPairValueUSDG = config.maxPairValueUSDG;
        current.maxSettlementSwapUSDG = config.maxSettlementSwapUSDG;
        current.maxCheckpointAge = config.maxCheckpointAge;
        current.deprecatedMinDeadlineDelay = config.deprecatedMinDeadlineDelay;
        current.maxDeadlineDelay = config.maxDeadlineDelay;
        current.reserveFeeBps = config.reserveFeeBps;
        current.maxSwapSlippageBps = config.maxSwapSlippageBps;
        current.withdrawOverUnwindBps = config.withdrawOverUnwindBps;
        current.stockDecimals = config.stockDecimals;
        current.usdgDecimals = config.usdgDecimals;
        current.allocationPaused = allocationPaused;
        current.swapsPaused = swapsPaused;
        current.emergencyMode = emergencyMode;
        current.exists = true;
        emit PairRiskUpdated(pairId, current);
    }
}
