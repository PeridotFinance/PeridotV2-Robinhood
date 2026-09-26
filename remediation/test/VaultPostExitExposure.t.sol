// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { RobinhoodBoostedVaultTest } from "baseline/test/RobinhoodBoostedVault.t.sol";

contract VaultPostExitExposureTest is RobinhoodBoostedVaultTest {
    function testArchivedVaultLetsBackedSideExitBeforeSharedLoss() external {
        _depositPair(10e18, 1_000e6);
        vm.prank(keeper);
        vault.rebalance(PAIR_ID, block.timestamp + 60);
        usdg.mint(address(adapter), 100e6);
        adapter.setPosition(PAIR_ID, 9e18, 1_100e6);
        uint128 liquidity = adapter.positionState(PAIR_ID).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR_ID, liquidity, block.timestamp + 60);
        vault.setPairPause(PAIR_ID, true, true, false);
        // No LP remains, but one promised stock token is backed by dollar surplus.
        // A stock price increase therefore still creates a shared benchmark deficit.
        oracle.setPrices(200e18, 1e18);
        vm.prank(usdgAccount);
        (uint256 returned,) =
            vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(returned, 1_000e6, "reproduces the archived unguarded exit");
    }
}
