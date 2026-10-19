// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { LendingFixture } from "./LendingFixture.sol";
import { RamsesBoostedDelegate } from "../src/lending/RamsesBoostedDelegate.sol";
import { PToken } from "peridot/PToken.sol";

contract LaunchTest is LendingFixture {
    function market(address token, uint256 initialRate, string memory symbol)
        internal
        override
        returns (RamsesBoostedDelegate)
    {
        return super.market(token, initialRate / (token == address(stock) ? 100000 : 1000), symbol);
    }

    function setUp() public override {
        super.setUp();
        sMarket.setSupplyCap(4.35e18);
        uMarket.setSupplyCap(1000e6);
        PToken[] memory ms = new PToken[](2);
        ms[0] = PToken(stockAccount);
        ms[1] = PToken(usdgAccount);
        uint256[] memory caps = new uint256[](2);
        caps[0] = 4.35e18;
        caps[1] = 1000e6;
        controller._setMarketBorrowCaps(ms, caps);
        enableBorrow();
    }

    function testApprovedLaunchWiringAndRates() public view {
        (, uint256 sc,) = controller.markets(stockAccount);
        (, uint256 uc,) = controller.markets(usdgAccount);
        assertEq(sc, 0.8e18);
        assertEq(uc, 0.8e18);
        assertFalse(controller.borrowGuardianPaused(stockAccount));
        assertFalse(controller.borrowGuardianPaused(usdgAccount));
        assertFalse(controller.seizeGuardianPaused());
        assertEq(controller.borrowCaps(stockAccount), 4.35e18);
        assertEq(controller.borrowCaps(usdgAccount), 1000e6);
        assertEq(sMarket.supplyCap(), 4.35e18);
        assertEq(uMarket.supplyCap(), 1000e6);
        assertEq(sMarket.reserveFactorMantissa(), 0.1e18);
        assertEq(uMarket.reserveFactorMantissa(), 0.1e18);
        assertEq(rate.BASE(), 7610350076);
        assertEq(rate.SLOPE(), 47564687975);
        assertEq(rate.JUMP(), 380517503805);
        assertEq(rate.KINK(), 0.8e18);
        assertEq(rate.getBorrowRate(100, 0, 0), 7610350076);
        assertEq(rate.getBorrowRate(20, 80, 0), 7610350076 + 47564687975 * 8 / 10);
        assertEq(
            rate.getBorrowRate(0, 100, 0), 7610350076 + 47564687975 * 8 / 10 + 380517503805 / 5
        );
    }

    function testEightyPercentLiquidationAndCloseFactor() public {
        supply();
        vm.prank(alice);
        uMarket.borrow(150e6);
        feed.set(90e8); // 10% price decline crosses 80% collateral boundary.
        (,, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertGt(shortfall, 0);
        vm.expectRevert();
        uMarket.liquidateBorrow(alice, 76e6, PToken(stockAccount));
        uint256 before = sMarket.balanceOf(address(this));
        uMarket.liquidateBorrow(alice, 70e6, PToken(stockAccount));
        assertEq(uMarket.borrowBalanceStored(alice), 80e6);
        assertGt(sMarket.balanceOf(address(this)), before);
    }

    function testEightyPercentStaleBoundaryAndBufferConsumption() public {
        supply();
        recenter();
        assertGe(usdg.balanceOf(usdgAccount), uMarket.vaultAccountedAssets());
        vm.prank(alice);
        uMarket.borrow(90e6);
        // Borrowing consumes cash; the 50% target is not a withdrawal guarantee.
        assertLt(usdg.balanceOf(usdgAccount), uMarket.vaultAccountedAssets());
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.prank(alice);
        vm.expectRevert();
        uMarket.borrow(1);
        vm.prank(alice);
        vm.expectRevert();
        sMarket.redeemUnderlying(1);
        vm.expectRevert();
        uMarket.liquidateBorrow(alice, 1, PToken(stockAccount));
        uint256 cash = usdg.balanceOf(usdgAccount);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying(cash + 1);
        vm.prank(bob);
        uMarket.redeemUnderlying(cash);
        vm.prank(alice);
        usdg.approve(usdgAccount, type(uint256).max);
        vm.prank(alice);
        uMarket.repayBorrow(type(uint256).max);
        assertEq(uMarket.totalBorrows(), 0);
        fresh();
        vm.prank(keeper);
        uMarket.rebalanceVault();
        assertGe(usdg.balanceOf(usdgAccount), uMarket.vaultAccountedAssets());
    }

    function testSmallLockedSeedStillBoundsDonationAttack() public {
        sMarket.redeem(sMarket.balanceOf(address(this)));
        assertEq(sMarket.totalSupply(), 1e8);
        assertLe(sMarket.totalManagedAssets(), 2e11 + 1);
        stock.mint(stockAccount, 1e18);
        uint256 before = stock.balanceOf(alice);
        vm.prank(alice);
        sMarket.mint(1e18);
        uint256 shares = sMarket.balanceOf(alice);
        assertGt(shares, 0);
        vm.prank(alice);
        sMarket.redeem(shares);
        assertGe(stock.balanceOf(alice), before - 2e10);
        uMarket.redeem(uMarket.balanceOf(address(this)));
        assertEq(uMarket.totalSupply(), 1e8);
        assertLe(uMarket.totalManagedAssets(), 21); // 20 raw USDG locked, one rounding unit.
    }
}
