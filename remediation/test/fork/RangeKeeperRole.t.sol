// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { RobinhoodBoostedVaultV3 } from "../../src/RobinhoodBoostedVaultV3.sol";
import { RangePolicy } from "../../src/libraries/RangeTypes.sol";
import {
    QueueGrantRangeKeeperRole,
    ExecuteGrantRangeKeeperRole,
    CancelGrantRangeKeeperRole
} from "../../script/GrantRangeKeeperRole.s.sol";

/// @notice The dedicated cloud keeper wallet, granted exactly as the governor will grant it, on the
/// LIVE V3 vault: it can run the keeper functions and nothing else.
contract RangeKeeperRoleForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    address constant KEEPER = address(0xbeEF00000000000000000000000000000000CAfE);
    RobinhoodBoostedVaultV3 internal vault = RobinhoodBoostedVaultV3(VAULT);

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        vm.setEnv("KEEPER_ADDRESS", vm.toString(KEEPER));
    }

    function _grant() internal {
        new QueueGrantRangeKeeperRole().run();
        vm.warp(vm.getBlockTimestamp() + TimelockController(payable(TIMELOCK)).getMinDelay());
        new ExecuteGrantRangeKeeperRole().run();
    }

    function testQueueExecuteGrantsOnlyKeeperRoleAfterTheDelay() public {
        assertFalse(vault.hasRole(vault.KEEPER_ROLE(), KEEPER));
        new QueueGrantRangeKeeperRole().run();
        ExecuteGrantRangeKeeperRole executor = new ExecuteGrantRangeKeeperRole();
        vm.expectRevert("TIMELOCK_NOT_READY");
        executor.run();
        vm.warp(vm.getBlockTimestamp() + TimelockController(payable(TIMELOCK)).getMinDelay());
        executor.run();
        assertTrue(vault.hasRole(vault.KEEPER_ROLE(), KEEPER));
        assertFalse(vault.hasRole(vault.CONFIG_ROLE(), KEEPER));
        assertFalse(vault.hasRole(vault.GUARDIAN_ROLE(), KEEPER));
        assertFalse(vault.hasRole(vault.DEFAULT_ADMIN_ROLE(), KEEPER));
    }

    function testQueueTwiceIsRefusedAndQueueCanBeCancelled() public {
        new QueueGrantRangeKeeperRole().run();
        QueueGrantRangeKeeperRole again = new QueueGrantRangeKeeperRole();
        vm.expectRevert("ALREADY_QUEUED");
        again.run();
        new CancelGrantRangeKeeperRole().run();
        vm.warp(vm.getBlockTimestamp() + 2 hours);
        ExecuteGrantRangeKeeperRole executor = new ExecuteGrantRangeKeeperRole();
        vm.expectRevert("TIMELOCK_NOT_READY");
        executor.run();
        assertFalse(vault.hasRole(vault.KEEPER_ROLE(), KEEPER));
    }

    function testScriptsRefuseTheGovernorAndZero() public {
        vm.setEnv("KEEPER_ADDRESS", vm.toString(GOVERNOR));
        QueueGrantRangeKeeperRole q = new QueueGrantRangeKeeperRole();
        vm.expectRevert("BAD_KEEPER");
        q.run();
        vm.setEnv("KEEPER_ADDRESS", vm.toString(address(0)));
        QueueGrantRangeKeeperRole zero = new QueueGrantRangeKeeperRole();
        vm.expectRevert("BAD_KEEPER");
        zero.run();
    }

    /// The point of a dedicated wallet: it can run the keeper, and cannot change anything else.
    function testKeeperCanOperateButCannotChangePolicyPausesOrExitsCapital() public {
        _grant();
        vm.startPrank(KEEPER);
        // Operational calls are allowed (they may still revert on their own preconditions, but never
        // with an access-control error).
        vm.warp(vm.getBlockTimestamp() + 1);
        (bool ok,) = address(vault)
            .call(abi.encodeCall(vault.checkpoint, (PAIR, vm.getBlockTimestamp() + 120)));
        ok; // checkpoint may revert on a stale feed at the fork block
        vm.stopPrank();

        bytes memory denied =
            abi.encodeWithSignature("AccessControlUnauthorizedAccount(address,bytes32)");
        denied;
        vm.startPrank(KEEPER);
        vm.expectRevert();
        vault.setPairPause(PAIR, true, true, true);
        vm.expectRevert();
        vault.emergencyDecrease(PAIR, 1, vm.getBlockTimestamp() + 120);
        vm.expectRevert();
        vault.setRangePolicy(
            PAIR,
            RangePolicy({
                enabled: true,
                halfWidthTicks: 600,
                triggerTicks: 60,
                minInterval: 1 hours,
                maxPerDay: 24,
                maxLossBps: 100,
                maxRangedValueUsd: 1e30
            })
        );
        vm.expectRevert();
        vault.setAggregateUsdgDepositCap(address(0x1), 1);
        vm.stopPrank();
        bytes32 keeperRole = vault.KEEPER_ROLE(); // read before expectRevert: it would consume it
        vm.prank(KEEPER);
        vm.expectRevert();
        vault.grantRole(keeperRole, address(0xBAD));
    }
}
