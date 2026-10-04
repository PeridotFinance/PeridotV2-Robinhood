// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    RobinhoodBoostedVaultV2CompatibilityTest as RobinhoodBoostedVaultTest
} from "./VaultV2Compatibility.t.sol";
import {
    RobinhoodBoostedVaultV2 as RobinhoodBoostedVault
} from "../src/RobinhoodBoostedVaultV2.sol";

/// @notice Regressions against the unchanged deployed vault source, not a new implementation.
contract VaultV2RecoveryTest is RobinhoodBoostedVaultTest {
    function _compositionExit() internal {
        _depositPair(10e18, 1_000e6);
        vm.prank(keeper);
        vault.rebalance(PAIR_ID, block.timestamp + 60);
        // $2,001 assets versus $2,000 claims: a native USDG shortage, no USD insolvency.
        stock.mint(address(adapter), 1e18);
        adapter.setPosition(PAIR_ID, 11e18, 901e6);
        uint128 liquidity = adapter.positionState(PAIR_ID).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR_ID, liquidity, block.timestamp + 60);
        // Only timelock/config role may clear emergency. Allocation stays paused.
        vault.setPairPause(PAIR_ID, true, true, false);
    }

    function testCompositionDoesNotWriteOffSolventClaimsWhenSwapsPaused() public {
        _compositionExit();
        vm.prank(keeper);
        assertEq(vault.checkpoint(PAIR_ID, block.timestamp + 60), 0);
        vm.prank(usdgAccount);
        (uint256 returned, uint256 loss) =
            vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(returned, 901e6);
        assertEq(loss, 0);
        assertEq(vault.accountedAssets(PAIR_ID, address(usdg)), 99e6);
        assertEq(vault.accountedAssets(PAIR_ID, address(stock)), 10e18);
    }

    function testBoundedSettlementThenCheckpointRecoversAllSurplus() public {
        _compositionExit();
        // Timelock permits settlement swaps while keeping new allocation disabled.
        vault.setPairPause(PAIR_ID, true, false, false);
        vm.prank(usdgAccount);
        (uint256 returned,) =
            vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(returned, 1_000e6);
        uint256 remaining = vault.accountedAssets(PAIR_ID, address(stock));
        vm.prank(stockAccount);
        vault.withdrawForSide(PAIR_ID, address(stock), remaining, receiver, block.timestamp + 60);
        vm.prank(keeper);
        vault.checkpoint(PAIR_ID, block.timestamp + 60);
        uint256 stockRemainder = vault.accountedAssets(PAIR_ID, address(stock));
        uint256 dollarRemainder = vault.accountedAssets(PAIR_ID, address(usdg));
        if (stockRemainder != 0) {
            vm.prank(stockAccount);
            vault.withdrawForSide(
                PAIR_ID, address(stock), stockRemainder, receiver, block.timestamp + 60
            );
        }
        if (dollarRemainder != 0) {
            vm.prank(usdgAccount);
            vault.withdrawForSide(
                PAIR_ID, address(usdg), dollarRemainder, receiver, block.timestamp + 60
            );
        }
        assertEq(vault.ledger(PAIR_ID).stockPrincipal, 0);
        assertEq(vault.ledger(PAIR_ID).usdgPrincipal, 0);
        assertEq(vault.ledger(PAIR_ID).stockIdle, 0);
        assertEq(vault.ledger(PAIR_ID).usdgIdle, 0);
        assertEq(vault.aggregateUsdgPrincipal(address(usdg)), 0);
        assertEq(adapter.positionState(PAIR_ID).liquidity, 0);
        _assertVaultAllowancesZero();
    }

    function testUncheckpointedSurplusIsRecoverableButStillOracleGated() public {
        _depositPair(10e18, 1_000e6);
        vm.prank(keeper);
        vault.rebalance(PAIR_ID, block.timestamp + 60);
        stock.mint(address(adapter), 1e12);
        adapter.setPosition(PAIR_ID, 10e18 + 1e12, 1_000e6);
        uint128 liquidity = adapter.positionState(PAIR_ID).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR_ID, liquidity, block.timestamp + 60);
        // Zero-liquidity idle exits remain available during emergency.
        vm.prank(stockAccount);
        vault.withdrawForSide(PAIR_ID, address(stock), 10e18, receiver, block.timestamp + 60);
        vm.prank(usdgAccount);
        vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(vault.ledger(PAIR_ID).stockPrincipal, 0);
        assertEq(vault.ledger(PAIR_ID).stockIdle, 1e12);
        vault.setPairPause(PAIR_ID, true, true, false);
        oracle.setShouldRevert(true);
        vm.prank(keeper);
        vm.expectRevert("ORACLE");
        vault.checkpoint(PAIR_ID, block.timestamp + 60);
        oracle.setShouldRevert(false);
        vm.prank(keeper);
        vault.checkpoint(PAIR_ID, block.timestamp + 60);
        assertEq(vault.accountedAssets(PAIR_ID, address(stock)), 1e12);
        vm.prank(receiver);
        vm.expectRevert(RobinhoodBoostedVault.UnauthorizedSide.selector);
        vault.withdrawForSide(PAIR_ID, address(stock), 1e12, receiver, block.timestamp + 60);
        vm.prank(stockAccount);
        vault.withdrawForSide(PAIR_ID, address(stock), 1e12, receiver, block.timestamp + 60);
        assertEq(vault.ledger(PAIR_ID).stockIdle, 0);
    }
}
