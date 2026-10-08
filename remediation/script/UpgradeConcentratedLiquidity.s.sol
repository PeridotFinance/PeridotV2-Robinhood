// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { ProxyAdmin } from "@openzeppelin/contracts/proxy/transparent/ProxyAdmin.sol";
import {
    ITransparentUpgradeableProxy
} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import { SettlementLib } from "baseline/src/libraries/SettlementLib.sol";
import { RobinhoodBoostedVaultV3 } from "../src/RobinhoodBoostedVaultV3.sol";
import { UniswapV4PairedAdapterV3 } from "../src/UniswapV4PairedAdapterV3.sol";
import { RangePolicy } from "../src/libraries/RangeTypes.sol";

/// @notice Concentrated-liquidity upgrade for the live paired vault, through the existing timelock.
/// @dev One `scheduleBatch` (one delay, <= 2h total): upgrade adapter proxy -> upgrade vault proxy ->
/// setRangePolicy. Atomic, so the vault is never live on an adapter that lacks `positionTicks`.
/// It does not touch pause flags: allocation is whatever it is when the batch executes (open today,
/// with a full-range position that the keeper's first `recenter` converts). Nothing here changes
/// lending, margin, caps, or roles. Swaps stay paused.
abstract contract ConcentratedLiquidityBase is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant VAULT_ADMIN = 0xad2165E6f3b8146D17815968470eDb8B9a0A4ab7;
    address constant OLD_VAULT_IMPL = 0x17f0cf262Fbbf27e44756dbA6d852815695E9C4a;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant ADAPTER_ADMIN = 0x5a345842E3304EE97360de1C61699BA0Efea906b;
    address constant OLD_ADAPTER_IMPL = 0xc4Db160750638a92578C0C657Ad6AB7c2a1399F9;
    address constant SETTLEMENT_LIB = 0x813AbFeC0DE50f8674798CbaB72Ed7b5D8CcB9cB;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    bytes32 constant IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    bytes32 constant SALT_UPGRADE = keccak256("PERIDOT_CONCENTRATED_LIQUIDITY_UPGRADE_2026_10");

    function _policy() internal pure returns (RangePolicy memory) {
        return RangePolicy({
            enabled: true,
            halfWidthTicks: 1200, // about +12.7% / -11.3% around the oracle price
            triggerTicks: 300, // recenter after a ~3% oracle move (or once the price leaves the range)
            minInterval: 1 hours,
            maxPerDay: 4,
            maxLossBps: 10, // measured rounding floor is 1 bp at this size
            maxRangedValueUsd: 50e18
        });
    }

    function _impl(address proxy) internal view returns (address) {
        return address(uint160(uint256(vm.load(proxy, IMPLEMENTATION_SLOT))));
    }

    function _preflight() internal view {
        require(block.chainid == 4663, "WRONG_CHAIN");
        require(address(SettlementLib) == SETTLEMENT_LIB, "USE_VAULT_V3_PROFILE");
        require(ProxyAdmin(VAULT_ADMIN).owner() == TIMELOCK, "VAULT_ADMIN_OWNER_CHANGED");
        require(ProxyAdmin(ADAPTER_ADMIN).owner() == TIMELOCK, "ADAPTER_ADMIN_OWNER_CHANGED");
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        require(timelock.getMinDelay() >= 1 hours, "TIMELOCK_DELAY_TOO_SHORT");
        require(timelock.getMinDelay() <= 2 hours, "TIMELOCK_DELAY_ABOVE_PLAN");
        require(timelock.hasRole(timelock.PROPOSER_ROLE(), GOVERNOR), "PROPOSER_CHANGED");
        require(timelock.hasRole(timelock.EXECUTOR_ROLE(), GOVERNOR), "EXECUTOR_CHANGED");
    }

    function _upgradeBatch(address adapterImpl, address vaultImpl)
        internal
        pure
        returns (address[] memory targets, uint256[] memory values, bytes[] memory payloads)
    {
        targets = new address[](3);
        values = new uint256[](3);
        payloads = new bytes[](3);
        targets[0] = ADAPTER_ADMIN;
        payloads[0] = abi.encodeCall(
            ProxyAdmin.upgradeAndCall, (ITransparentUpgradeableProxy(ADAPTER), adapterImpl, "")
        );
        targets[1] = VAULT_ADMIN;
        payloads[1] = abi.encodeCall(
            ProxyAdmin.upgradeAndCall, (ITransparentUpgradeableProxy(VAULT), vaultImpl, "")
        );
        targets[2] = VAULT;
        payloads[2] = abi.encodeCall(RobinhoodBoostedVaultV3.setRangePolicy, (PAIR, _policy()));
    }

    /// @dev Exact bytecode equality with the reviewed build is checked outside the script by
    /// remediation/tools/verify_concentrated_liquidity.py (embedding `type(X).runtimeCode` for
    /// contracts with linked libraries corrupts this script's constants under via-ir).
    function _candidates() internal view returns (address adapterImpl, address vaultImpl) {
        adapterImpl = vm.envAddress("NEW_ADAPTER_IMPLEMENTATION");
        vaultImpl = vm.envAddress("NEW_VAULT_IMPLEMENTATION");
        require(adapterImpl.code.length != 0 && vaultImpl.code.length != 0, "NO_CODE");
    }
}

/// @notice Deploys the two implementations (and the linked `RangeLib`) and queues the batch.
contract DeployAndQueueConcentratedLiquidity is ConcentratedLiquidityBase {
    function run() external {
        _preflight();
        require(_impl(VAULT) == OLD_VAULT_IMPL, "VAULT_IMPLEMENTATION_CHANGED");
        require(_impl(ADAPTER) == OLD_ADAPTER_IMPL, "ADAPTER_IMPLEMENTATION_CHANGED");
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        uint256 delay = timelock.getMinDelay();

        // Re-running with the implementations already deployed (printed by the first run) reuses
        // them, so a repeat cannot deploy again or queue a second, different operation.
        address adapterImpl = vm.envOr("NEW_ADAPTER_IMPLEMENTATION", address(0));
        address vaultImpl = vm.envOr("NEW_VAULT_IMPLEMENTATION", address(0));
        if (adapterImpl == address(0) || vaultImpl == address(0)) {
            vm.startBroadcast(GOVERNOR);
            adapterImpl = address(new UniswapV4PairedAdapterV3());
            vaultImpl = address(new RobinhoodBoostedVaultV3());
            vm.stopBroadcast();
        }

        require(adapterImpl.code.length != 0 && vaultImpl.code.length != 0, "NO_CODE");
        (address[] memory targets, uint256[] memory values, bytes[] memory payloads) =
            _upgradeBatch(adapterImpl, vaultImpl);
        bytes32 upgradeOp =
            timelock.hashOperationBatch(targets, values, payloads, bytes32(0), SALT_UPGRADE);
        require(timelock.getTimestamp(upgradeOp) == 0, "UPGRADE_ALREADY_QUEUED");

        vm.startBroadcast(GOVERNOR);
        timelock.scheduleBatch(targets, values, payloads, bytes32(0), SALT_UPGRADE, delay);
        vm.stopBroadcast();

        console2.log("Adapter V3 implementation:", adapterImpl);
        console2.log("Vault V3 implementation:", vaultImpl);
        console2.log("Upgrade operation (batch):");
        console2.logBytes32(upgradeOp);
        console2.log("Ready at:", timelock.getTimestamp(upgradeOp));
    }
}

/// @notice Executes the upgrade batch once ready, then checks nothing about the pair moved.
contract ExecuteConcentratedLiquidityUpgrade is ConcentratedLiquidityBase {
    function run() external {
        _preflight();
        (address adapterImpl, address vaultImpl) = _candidates();
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        (address[] memory targets, uint256[] memory values, bytes[] memory payloads) =
            _upgradeBatch(adapterImpl, vaultImpl);
        bytes32 op =
            timelock.hashOperationBatch(targets, values, payloads, bytes32(0), SALT_UPGRADE);
        require(timelock.isOperationReady(op), "TIMELOCK_NOT_READY");
        RobinhoodBoostedVaultV3 vault = RobinhoodBoostedVaultV3(VAULT);
        bytes32 ledgerBefore = keccak256(abi.encode(vault.ledger(PAIR)));
        bytes32 configBefore = keccak256(abi.encode(vault.pairConfig(PAIR)));

        vm.startBroadcast(GOVERNOR);
        timelock.executeBatch(targets, values, payloads, bytes32(0), SALT_UPGRADE);
        vm.stopBroadcast();

        require(timelock.isOperationDone(op), "OPERATION_NOT_DONE");
        require(_impl(VAULT) == vaultImpl && _impl(ADAPTER) == adapterImpl, "UPGRADE_NOT_APPLIED");
        require(keccak256(abi.encode(vault.ledger(PAIR))) == ledgerBefore, "LEDGER_CHANGED");
        require(keccak256(abi.encode(vault.pairConfig(PAIR))) == configBefore, "CONFIG_CHANGED");
        (bool enabled,,,,,,) = vault.rangePolicy(PAIR);
        require(enabled, "POLICY_NOT_SET");
        console2.log("Concentrated-liquidity V3 live. Pause flags unchanged. Run the keeper next.");
    }
}
