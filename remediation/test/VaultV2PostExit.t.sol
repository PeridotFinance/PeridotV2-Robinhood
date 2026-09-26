// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { RobinhoodBoostedVaultV2CompatibilityTest } from "./VaultV2Compatibility.t.sol";

contract VaultV2PostExitTest is RobinhoodBoostedVaultV2CompatibilityTest {
    function _closeWithCompositionExposure() internal {
        _depositPair(10e18, 1_000e6);
        vm.prank(keeper);
        vault.rebalance(PAIR_ID, block.timestamp + 60);
        usdg.mint(address(adapter), 100e6);
        adapter.setPosition(PAIR_ID, 9e18, 1_100e6);
        uint128 liquidity = adapter.positionState(PAIR_ID).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR_ID, liquidity, block.timestamp + 60);
        vault.setPairPause(PAIR_ID, true, true, false);
    }

    function testClosedPositionCompositionSharesPriceLossBeforeExit() external {
        _closeWithCompositionExposure();
        oracle.setPrices(200e18, 1e18);
        vm.prank(usdgAccount);
        (uint256 returned, uint256 realizedLoss) =
            vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(returned, 966_666_666);
        assertEq(realizedLoss, 33_333_334);
        assertEq(vault.ledger(PAIR_ID).stockPrincipal, uint256(10e18) * 2_900 / 3_000);
        assertEq(vault.ledger(PAIR_ID).cumulativeLossUSDG, 100e18);
    }

    function testClosedCompositionCannotExitAheadOfUnavailableOracle() external {
        _closeWithCompositionExposure();
        oracle.setShouldRevert(true);
        assertEq(vault.withdrawableAssets(PAIR_ID, address(usdg)), 0);
        assertEq(vault.withdrawableAssets(PAIR_ID, address(stock)), 0);
        vm.prank(usdgAccount);
        vm.expectRevert("ORACLE");
        vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(vault.ledger(PAIR_ID).usdgPrincipal, 1_000e6);
    }

    function testClosedCompositionRemainsBlockedDuringEmergency() external {
        _closeWithCompositionExposure();
        vault.setPairPause(PAIR_ID, true, true, true);
        assertEq(vault.withdrawableAssets(PAIR_ID, address(usdg)), 0);
        vm.prank(usdgAccount);
        vm.expectRevert(bytes4(keccak256("EmergencyMode()")));
        vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
    }

    function testFuzzClosedCompositionRecognizesSharedDeficit(uint96 rawPrice) external {
        _closeWithCompositionExposure();
        uint256 price = bound(rawPrice, 101e18, 10_000e18);
        oracle.setPrices(price, 1e18);
        uint256 benchmark = 10 * price + 1_000e18;
        uint256 gross = 9 * price + 1_100e18;
        vm.prank(usdgAccount);
        (uint256 returned,) =
            vault.withdrawForSide(PAIR_ID, address(usdg), 1_000e6, receiver, block.timestamp + 60);
        assertEq(returned, 1_000e6 * gross / benchmark);
        assertEq(vault.ledger(PAIR_ID).stockPrincipal, 10e18 * gross / benchmark);
    }
}
