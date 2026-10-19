// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";
import { RobinhoodBoostedVaultV3 } from "../src/RobinhoodBoostedVaultV3.sol";
import { RangePolicy } from "../src/libraries/RangeTypes.sol";

/// @notice Widens the live range policy from +-1200 to +-2400 ticks (trigger 300 -> 600) through
/// the existing timelock. One `schedule` of one `setRangePolicy` call; nothing else changes.
/// @dev The new width does not move live liquidity. It is applied at the next due `recenter`.
/// The queued operation only matches while the policy is still the one this was written against.
abstract contract SetRangePolicyBase is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    bytes32 constant SALT = keccak256("PERIDOT_RANGE_POLICY_2400_2026_10");

    function _target() internal pure returns (RangePolicy memory) {
        return RangePolicy({
            enabled: true,
            halfWidthTicks: 2400, // about +27% / -21% around the oracle price
            triggerTicks: 600, // recenter after a ~6% oracle move (or once the price leaves the range)
            minInterval: 1 hours,
            maxPerDay: 4,
            maxLossBps: 10,
            maxRangedValueUsd: 50e18
        });
    }

    function _payload() internal pure returns (bytes memory) {
        return abi.encodeCall(RobinhoodBoostedVaultV3.setRangePolicy, (PAIR, _target()));
    }

    function _preflight() internal view returns (TimelockController timelock, bytes32 op) {
        require(block.chainid == 4663, "WRONG_CHAIN");
        timelock = TimelockController(payable(TIMELOCK));
        require(timelock.getMinDelay() >= 1 hours, "TIMELOCK_DELAY_TOO_SHORT");
        require(timelock.getMinDelay() <= 2 hours, "TIMELOCK_DELAY_ABOVE_PLAN");
        require(timelock.hasRole(timelock.PROPOSER_ROLE(), GOVERNOR), "PROPOSER_CHANGED");
        require(timelock.hasRole(timelock.EXECUTOR_ROLE(), GOVERNOR), "EXECUTOR_CHANGED");
        (bool enabled, uint16 half, uint16 trigger,,,,) =
            RobinhoodBoostedVaultV3(VAULT).rangePolicy(PAIR);
        require(enabled && half == 1200 && trigger == 300, "POLICY_ALREADY_CHANGED");
        op = timelock.hashOperation(VAULT, 0, _payload(), bytes32(0), SALT);
    }
}

contract QueueRangePolicy is SetRangePolicyBase {
    function run() external {
        (TimelockController timelock, bytes32 op) = _preflight();
        require(timelock.getTimestamp(op) == 0, "ALREADY_QUEUED");
        uint256 delay = timelock.getMinDelay();
        vm.startBroadcast(GOVERNOR);
        timelock.schedule(VAULT, 0, _payload(), bytes32(0), SALT, delay);
        vm.stopBroadcast();
        require(timelock.isOperationPending(op), "NOT_QUEUED");
        console2.log("Range policy operation:");
        console2.logBytes32(op);
        console2.log("Ready at:", timelock.getTimestamp(op));
    }
}

contract ExecuteRangePolicy is SetRangePolicyBase {
    function run() external {
        (TimelockController timelock, bytes32 op) = _preflight();
        require(timelock.isOperationReady(op), "TIMELOCK_NOT_READY");
        RobinhoodBoostedVaultV3 vault = RobinhoodBoostedVaultV3(VAULT);
        bytes32 ledgerBefore = keccak256(abi.encode(vault.ledger(PAIR)));
        bytes32 configBefore = keccak256(abi.encode(vault.pairConfig(PAIR)));
        vm.startBroadcast(GOVERNOR);
        timelock.execute(VAULT, 0, _payload(), bytes32(0), SALT);
        vm.stopBroadcast();
        require(timelock.isOperationDone(op), "OPERATION_NOT_DONE");
        (
            bool enabled,
            uint16 half,
            uint16 trigger,
            uint32 interval,
            uint8 perDay,
            uint16 lossBps,
            uint128 cap
        ) = vault.rangePolicy(PAIR);
        require(enabled && half == 2400 && trigger == 600, "POLICY_NOT_APPLIED");
        require(
            interval == 1 hours && perDay == 4 && lossBps == 10 && cap == 50e18,
            "OTHER_FIELDS_CHANGED"
        );
        require(keccak256(abi.encode(vault.ledger(PAIR))) == ledgerBefore, "LEDGER_CHANGED");
        require(keccak256(abi.encode(vault.pairConfig(PAIR))) == configBefore, "CONFIG_CHANGED");
        console2.log("Range policy is now +-2400 / trigger 600. Applies at the next recenter.");
    }
}

contract CancelRangePolicy is SetRangePolicyBase {
    function run() external {
        (TimelockController timelock, bytes32 op) = _preflight();
        require(timelock.isOperationPending(op), "NOT_PENDING");
        vm.startBroadcast(GOVERNOR);
        timelock.cancel(op);
        vm.stopBroadcast();
        require(timelock.getTimestamp(op) == 0, "NOT_CANCELLED");
        console2.log("Range policy operation cancelled.");
    }
}
