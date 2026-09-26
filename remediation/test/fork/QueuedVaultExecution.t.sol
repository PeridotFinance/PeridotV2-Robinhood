// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { ExecuteNativeBacking } from "../../script/UpgradeNativeBacking.s.sol";
import { RobinhoodBoostedVaultV2 } from "../../src/RobinhoodBoostedVaultV2.sol";

/// @notice Rehearse the actual September 26 queued operation; only fork time/state changes.
contract QueuedVaultExecutionForkTest is Test {
    function testActualQueuedOperationExecutesAfterDelayWithoutChangingPairLedgers() external {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        assertEq(block.chainid, 4663);
        address candidate = 0x17f0cf262Fbbf27e44756dbA6d852815695E9C4a;
        assertEq(
            candidate.codehash, 0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84
        );
        RobinhoodBoostedVaultV2 vault =
            RobinhoodBoostedVaultV2(0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f);
        TimelockController timelock =
            TimelockController(payable(0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498));
        bytes32 operation = 0xacc0e39a4de59d916d7ddb337dedc5717ab64479bb449714d70fcd05ad7b6067;
        uint256 readyAt = timelock.getTimestamp(operation);
        assertEq(readyAt, 1790457352);
        assertFalse(timelock.isOperationDone(operation));
        bytes32 production = keccak256("NVDA/USDG");
        bytes32 canary = 0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee;
        bytes memory beforeState = abi.encode(
            vault.ledger(production), vault.ledger(canary), vault.pairConfig(production)
        );
        vm.setEnv("NEW_VAULT_IMPLEMENTATION", vm.toString(candidate));
        ExecuteNativeBacking executor = new ExecuteNativeBacking();
        if (block.timestamp < readyAt) {
            vm.expectRevert("TIMELOCK_NOT_READY");
            executor.run();
            vm.warp(readyAt);
        }
        executor.run();
        assertTrue(timelock.isOperationDone(operation));
        assertEq(
            abi.encode(
                vault.ledger(production), vault.ledger(canary), vault.pairConfig(production)
            ),
            beforeState
        );
        assertTrue(vault.pairConfig(production).allocationPaused);
        assertTrue(vault.pairConfig(production).swapsPaused);
    }
}
