// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { IAccessControl } from "@openzeppelin/contracts/access/IAccessControl.sol";
import { RamsesBoostedDelegate } from "../src/lending/RamsesBoostedDelegate.sol";

/// @notice Operator-only tooling. Default prints the ordered timelock batch. queue/execute are
/// simulated by forge unless the OPERATOR explicitly adds --broadcast --account <keystore>.
/// Never accepts a private key, loads a wallet, or assumes a default broadcaster.
contract GrantRamsesKeeperRole is Script {
    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        address sender = vm.envAddress("RAMSES_PUBLIC_SENDER");
        address keeper = vm.envAddress("RAMSES_KEEPER");
        TimelockController timelock = TimelockController(payable(vm.envAddress("RAMSES_TIMELOCK")));
        RamsesBoostedVault vault = RamsesBoostedVault(vm.envAddress("RAMSES_VAULT"));
        require(
            sender != address(0) && keeper != address(0) && keeper != sender
                && keeper != address(timelock),
            "DISTINCT_KEEPER"
        );
        require(keeper.code.length == 0, "KEEPER_EOA_REQUIRED");
        require(
            address(vault).codehash == vm.envBytes32("RAMSES_VAULT_CODEHASH"), "VAULT_CODE_CHANGED"
        );
        require(timelock.getMinDelay() >= 1 hours, "TIMELOCK_DELAY");
        require(vault.hasRole(bytes32(0), address(timelock)), "TIMELOCK_ADMIN_REQUIRED");
        require(
            !vault.hasRole(bytes32(0), keeper) && !vault.hasRole(vault.CONFIG_ROLE(), keeper)
                && !vault.hasRole(vault.GUARDIAN_ROLE(), keeper),
            "PRIVILEGED_KEEPER"
        );
        address[2] memory dependencies =
            [address(vault.oracleGuard()), address(vault.lossReserve())];
        for (uint256 i; i < dependencies.length; i++) {
            IAccessControl controlled = IAccessControl(dependencies[i]);
            require(
                !controlled.hasRole(bytes32(0), keeper)
                    && !controlled.hasRole(keccak256("CONFIG_ROLE"), keeper)
                    && !controlled.hasRole(keccak256("GUARDIAN_ROLE"), keeper),
                "PRIVILEGED_DEPENDENCY_KEEPER"
            );
        }
        bytes32[4] memory roles = [
            bytes32(0),
            timelock.PROPOSER_ROLE(),
            timelock.EXECUTOR_ROLE(),
            timelock.CANCELLER_ROLE()
        ];
        for (uint256 i; i < 4; i++) {
            require(!timelock.hasRole(roles[i], keeper), "KEEPER_TIMELOCK_ROLE");
        }
        address[] memory targets = new address[](3);
        uint256[] memory values = new uint256[](3);
        bytes[] memory payloads = new bytes[](3);
        targets[0] = address(vault);
        payloads[0] = abi.encodeCall(vault.grantRole, (vault.KEEPER_ROLE(), keeper));
        targets[1] = vm.envAddress("RAMSES_STOCK_MARKET");
        targets[2] = vm.envAddress("RAMSES_USDG_MARKET");
        for (uint256 i = 1; i < 3; i++) {
            RamsesBoostedDelegate market = RamsesBoostedDelegate(targets[i]);
            require(market.admin() == address(timelock), "MARKET_ADMIN");
            require(address(market.robinhoodVault()) == address(vault), "MARKET_VAULT");
            payloads[i] = abi.encodeCall(
                market.setVaultConfig,
                (address(vault), market.robinhoodPairId(), market.vaultBufferMantissa(), keeper)
            );
        }
        bytes32 salt = keccak256(abi.encode("RAMSES_KEEPER_V1", address(vault), keeper));
        console2.log("Timelock", address(timelock));
        console2.log("scheduleBatch payload");
        console2.logBytes(
            abi.encodeCall(
                timelock.scheduleBatch,
                (targets, values, payloads, bytes32(0), salt, timelock.getMinDelay())
            )
        );
        console2.log("executeBatch payload");
        console2.logBytes(
            abi.encodeCall(timelock.executeBatch, (targets, values, payloads, bytes32(0), salt))
        );
        string memory mode = vm.envOr("RAMSES_GRANT_MODE", string("payload"));
        if (keccak256(bytes(mode)) == keccak256("payload")) return;
        vm.startBroadcast(sender);
        if (keccak256(bytes(mode)) == keccak256("queue")) {
            timelock.scheduleBatch(
                targets, values, payloads, bytes32(0), salt, timelock.getMinDelay()
            );
        } else {
            require(keccak256(bytes(mode)) == keccak256("execute"), "UNKNOWN_MODE");
            timelock.executeBatch(targets, values, payloads, bytes32(0), salt);
        }
        vm.stopBroadcast();
    }
}
