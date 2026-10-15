// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";

interface IKeeperRoleVault {
    function KEEPER_ROLE() external view returns (bytes32);
    function CONFIG_ROLE() external view returns (bytes32);
    function GUARDIAN_ROLE() external view returns (bytes32);
    function DEFAULT_ADMIN_ROLE() external view returns (bytes32);
    function hasRole(bytes32 role, address account) external view returns (bool);
    function grantRole(bytes32 role, address account) external;
}

/// @notice Grants KEEPER_ROLE (only) on the vault to the dedicated cloud keeper wallet, through the
/// timelock. KEEPER_ROLE allows checkpoint, rebalance, recenter, collectFees and burnEmptyPosition,
/// every one of them bounded on-chain; it cannot change policy, pauses or any config.
/// @dev Env: KEEPER_ADDRESS (public address from keeper/create_wallet.py). Queue, wait the timelock's
/// delay, then execute. The wallet must hold no other role, and must not be the governor.
abstract contract GrantKeeperBase is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    bytes32 constant IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    address constant EXPECTED_VAULT_IMPL = 0x53A23Ef7A5639f237625Ff7c89Ff9e9Ecf1DD18a;

    function _keeper() internal view returns (address keeper) {
        keeper = vm.envAddress("KEEPER_ADDRESS");
        require(keeper != address(0) && keeper != GOVERNOR && keeper != TIMELOCK, "BAD_KEEPER");
        require(keeper.code.length == 0, "KEEPER_MUST_BE_AN_EOA");
    }

    function _salt(address keeper) internal pure returns (bytes32) {
        return keccak256(abi.encode("PERIDOT_RANGE_KEEPER_ROLE_2026_10", keeper));
    }

    function _payload(address keeper) internal view returns (bytes memory) {
        return abi.encodeCall(
            IKeeperRoleVault.grantRole, (IKeeperRoleVault(VAULT).KEEPER_ROLE(), keeper)
        );
    }

    function _preflight(address keeper) internal view {
        require(block.chainid == 4663, "WRONG_CHAIN");
        require(
            address(uint160(uint256(vm.load(VAULT, IMPLEMENTATION_SLOT)))) == EXPECTED_VAULT_IMPL,
            "VAULT_IMPLEMENTATION_CHANGED"
        );
        IKeeperRoleVault vault = IKeeperRoleVault(VAULT);
        require(
            !vault.hasRole(vault.DEFAULT_ADMIN_ROLE(), keeper)
                && !vault.hasRole(vault.CONFIG_ROLE(), keeper)
                && !vault.hasRole(vault.GUARDIAN_ROLE(), keeper),
            "KEEPER_HOLDS_ADMIN_ROLE"
        );
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        require(timelock.getMinDelay() >= 1 hours, "TIMELOCK_DELAY_TOO_SHORT");
        require(timelock.getMinDelay() <= 2 hours, "TIMELOCK_DELAY_ABOVE_PLAN");
        require(timelock.hasRole(timelock.PROPOSER_ROLE(), GOVERNOR), "PROPOSER_CHANGED");
        require(timelock.hasRole(timelock.EXECUTOR_ROLE(), GOVERNOR), "EXECUTOR_CHANGED");
    }
}

contract QueueGrantRangeKeeperRole is GrantKeeperBase {
    function run() external {
        address keeper = _keeper();
        _preflight(keeper);
        IKeeperRoleVault vault = IKeeperRoleVault(VAULT);
        require(!vault.hasRole(vault.KEEPER_ROLE(), keeper), "ALREADY_KEEPER");
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        bytes memory payload = _payload(keeper);
        bytes32 op = timelock.hashOperation(VAULT, 0, payload, bytes32(0), _salt(keeper));
        require(timelock.getTimestamp(op) == 0, "ALREADY_QUEUED");
        vm.startBroadcast(GOVERNOR);
        timelock.schedule(VAULT, 0, payload, bytes32(0), _salt(keeper), timelock.getMinDelay());
        vm.stopBroadcast();
        console2.log("Queued KEEPER_ROLE grant for:", keeper);
        console2.log("Ready at:", timelock.getTimestamp(op));
    }
}

contract ExecuteGrantRangeKeeperRole is GrantKeeperBase {
    function run() external {
        address keeper = _keeper();
        _preflight(keeper);
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        bytes memory payload = _payload(keeper);
        bytes32 op = timelock.hashOperation(VAULT, 0, payload, bytes32(0), _salt(keeper));
        require(timelock.isOperationReady(op), "TIMELOCK_NOT_READY");
        vm.startBroadcast(GOVERNOR);
        timelock.execute(VAULT, 0, payload, bytes32(0), _salt(keeper));
        vm.stopBroadcast();
        IKeeperRoleVault vault = IKeeperRoleVault(VAULT);
        require(vault.hasRole(vault.KEEPER_ROLE(), keeper), "NOT_GRANTED");
        require(
            !vault.hasRole(vault.CONFIG_ROLE(), keeper)
                && !vault.hasRole(vault.GUARDIAN_ROLE(), keeper)
                && !vault.hasRole(vault.DEFAULT_ADMIN_ROLE(), keeper),
            "UNEXPECTED_ADMIN_ROLE"
        );
        console2.log("KEEPER_ROLE granted to:", keeper);
    }
}

/// @notice Cancel a queued grant (e.g. wrong address) before it executes.
contract CancelGrantRangeKeeperRole is GrantKeeperBase {
    function run() external {
        address keeper = _keeper();
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        bytes32 op = timelock.hashOperation(VAULT, 0, _payload(keeper), bytes32(0), _salt(keeper));
        require(timelock.isOperationPending(op), "NOT_PENDING");
        vm.startBroadcast(GOVERNOR);
        timelock.cancel(op);
        vm.stopBroadcast();
        console2.log("Grant cancelled for:", keeper);
    }
}
