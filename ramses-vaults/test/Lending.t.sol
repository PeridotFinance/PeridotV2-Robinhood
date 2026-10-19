// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import {LendingFixture} from "./LendingFixture.sol";
import {RamsesBoostedDelegate} from "../src/lending/RamsesBoostedDelegate.sol";
import {RamsesV3PairedAdapter} from "../src/RamsesV3PairedAdapter.sol";
import {RamsesOracleGuard} from "../src/RamsesOracleGuard.sol";
import {PToken} from "peridot/PToken.sol";
import {PairLedger} from "baseline/src/libraries/VaultTypes.sol";

contract LendingTest is LendingFixture {
    function testBufferBothSidesAndRamsesLP() public {
        supply(); recenter();
        assertGt(adapter.positionState(PAIR).liquidity, 0);
        assertGe(stock.balanceOf(stockAccount), vault.accountedAssets(PAIR, address(stock)));
        assertGe(usdg.balanceOf(usdgAccount), vault.accountedAssets(PAIR, address(usdg)));
        assertEq(sMarket.vaultBufferMantissa(), 0.5e18);
        assertEq(sMarket.decimals(), 8);
    }

    function testStaleCashOnlyExitRollbackAndRefresh() public {
        supply(); recenter();
        vm.prank(bob); uMarket.mint(10e6);
        uint256 cash = usdg.balanceOf(usdgAccount);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        assertEq(uMarket.vaultWithdrawableAssets(), 0);
        assertGt(uMarket.vaultLiquidAssets(), 0);
        assertEq(uMarket.getCash(), cash);
        bytes32 ledgerHash = keccak256(abi.encode(vault.ledger(PAIR)));
        uint256 shares = uMarket.balanceOf(bob);
        vm.prank(bob); vm.expectRevert(); uMarket.redeemUnderlying{gas: 8_000_000}(cash + 1);
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash);
        assertEq(uMarket.balanceOf(bob), shares);
        assertEq(uMarket.cumulativeVaultLoss(), 0);
        vm.prank(bob); uMarket.redeemUnderlying(cash);
        assertEq(usdg.balanceOf(usdgAccount), 0);
        fresh();
        vm.prank(bob); uMarket.redeemUnderlying{gas: 8_000_000}(1e6);
        assertGt(usdg.balanceOf(bob), 890e6);
    }

    function testFreshnessBorrowRedeemCollateralButRepayAllowed() public {
        supply(); enableBorrow();
        vm.prank(alice); uMarket.borrow(10e6);
        vm.prank(alice); usdg.approve(usdgAccount, type(uint256).max);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.prank(alice); vm.expectRevert(); uMarket.borrow(1);
        vm.prank(alice); vm.expectRevert(); sMarket.redeemUnderlying(1);
        vm.prank(alice); uMarket.repayBorrow(type(uint256).max);
        assertEq(uMarket.borrowBalanceStored(alice), 0);
        fresh(); vm.prank(alice); sMarket.redeemUnderlying(1);
    }

    function testBorrowRepayAccrueInterestAndCaps() public {
        supply(); enableBorrow();
        vm.prank(alice); uMarket.borrow(50e6);
        vm.roll(block.number + 10000);
        uMarket.accrueInterest();
        assertGt(uMarket.borrowBalanceStored(alice), 50e6);
        assertGt(uMarket.totalReserves(), 0);
        usdg.mint(alice, 2e6);
        vm.prank(alice); usdg.approve(usdgAccount, type(uint256).max);
        vm.prank(alice); uMarket.repayBorrow(type(uint256).max);
        assertEq(uMarket.totalBorrows(), 0);
        sMarket.setSupplyCap(0);
        vm.prank(alice); vm.expectRevert("SUPPLY_CAP"); sMarket.mint(1e16);
        vm.prank(alice); vm.expectRevert("market borrow cap reached"); uMarket.borrow(100e6);
    }

    function testZeroCollateralLaunchDisablesBorrow() public {
        supply();
        vm.prank(alice); vm.expectRevert(); uMarket.borrow(1);
    }

    function testAllocationPausedSwallowKeepsCashAndNoApproval() public {
        vault.setPairPause(PAIR, true, false, false);
        uint256 claim = vault.accountedAssets(PAIR, address(stock));
        vm.prank(alice); sMarket.mint(1e18);
        assertEq(vault.accountedAssets(PAIR, address(stock)), claim);
        assertEq(stock.allowance(stockAccount, address(vault)), 0);
        assertGe(stock.balanceOf(stockAccount), 1e18);
    }

    function testRoleBufferFloorAndPauseGates() public {
        vm.prank(keeper); vm.expectRevert(RamsesBoostedDelegate.OnlyAdmin.selector); sMarket.setSupplyCap(1);
        vm.prank(keeper); vm.expectRevert(RamsesBoostedDelegate.OnlyAdmin.selector); sMarket.setVaultBuffer(1e18);
        vm.expectRevert(RamsesBoostedDelegate.InvalidBuffer.selector); sMarket.setVaultBuffer(0.5e18 - 1);
        vm.expectRevert(RamsesBoostedDelegate.InvalidBuffer.selector); sMarket.setVaultConfig(address(vault), PAIR, 0.5e18 - 1, keeper);
        vm.prank(alice); vm.expectRevert(RamsesBoostedDelegate.OnlyVaultOperator.selector); sMarket.rebalanceVault();
        sMarket.setVaultPaused(true);
        uint256 claim = sMarket.vaultAccountedAssets();
        vm.prank(alice); sMarket.mint(1e18);
        assertEq(sMarket.vaultAccountedAssets(), claim);
        sMarket.setVaultPaused(false);
        vm.prank(keeper); sMarket.rebalanceVault();
        assertGe(stock.balanceOf(stockAccount), sMarket.vaultAccountedAssets());
        sMarket.setVaultBuffer(0.75e18);
        assertGe(stock.balanceOf(stockAccount) * 1e18, sMarket.totalManagedAssets() * 0.75e18);
    }

    function testLossPreviewAndCheckpointBeforeCashExit() public {
        supply(); recenter();
        uint256 sr = sMarket.exchangeRateStored(); uint256 ur = uMarket.exchangeRateStored();
        uint256 lossBefore = vault.ledger(PAIR).cumulativeLossUSDG;
        shift(500);
        uint256 previewS = sMarket.exchangeRateStored(); uint256 previewU = uMarket.exchangeRateStored();
        assertLt(previewS, sr); assertLt(previewU, ur);
        vm.prank(alice); sMarket.redeemUnderlying(1e16);
        assertLt(sMarket.exchangeRateStored(), sr);
        assertLt(uMarket.exchangeRateStored(), ur);
        assertGt(vault.ledger(PAIR).cumulativeLossUSDG, lossBefore);
        assertApproxEqRel(sMarket.exchangeRateStored() * 1e18 / sr, uMarket.exchangeRateStored() * 1e18 / ur, 0.0001e18);
    }

    function testEmergencyExitWithMarketsHoldingClaims() public {
        supply(); recenter();
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian); vault.emergencyDecrease(PAIR, l, block.timestamp);
        assertTrue(vault.pairConfig(PAIR).emergencyMode);
        vm.prank(alice); sMarket.redeemUnderlying(0.1e18);
        vm.prank(bob); uMarket.redeemUnderlying(10e6);
    }

    function testDirectVaultDonationsIgnoredAndMarketDonationBacked() public {
        supply();
        uint256 beforeRate = sMarket.exchangeRateStored();
        stock.mint(address(vault), 1000e18);
        assertEq(sMarket.exchangeRateStored(), beforeRate);
        uint256 supplyBefore = sMarket.totalSupply();
        stock.mint(stockAccount, 1e18);
        assertEq(sMarket.exchangeRateStored() - beforeRate, 1e36 / supplyBefore);
        assertEq(sMarket.balanceOf(address(0xdead)), 1e8);
    }

    function testFreshMarketRequiresTimelockSeed() public {
        RamsesBoostedDelegate m = market(address(stock), 2e26, "fresh");
        m.setSupplyCap(10e18);
        require(controller._supportMarket(PToken(address(m))) == 0);
        vm.prank(alice); stock.approve(address(m), type(uint256).max);
        vm.prank(alice); vm.expectRevert("TIMELOCK_SEED_REQUIRED"); m.mint(1e18);
        stock.approve(address(m), type(uint256).max);
        m.mint(1e18);
        assertEq(m.balanceOf(address(0xdead)), 1e8);
    }

    function testLiquidationUsingDecimalCorrectOracle() public {
        supply(); enableBorrow();
        vm.prank(alice); uMarket.borrow(50e6);
        feed.set(50e8);
        (,, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertGt(shortfall, 0);
        uint256 beforeShares = sMarket.balanceOf(address(this));
        uint256 beforeDebt = uMarket.borrowBalanceStored(alice);
        uMarket.liquidateBorrow(alice, 20e6, PToken(stockAccount));
        assertEq(uMarket.borrowBalanceStored(alice), beforeDebt - 20e6);
        assertGt(sMarket.balanceOf(address(this)), beforeShares);
        vm.expectRevert(); uMarket.liquidateBorrow(alice, 20e6, PToken(stockAccount)); // above close factor
    }

    function testFeesAndRewardsLiftRatesButRamDoesNot() public {
        supply(); recenter();
        uint256 sr = sMarket.exchangeRateStored(); uint256 ur = uMarket.exchangeRateStored();
        pool.accrueFees(10e6, 0.1e18);
        vm.prank(keeper); vault.collectFees(PAIR, block.timestamp);
        assertGt(sMarket.exchangeRateStored(), sr); assertGt(uMarket.exchangeRateStored(), ur);
        sr = sMarket.exchangeRateStored(); ur = uMarket.exchangeRateStored();
        fundRewards(); vm.warp(vm.getBlockTimestamp() + 1 hours); fresh();
        vm.prank(keeper); vault.harvest(PAIR, block.timestamp);
        assertGt(sMarket.exchangeRateStored(), sr); assertGt(uMarket.exchangeRateStored(), ur);
        assertGt(vault.externalRewards(PAIR, address(ram)), 0);
        sr = sMarket.exchangeRateStored(); ur = uMarket.exchangeRateStored();
        ram.mint(address(vault), 1000e18);
        assertEq(sMarket.exchangeRateStored(), sr); assertEq(uMarket.exchangeRateStored(), ur);
    }

    function testSeedMakesDonationAttackCostlyAndZeroSharesRevert() public {
        // All non-dead seed shares are removed; 1e8 shares still protect the next depositor.
        sMarket.redeem(sMarket.balanceOf(address(this)));
        assertEq(sMarket.totalSupply(), 1e8);
        uint256 before = stock.balanceOf(alice);
        stock.mint(stockAccount, 1e18);
        vm.prank(alice); sMarket.mint(1e18);
        assertGt(sMarket.balanceOf(alice), 0);
        uint256 shares = sMarket.balanceOf(alice);
        vm.prank(alice); sMarket.redeem(shares);
        assertGe(stock.balanceOf(alice), before - 1e11); // rounding bound, donation benefits dead shares
        stock.mint(stockAccount, 100e18);
        sMarket.setSupplyCap(1000e18);
        vm.prank(alice); vm.expectRevert(RamsesBoostedDelegate.ZeroSharesMinted.selector); sMarket.mint(1);
    }

    function testBorrowUsesWithdrawableNotCustody() public {
        supply(); enableBorrow();
        vm.prank(alice); sMarket.mint(3e18);
        PToken[] memory ms = new PToken[](1); ms[0] = PToken(usdgAccount);
        uint256[] memory cap = new uint256[](1); cap[0] = 200e6;
        controller._setMarketBorrowCaps(ms, cap);
        vm.mockCall(address(vault), abi.encodeWithSignature("withdrawableAssets(bytes32,address)", PAIR, address(usdg)), abi.encode(uint256(0)));
        assertGt(uMarket.vaultLiquidAssets(), 0);
        assertEq(uMarket.getCash(), usdg.balanceOf(usdgAccount));
        uint256 amount = usdg.balanceOf(usdgAccount) + 1;
        vm.prank(alice); vm.expectRevert(); uMarket.borrow(amount);
        vm.clearMockedCalls();
        vm.prank(alice); uMarket.borrow(amount);
    }

    function testLendingSourceItselfFailsClosedForBothMarkets() public {
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.expectRevert(); priceAdapter.getUnderlyingPrice(stockAccount);
        vm.expectRevert(); priceAdapter.getUnderlyingPrice(usdgAccount);
    }

    function testUnknownCheckpointFailureCannotMasqueradeAsStale() public {
        supply(); recenter();
        vm.mockCallRevert(address(vault), abi.encodeWithSignature("checkpoint(bytes32,uint256)", PAIR, block.timestamp), abi.encodeWithSignature("UnexpectedFailure()"));
        vm.prank(alice); vm.expectRevert(bytes4(keccak256("UnexpectedFailure()"))); sMarket.redeemUnderlying(1e16);
    }

    function testOperatorSyncDoesNotReportSilentWithdrawSuccess() public {
        supply(); recenter();
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.prank(keeper); vm.expectRevert(RamsesBoostedDelegate.StrategyLiquidityShortfall.selector); uMarket.syncVault{gas: 8000000}(1e6);
        fresh();
        vm.prank(keeper); (uint256 received,) = uMarket.syncVault{gas: 8000000}(1e6);
        assertEq(received, 1e6);
    }

    function testCompositionSurplusCannotInflateMarketRates() public {
        supply(); recenter();
        uint256 sr = sMarket.exchangeRateStored(); uint256 ur = uMarket.exchangeRateStored();
        uint160 ref = guard.referenceSqrtPriceX96(PAIR, false);
        RamsesV3PairedAdapter.PositionState memory p = adapter.positionStateAt(PAIR, ref);
        // Accounting fault injection: large stock surplus, dollar side still uncovered.
        p.stockAmount += 100e18; p.usdgAmount = 0;
        vm.mockCall(address(adapter), abi.encodeWithSelector(adapter.positionStateAt.selector, PAIR, ref), abi.encode(p));
        assertLe(sMarket.exchangeRateStored(), sr + 1);
        assertLe(uMarket.exchangeRateStored(), ur + 1);
        vm.prank(keeper); vault.checkpoint(PAIR, block.timestamp);
        assertLe(sMarket.exchangeRateStored(), sr + 1);
        assertLe(uMarket.exchangeRateStored(), ur + 1);
    }

    function testDollarCollateralStockBorrowAndRepay() public {
        supply(); recenter(); enableBorrow();
        address[] memory ms = new address[](1); ms[0] = usdgAccount;
        vm.prank(bob); controller.enterMarkets(ms);
        vm.prank(bob); sMarket.borrow(0.1e18);
        assertEq(stock.balanceOf(bob), 0.1e18);
        vm.prank(bob); stock.approve(stockAccount, type(uint256).max);
        vm.prank(bob); sMarket.repayBorrow(type(uint256).max);
        assertEq(sMarket.totalBorrows(), 0);
        vm.prank(bob); uMarket.redeemUnderlying(10e6);
    }

    function testFuzzBufferAndIdleSupplierRoundTrip(uint256 raw) public {
        uint256 amount = bound(raw, 1, 400e6);
        uint256 before = usdg.balanceOf(bob);
        vm.prank(bob); uMarket.mint(amount);
        assertGe(usdg.balanceOf(usdgAccount), uMarket.vaultAccountedAssets());
        assertLe(usdg.balanceOf(usdgAccount) - uMarket.vaultAccountedAssets(), 1);
        uint256 shares = uMarket.balanceOf(bob);
        vm.prank(bob); uMarket.redeem(shares);
        assertEq(usdg.balanceOf(bob), before);
    }

    function testProtocolReserveDonationCannotIncreaseLPExposure() public {
        supply(); recenter();
        uint256 claim = vault.accountedAssets(PAIR, address(usdg));
        uMarket._addReserves(1000e6);
        vm.prank(keeper); uMarket.rebalanceVault();
        assertLe(vault.accountedAssets(PAIR, address(usdg)), claim);
        uint256 cash = usdg.balanceOf(usdgAccount);
        assertGe(cash, uMarket.totalReserves());
        assertGe(cash - uMarket.totalReserves(), uMarket.vaultAccountedAssets());
    }

    function testOddRawUnitRoundsBufferUp() public {
        vm.prank(bob); uMarket.mint(1);
        assertEq(usdg.balanceOf(usdgAccount), 20001);
        assertGe(usdg.balanceOf(usdgAccount), uMarket.vaultAccountedAssets());
    }

    function testManualRebalanceCheckpointsBeforeDeposit() public {
        supply(); recenter();
        uint256 lossBefore = vault.ledger(PAIR).cumulativeLossUSDG;
        shift(500);
        stock.mint(stockAccount, 0.1e18);
        vm.prank(keeper); sMarket.rebalanceVault();
        assertGt(vault.ledger(PAIR).cumulativeLossUSDG, lossBefore);
        assertGe(stock.balanceOf(stockAccount), sMarket.vaultAccountedAssets());
    }

    function testUnknownOracleFailureDoesNotBecomeZeroAssets() public {
        supply(); recenter();
        stock.setPaused(true);
        vm.expectRevert(); sMarket.exchangeRateStored();
        vm.prank(alice); vm.expectRevert(); sMarket.redeemUnderlying(1e16);
    }

    function testOracleDecimalsAndIsolation() public view {
        assertEq(priceAdapter.getUnderlyingPrice(stockAccount), 100e18);
        assertEq(priceAdapter.getUnderlyingPrice(usdgAccount), 1e30);
        assertEq(priceAdapter.assetPrices(address(usdg)), 1e18);
        assertTrue(address(controller) != 0x6148183676E304dbe63a85C350c208DA3cEAc39C);
        assertEq(address(sMarket.peridottroller()), address(controller));
        assertEq(address(uMarket.peridottroller()), address(controller));
        assertEq(unitroller.admin(), address(this));
        assertFalse(vault.hasRole(vault.CONFIG_ROLE(), keeper));
    }
}
