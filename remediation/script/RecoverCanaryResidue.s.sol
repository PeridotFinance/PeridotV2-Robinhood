// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { RobinhoodBoostedVaultV2 } from "../src/RobinhoodBoostedVaultV2.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";

/// @notice Two user-local signed calls returning the old canary's accounted residue to its side owner.
/// @dev No deployment, approvals, mainnet impersonation, reserve use or unpause. Do not blindly retry
///      after partial execution: checkpoint and withdrawal are separate mainnet transactions.
contract RecoverCanaryResidue is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    bytes32 constant PRODUCTION = keccak256("NVDA/USDG");
    bytes32 constant CANARY = 0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee;
    bytes32 constant SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    uint256 constant RESIDUE = 24_697_449_583;

    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        RobinhoodBoostedVaultV2 vault = RobinhoodBoostedVaultV2(VAULT);
        address implementation = address(uint160(uint256(vm.load(VAULT, SLOT))));
        require(
            implementation.codehash
                == 0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84,
            "VAULT_IMPLEMENTATION_CHANGED"
        );
        require(vault.hasRole(vault.KEEPER_ROLE(), GOVERNOR), "KEEPER_CHANGED");
        PairConfig memory config = vault.pairConfig(CANARY);
        require(
            keccak256(abi.encode(config))
                == 0x627861ffbfd31e75a3864bcd6cdb20450d14be42b1190d3248b86a03300436aa,
            "CANARY_CONFIG_CHANGED"
        );
        require(
            config.stockToken == STOCK && config.stockAccount == GOVERNOR
                && config.usdgAccount == GOVERNOR,
            "WRONG_SIDE_OWNER"
        );
        require(
            address(vault.liquidityAdapter()) == 0xadA73211711e4790bc83B5d6B39f47fE04D276f3
                && address(vault.oracleGuard()) == 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741
                && address(vault.lossReserve()) == 0x806b182B050f7EcF908758dD6bBF91DB8B2212aF,
            "DEPENDENCIES_CHANGED"
        );
        require(
            vault.liquidityAdapter().positionState(CANARY).tokenId == 0
                && vault.liquidityAdapter().positionState(CANARY).liquidity == 0,
            "CANARY_POSITION_EXISTS"
        );
        PairLedger memory ledger = vault.ledger(CANARY);
        require(
            ledger.stockPrincipal == 0 && ledger.usdgPrincipal == 0 && ledger.stockIdle == RESIDUE
                && ledger.usdgIdle == 0,
            "CANARY_STATE_CHANGED_OR_PARTIAL_ATTEMPT"
        );
        bytes32 productionBefore = keccak256(abi.encode(vault.ledger(PRODUCTION)));
        bytes32 productionConfig = keccak256(abi.encode(vault.pairConfig(PRODUCTION)));
        uint256 ownerBalance = IERC20(STOCK).balanceOf(GOVERNOR);
        uint256 vaultBalance = IERC20(STOCK).balanceOf(VAULT);
        uint256 deadline = block.timestamp + 300;
        vm.startBroadcast(GOVERNOR);
        vault.checkpoint{ gas: 1_000_000 }(CANARY, deadline);
        require(vault.ledger(CANARY).stockPrincipal == RESIDUE, "CHECKPOINT_DID_NOT_CREDIT_RESIDUE");
        (uint256 returned, uint256 loss) =
            vault.withdrawForSide{ gas: 1_000_000 }(CANARY, STOCK, RESIDUE, GOVERNOR, deadline);
        vm.stopBroadcast();
        require(returned == RESIDUE && loss == 0, "RECOVERY_AMOUNT_MISMATCH");
        ledger = vault.ledger(CANARY);
        require(
            ledger.stockPrincipal == 0 && ledger.usdgPrincipal == 0 && ledger.stockIdle == 0
                && ledger.usdgIdle == 0,
            "CANARY_NOT_DRAINED"
        );
        require(
            IERC20(STOCK).balanceOf(GOVERNOR) == ownerBalance + RESIDUE
                && IERC20(STOCK).balanceOf(VAULT) + RESIDUE == vaultBalance,
            "BALANCE_DELTA_MISMATCH"
        );
        require(
            keccak256(abi.encode(vault.ledger(PRODUCTION))) == productionBefore
                && keccak256(abi.encode(vault.pairConfig(PRODUCTION))) == productionConfig,
            "PRODUCTION_CHANGED"
        );
        require(
            keccak256(abi.encode(vault.pairConfig(CANARY))) == keccak256(abi.encode(config)),
            "CANARY_CONFIG_CHANGED_DURING_RECOVERY"
        );
    }
}
