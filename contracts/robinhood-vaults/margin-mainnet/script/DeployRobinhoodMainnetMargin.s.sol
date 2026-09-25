// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { PrepareRobinhoodMainnetMargin } from "./PrepareRobinhoodMainnetMargin.s.sol";

/// @notice User-signed staged deployment. The runner supplies fresh pins and verifies receipts.
/// @dev Final stage is activate(), not finishCanary(). No user positions are opened.
contract DeployRobinhoodMainnetMargin is PrepareRobinhoodMainnetMargin {
    /// @notice Restore ordinary lending immediately after the accounting migration.
    /// @dev Margin stays paused throughout its governance delays.
    function resumeBorrowing() external {
        _confirm();
        vm.startBroadcast(ACTOR);
        _resumeBorrowingAfterMigration();
        vm.stopBroadcast();
    }

    /// @notice Run after the queued pair-risk delay, then wait a separate activation delay.
    function configureAndQueue() external {
        _load();
        require(config.queuedActions(keccak256("unpauseOpens")) == 0, "ALREADY_QUEUED");
        vm.startBroadcast(ACTOR);
        _applyRisk();
        _fundCanary();
        config.queueUnpauseOpens();
        vm.stopBroadcast();
    }
}
