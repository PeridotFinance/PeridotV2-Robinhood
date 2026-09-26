// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { ProxyAdmin } from "@openzeppelin/contracts/proxy/transparent/ProxyAdmin.sol";
import {
    ITransparentUpgradeableProxy
} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import { RobinhoodBoostedVaultV2 } from "../src/RobinhoodBoostedVaultV2.sol";
import { SettlementLib } from "baseline/src/libraries/SettlementLib.sol";

interface IMintContainment {
    function _setMintPaused(address, bool) external returns (bool);
    function borrowGuardianPaused(address) external view returns (bool);
    function seizeGuardianPaused() external view returns (bool);
}

abstract contract NativeBackingUpgradeBase is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant ADMIN = 0xad2165E6f3b8146D17815968470eDb8B9a0A4ab7;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant LIBRARY = 0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB;
    address constant OLD_IMPLEMENTATION = 0x21c7e1c2cAdeD480fa373c5c9b3F51492B2D50ac;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    bytes32 constant IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    bytes32 constant SALT = keccak256("PERIDOT_VAULT_NATIVE_BACKING_2026_09_26");

    function _preflight() internal view {
        require(block.chainid == 4663, "WRONG_CHAIN");
        require(address(SettlementLib) == LIBRARY, "USE_VAULT_UPGRADE_PROFILE");
        require(
            LIBRARY.codehash == 0x2db5ef48328c828e38fe4e523a7191c4321f0eb501641c65380313097826724d,
            "LIBRARY_CHANGED"
        );
        require(ProxyAdmin(ADMIN).owner() == TIMELOCK, "PROXY_ADMIN_OWNER_CHANGED");
        require(
            address(uint160(uint256(vm.load(VAULT, IMPLEMENTATION_SLOT)))) == OLD_IMPLEMENTATION,
            "IMPLEMENTATION_CHANGED"
        );
        require(
            IMintContainment(CONTROLLER).borrowGuardianPaused(PUSDG)
                && IMintContainment(CONTROLLER).borrowGuardianPaused(PSTOCK)
                && IMintContainment(CONTROLLER).seizeGuardianPaused(),
            "LENDING_CONTAINMENT_REQUIRED"
        );
    }

    function _payload(address implementation) internal pure returns (bytes memory) {
        return abi.encodeCall(
            ProxyAdmin.upgradeAndCall, (ITransparentUpgradeableProxy(VAULT), implementation, "")
        );
    }

    function _checkCandidate(address implementation) internal view {
        require(
            implementation.codehash == keccak256(type(RobinhoodBoostedVaultV2).runtimeCode),
            "CANDIDATE_CODE_MISMATCH"
        );
    }
}

contract DeployAndQueueNativeBacking is NativeBackingUpgradeBase {
    function run() external {
        _preflight();
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        uint256 delay = timelock.getMinDelay();
        require(delay >= 1 hours, "TIMELOCK_DELAY_TOO_SHORT");
        require(timelock.hasRole(timelock.PROPOSER_ROLE(), GOVERNOR), "PROPOSER_CHANGED");
        address implementation = vm.envOr("NEW_VAULT_IMPLEMENTATION", address(0));
        if (implementation != address(0)) _checkCandidate(implementation);
        bool emergency =
            RobinhoodBoostedVaultV2(VAULT).pairConfig(keccak256("NVDA/USDG")).emergencyMode;
        vm.startBroadcast(GOVERNOR);
        require(IMintContainment(CONTROLLER)._setMintPaused(PUSDG, true), "USDG_MINT_PAUSE_FAILED");
        require(
            IMintContainment(CONTROLLER)._setMintPaused(PSTOCK, true), "STOCK_MINT_PAUSE_FAILED"
        );
        RobinhoodBoostedVaultV2(VAULT).setPairPause(keccak256("NVDA/USDG"), true, true, emergency);
        if (implementation == address(0)) implementation = address(new RobinhoodBoostedVaultV2());
        _checkCandidate(implementation);
        bytes memory payload = _payload(implementation);
        bytes32 operation = timelock.hashOperation(ADMIN, 0, payload, bytes32(0), SALT);
        require(timelock.getTimestamp(operation) == 0, "OPERATION_ALREADY_EXISTS");
        timelock.schedule(ADMIN, 0, payload, bytes32(0), SALT, delay);
        vm.stopBroadcast();
        console2.log("Vault V2 implementation:", implementation);
        console2.log("Operation:");
        console2.logBytes32(operation);
        console2.log("Earliest execution timestamp:", timelock.getTimestamp(operation));
        console2.log("Supply, borrowing, ordinary seizure and new LP allocation remain paused.");
    }
}

contract ExecuteNativeBacking is NativeBackingUpgradeBase {
    function run() external {
        _preflight();
        address implementation = vm.envAddress("NEW_VAULT_IMPLEMENTATION");
        _checkCandidate(implementation);
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        bytes memory payload = _payload(implementation);
        bytes32 operation = timelock.hashOperation(ADMIN, 0, payload, bytes32(0), SALT);
        require(timelock.isOperationReady(operation), "TIMELOCK_NOT_READY");
        bytes32 beforeLedger =
            keccak256(abi.encode(RobinhoodBoostedVaultV2(VAULT).ledger(keccak256("NVDA/USDG"))));
        vm.startBroadcast(GOVERNOR);
        timelock.execute(ADMIN, 0, payload, bytes32(0), SALT);
        vm.stopBroadcast();
        require(timelock.isOperationDone(operation), "OPERATION_NOT_DONE");
        require(
            address(uint160(uint256(vm.load(VAULT, IMPLEMENTATION_SLOT)))) == implementation,
            "UPGRADE_NOT_APPLIED"
        );
        require(
            beforeLedger
                == keccak256(
                    abi.encode(RobinhoodBoostedVaultV2(VAULT).ledger(keccak256("NVDA/USDG")))
                ),
            "LEDGER_CHANGED"
        );
        console2.log("Vault V2 applied. Pauses remain enabled; no Safe migration performed.");
    }
}
