// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IsolatedMarginTypes } from "peridot/margin/IsolatedMarginTypes.sol";
import {
    IsolatedMarginConfigUpgradeable
} from "peridot/margin/IsolatedMarginConfigUpgradeable.sol";
import { QueueRaiseMarginCaps, ApplyRaiseMarginCaps } from "../../script/RaiseMarginCaps.s.sol";
import { LendingDelegateMarginCompatForkTest } from "./LendingDelegateMarginCompat.t.sol";

interface IFlashDeposit {
    function depositLiquidity(address token, uint256 amount) external;
}

/// @notice The exact funding and cap-raise procedure on a local fork of live state, then positions
/// at the new size in both directions, closes, an over-cap rejection and liquidations. The funding
/// steps are the ones the governor will run: withdraw margin, redeem, approve, depositLiquidity.
/// Because it inherits the margin suite, the original small-position tests also run at the new caps.
contract RaiseMarginCapsForkTest is LendingDelegateMarginCompatForkTest {
    address constant FLASH = 0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06;
    address constant CONFIG = 0x09F94fe0B79E000c8a26617c63E3427fdECB528b;
    uint256 constant SHARES_TO_REDEEM = 16_100_000_000; // about 3.22 USDG
    uint256 constant NVDA_TO_FLASH = 0.013e18;

    uint256 internal flashUsdgBefore;
    uint256 internal flashStockBefore;

    function setUp() public override {
        super.setUp();
        flashUsdgBefore = usd.balanceOf(FLASH);
        flashStockBefore = stock.balanceOf(FLASH);
        // Give the governor margin to withdraw, as in the live account (free margin in the vault).
        _giveMargin(6e6);

        // 1) Fund the flash vault exactly as the governor will.
        vm.startPrank(GOVERNOR);
        marginVault.withdraw(P_USD, SHARES_TO_REDEEM);
        uint256 usdBefore = usd.balanceOf(GOVERNOR);
        require(pUsd.redeem(SHARES_TO_REDEEM) == 0, "REDEEM");
        uint256 usdgGot = usd.balanceOf(GOVERNOR) - usdBefore;
        usd.approve(FLASH, usdgGot);
        IFlashDeposit(FLASH).depositLiquidity(USD, usdgGot);
        if (stock.balanceOf(GOVERNOR) < NVDA_TO_FLASH) deal(STOCK, GOVERNOR, NVDA_TO_FLASH);
        stock.approve(FLASH, NVDA_TO_FLASH);
        IFlashDeposit(FLASH).depositLiquidity(STOCK, NVDA_TO_FLASH);
        vm.stopPrank();

        // 2) Queue, wait the config delay, apply.
        new QueueRaiseMarginCaps().run();
        vm.warp(block.timestamp + IsolatedMarginConfigUpgradeable(CONFIG).actionDelay());
        vm.roll(block.number + 300);
        new ApplyRaiseMarginCaps().run();
        require(pUsd.accrueInterest() == 0 && pStock.accrueInterest() == 0, "ACCRUE");
        baseUsdBorrows = pUsd.totalBorrows();
        baseStockBorrows = pStock.totalBorrows();
        baseUsdShares = pUsd.totalBorrowShares();
        baseStockShares = pStock.totalBorrowShares();
        baseLocked = marginVault.lockedBalance(GOVERNOR, P_USD);
    }

    function _giveMargin(uint256 usdgAmount) internal {
        deal(USD, GOVERNOR, usd.balanceOf(GOVERNOR) + usdgAmount);
        vm.startPrank(GOVERNOR);
        usd.approve(P_USD, usdgAmount);
        uint256 before = pUsd.balanceOf(GOVERNOR);
        require(pUsd.mint(usdgAmount) == 0, "MINT");
        uint256 shares = pUsd.balanceOf(GOVERNOR) - before;
        pUsd.approve(address(marginVault), shares);
        marginVault.deposit(P_USD, shares);
        vm.stopPrank();
    }

    function _risk(bool short) internal view returns (IsolatedMarginTypes.PairRiskConfig memory) {
        return IsolatedMarginConfigUpgradeable(CONFIG)
            .getPairRisk(P_USD, short ? P_USD : P_STOCK, short ? P_STOCK : P_USD);
    }

    function testCapsAreRaisedAndOnlyTheCapsChanged() public view {
        for (uint256 i; i < 2; ++i) {
            IsolatedMarginTypes.PairRiskConfig memory r = _risk(i == 1);
            assertEq(r.maxPositionValueUsd, 5e18);
            assertEq(r.maxDebtValueUsd, 4e18);
            assertEq(r.maxLeverageX100, 500);
            assertEq(r.initialMarginBps, 2000);
            assertEq(r.maintenanceMarginBps, 1000);
            assertEq(r.maxSlippageBps, 100);
            assertTrue(r.enabled);
        }
        assertGt(usd.balanceOf(FLASH), flashUsdgBefore + 3e6);
        assertGt(stock.balanceOf(FLASH), flashStockBefore + 0.012e18);
    }

    function testOneDollarMarginRoundTripBothDirections() public {
        for (uint256 d; d < 2; ++d) {
            bool short = d == 1;
            uint256 id = _open(short, 1e6);
            emit log_named_uint(
                short ? "short entry debt (USD18)" : "long entry debt (USD18)",
                riskEngine.getMetrics(_account(id)).debtValueUsd
            );
            assertGt(riskEngine.getMetrics(_account(id)).debtValueUsd, 3.5e18, "debt is about $4");
            uint256 returned = _closeAndWithdraw(id);
            assertGt(returned, 1e6 * 90 / 100);
        }
    }

    function testOverTheNewCapStillRejected() public {
        vm.expectRevert();
        this.openForTest(false, 1.3e6); // 5x debt would be about $5.2 > $4
    }

    function openForTest(bool short, uint256 amount) external returns (uint256) {
        return _open(short, amount);
    }

    function testOneDollarLongStaysLiquidatableWithTheFundedFlashVault() public {
        uint256 id = _open(false, 1e6);
        _shock(6000);
        _liquidate(id);
    }

    /// A normal liquidation trigger for a 5x short (a bit past the first liquidatable price).
    function testOneDollarShortLiquidatesAfterAnEighteenPercentRise() public {
        uint256 id = _open(true, 1e6);
        _shock(11800);
        _liquidate(id);
    }

    /// A violent gap. Reported, not hidden: if the shortfall exceeds the insurance fund the
    /// liquidation cannot repay the flash loan (LiquidationError 22) and lenders bear the rest.
    function testOneDollarShortAfterAFortyPercentGap() public {
        uint256 id = _open(true, 1e6);
        _shock(14000);
        _liquidate(id);
    }
}
