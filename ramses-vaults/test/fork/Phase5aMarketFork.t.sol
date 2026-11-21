// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { LendingForkTest } from "./LendingFork.t.sol";
import { RamsesV3PairedAdapter } from "../../src/RamsesV3PairedAdapter.sol";
import { IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { RamsesVaultAdapter } from "../../src/RamsesVaultAdapter.sol";
import { PToken } from "peridot/PToken.sol";
import { IRewardRecovery } from "../mocks/IRewardRecovery.sol";

contract Phase5aMarketForkTest is LendingForkTest {
    function _removalEdge(bool down) internal {
        uint160 ref = guard.referenceSqrtPriceX96(PAIR, false);
        uint160 edge = uint160(
            Math.mulDiv(ref, Math.sqrt(Math.mulDiv(10000, 1e36, down ? 9401 : 10599)), 1e18)
        );
        trader.move(!down, edge);
        guard.referenceSqrtPriceX96(PAIR, false);
        RamsesV3PairedAdapter.PositionState memory p = adapter.positionState(PAIR);
        vault.setAllowRewardForfeiture(PAIR, true);
        vm.prank(address(vault));
        (uint256 s, uint256 u,) = adapter.decreaseLiquidity(PAIR, p.liquidity, ref, block.timestamp);
        assertGe(s + 2, p.stockAmount);
        assertGe(u + 2, p.usdgAmount);
    }

    function testPhase5aForkRemovalLowerGateEdge() public {
        _removalEdge(true);
    }

    function testPhase5aForkRemovalUpperGateEdge() public {
        _removalEdge(false);
    }

    function testPhase5aForkRecenterBrokenRamWithForfeiture() public {
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _shift(700);
        vm.mockCallRevert(RAM, abi.encodeWithSelector(IERC20.balanceOf.selector), "RAM unavailable");
        vault.setAllowRewardForfeiture(PAIR, true);
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
        assertGt(adapter.positionState(PAIR).liquidity, 0);
    }

    function testPhase5aForkGuardIdentityAndGaugeMismatch() public {
        RamsesVaultAdapter a = new RamsesVaultAdapter(address(this), pm, guard);
        vm.mockCall(
            address(guard), abi.encodeWithSignature("poolOf(bytes32)", PAIR), abi.encode(address(9))
        );
        vm.expectRevert(RamsesV3PairedAdapter.InvalidConfiguration.selector);
        a.registerPair(
            PAIR,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock), address(usdg), pool, gauge, 400, 300
            )
        );
        vm.clearMockedCalls();
        vm.mockCall(address(gauge), abi.encodeWithSignature("pool()"), abi.encode(address(9)));
        vm.expectRevert(RamsesV3PairedAdapter.InvalidConfiguration.selector);
        a.registerPair(
            PAIR,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock), address(usdg), pool, gauge, 400, 300
            )
        );
    }

    function testPhase5aForkDepositCheckpointBeforePairChange() public {
        _shift(500);
        uint256 beforeLoss = vault.ledger(PAIR).cumulativeLossUSDG;
        // Compare against the same real checkpoint without a deposit: it collects swap fees
        // and may use their reserve share, both deliberately absent from the live loss preview.
        uint256 snapshot = vm.snapshotState();
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        uint256 preview = sMarket.exchangeRateStored();
        assertTrue(vm.revertToState(snapshot));
        vm.prank(bob);
        uMarket.mint(20e6);
        assertGt(vault.ledger(PAIR).cumulativeLossUSDG, beforeLoss);
        assertApproxEqAbs(sMarket.exchangeRateStored(), preview, 1e20);
        vm.prank(bob);
        uMarket.redeemUnderlying(120e6);
        assertLe(sMarket.exchangeRateStored(), preview + 1e20);
    }

    function testPhase5aForkBorrowAtLimitRespectsWithdrawableCash() public {
        _enableBorrow();
        PToken[] memory ms = new PToken[](1);
        ms[0] = PToken(address(uMarket));
        uint256[] memory caps = new uint256[](1);
        caps[0] = 200e6;
        controller._setMarketBorrowCaps(ms, caps);
        (, int24 tick,,,,,) = pool.slot0();
        trader.move(false, TickMath.getSqrtPriceAtTick(tick + 10));
        // Book fees before sizing the exact-limit borrow; withdrawal execution is still ahead.
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        (, uint256 liquidity,) = controller.getAccountLiquidity(alice);
        uint256 amount = liquidity / 1e12;
        assertGt(amount, usdg.balanceOf(address(uMarket)));
        vault.setAllowRewardForfeiture(PAIR, true);
        vm.prank(alice);
        vm.expectRevert(bytes4(keccak256("BorrowCashNotAvailable()")));
        uMarket.borrow(amount);
        assertEq(uMarket.totalBorrows(), 0);
        // Supply idle on the debt side: the same limit borrow can now withdraw from the vault.
        vm.prank(bob);
        uMarket.mint(100e6);
        (, liquidity,) = controller.getAccountLiquidity(alice);
        amount = liquidity / 1e12;
        assertGt(amount, usdg.balanceOf(address(uMarket)));
        assertLe(amount, uMarket.getCash());
        uint256 beforeClaim = vault.accountedAssets(PAIR, address(usdg));
        vm.prank(alice);
        uMarket.borrow(amount);
        assertEq(uMarket.borrowBalanceStored(alice), amount);
        assertLt(vault.accountedAssets(PAIR, address(usdg)), beforeClaim);
    }

    function testPhase5aForkUnacceptableSwapQuoteRollsBack() public {
        uint256 beforeStock = stock.balanceOf(address(vault));
        uint256 beforeUsdg = usdg.balanceOf(address(vault));
        (uint160 beforePrice,,,,,,) = pool.slot0();
        vm.prank(address(vault));
        stock.approve(address(adapter), type(uint256).max);
        vm.prank(address(vault));
        vm.expectRevert(bytes4(keccak256("BalanceDeltaMismatch()")));
        adapter.swapExactInput(PAIR, address(stock), beforeStock / 2, 1e30, block.timestamp);
        assertEq(stock.balanceOf(address(vault)), beforeStock);
        assertEq(usdg.balanceOf(address(vault)), beforeUsdg);
        (uint160 afterPrice,,,,,,) = pool.slot0();
        assertEq(afterPrice, beforePrice);
    }

    function testPhase5aForkWeekendCashLimit() public {
        _enableBorrow();
        PToken[] memory ms = new PToken[](1);
        ms[0] = PToken(address(uMarket));
        uint256[] memory caps = new uint256[](1);
        caps[0] = 200e6;
        controller._setMarketBorrowCaps(ms, caps);
        uint256 cash = usdg.balanceOf(address(uMarket));
        vm.prank(alice);
        uMarket.borrow(cash);
        vm.warp(vm.getBlockTimestamp() + 60 hours);
        vm.roll(block.number + 18000);
        assertEq(uMarket.getCash(), 0);
        assertEq(rate.utilization(0, uMarket.totalBorrows(), uMarket.totalReserves()), 1e18);
        assertLe(uMarket.borrowRatePerBlock(), 5e12);
        uMarket.accrueInterest();
        uint256 debt = uMarket.borrowBalanceStored(alice);
        emit log_named_uint("60h debt USDG raw", debt);
        assertApproxEqAbs(debt, cash + cash * rate.getBorrowRate(0, 1e18, 0) * 18000 / 1e18, 1);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying(1);
        vm.expectRevert();
        uMarket.liquidateBorrow(alice, 1e6, PToken(address(sMarket)));
        deal(address(usdg), alice, debt);
        vm.prank(alice);
        usdg.approve(address(uMarket), type(uint256).max);
        vm.prank(alice);
        uMarket.repayBorrow(type(uint256).max);
        vm.prank(bob);
        uMarket.redeemUnderlying(1e6);
        assertEq(uMarket.totalBorrows(), 0);
    }
}
