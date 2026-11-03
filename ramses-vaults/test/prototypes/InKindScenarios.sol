// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { RoutedVault, InKindExtension } from "./RoutedVault.sol";
import { InKindRedeemer } from "./InKindRedeemer.sol";

/// @notice Shared by the local fixture and the SPY and NVDA mainnet forks. Real vault, adapter, markets, controller and reserve code; real token transfers.
abstract contract InKindScenarios is Test {
    InKindRedeemer internal redeemer;
    address internal bridgeAccount = address(0xb51d6e);
    address internal carol = address(0xca401);

    function _v() internal view virtual returns (RamsesBoostedVault);
    function _g() internal view virtual returns (RamsesOracleGuard);
    function _r() internal view virtual returns (RamsesLossReserve);
    function _sm() internal view virtual returns (RamsesBoostedDelegate);
    function _um() internal view virtual returns (RamsesBoostedDelegate);
    function _st() internal view virtual returns (IERC20);
    function _us() internal view virtual returns (IERC20);
    function _pair() internal view virtual returns (bytes32);
    function _keeper() internal view virtual returns (address);
    function _alice() internal view virtual returns (address);
    function _bob() internal view virtual returns (address);
    function _ready() internal virtual;
    function _enableBorrowing() internal virtual;
    function _stockFalls(int24 ticks) internal virtual; // moves the pool and the (simulated, on a fork) feed
    function _refreshFeed() internal virtual;
    function _poolOnly(int24 ticks) internal virtual; // moves the pool only: the feed stays

    function _routed() internal returns (RamsesBoostedVault) {
        return RamsesBoostedVault(payable(address(new RoutedVault(address(new RamsesBoostedVault()), address(new InKindExtension())))));
    }

    function _setUpInKind() internal {
        _ready();
        redeemer = new InKindRedeemer(_v(), _pair(), _sm(), _um(), bridgeAccount);
        _v().grantRole(keccak256("IN_KIND_OPERATOR_ROLE"), address(redeemer));
        _v().grantRole(_v().KEEPER_ROLE(), address(redeemer));
        deal(address(_us()), bridgeAccount, 1_000e6);
        deal(address(_st()), bridgeAccount, 100e18);
        vm.startPrank(bridgeAccount);
        _us().approve(address(redeemer), type(uint256).max);
        _st().approve(address(redeemer), type(uint256).max);
        vm.stopPrank();
        vm.prank(_bob());
        _um().approve(address(redeemer), type(uint256).max);
        vm.prank(_alice());
        _sm().approve(address(redeemer), type(uint256).max);
    }

    function _addCarol() internal {
        deal(address(_us()), carol, 200e6);
        vm.startPrank(carol);
        _us().approve(address(_um()), type(uint256).max);
        _um().mint(100e6);
        _um().approve(address(redeemer), type(uint256).max);
        vm.stopPrank();
    }

    /// @dev Swaps paused; price shocked until the position holds only the stock; a keeper recentre moves it to idle. USDG is scarce, stock surplus is idle.
    function _oneSided() internal {
        _v().setPairPause(_pair(), false, true, false);
        _stockFalls(3000);
        vm.warp(vm.getBlockTimestamp() + 1 hours + 1);
        _refreshFeed();
        vm.prank(_keeper());
        _v().recenter(_pair(), vm.getBlockTimestamp());
    }

    function _usdgGap() internal view returns (uint256 available, uint256 principal) {
        PairLedger memory l = _v().ledger(_pair());
        return (l.usdgIdle + _v().liquidityAdapter().positionState(_pair()).usdgAmount, l.usdgPrincipal);
    }

    function testDebtFreeMixedRedemptionPaysTheFullValueWhereThePlainRedeemReverts() public {
        _setUpInKind();
        _oneSided();
        address bob = _bob();
        (uint256 avail, uint256 principal) = _usdgGap();
        assertLt(avail, principal, "USDG is scarce");
        assertEq(_r().available(_pair(), address(_us())), 0, "reserve exhausted");
        uint256 shares = _um().balanceOf(bob);

        // the plain exit fails atomically
        vm.prank(bob);
        vm.expectRevert();
        _um().redeem{ gas: 12_000_000 }(shares);
        assertEq(_um().balanceOf(bob), shares);

        (uint256 ps,) = _g().pricesUSD18(_pair());
        uint256 bobUsdg = _us().balanceOf(bob);
        uint256 supplyBefore = _um().totalSupply();
        uint256 bridgeBefore = _us().balanceOf(bridgeAccount);
        uint256 reserveBefore = _r().accountedBalance(address(_us()));
        uint256 lossBefore = _v().ledger(_pair()).cumulativeLossUSDG;
        uint256 stockPrincipal = _v().ledger(_pair()).stockPrincipal;

        vm.prank(bob);
        (uint256 own, uint256 other) = redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
        uint256 value = shares * _um().exchangeRateStored() / 1e18;
        uint256 paidUsd = own * 1e12 + other * ps / 1e18;
        emit log_named_uint("redeemed value (raw USDG)", value);
        emit log_named_uint("paid in USDG (raw)", own);
        emit log_named_uint("paid in stock (wei)", other);
        emit log_named_uint("paid, valued at the oracle (USD 1e18)", paidUsd);

        assertEq(_um().balanceOf(bob), 0, "all shares taken");
        assertEq(_um().balanceOf(address(redeemer)), 0, "and burned by the market");
        assertEq(supplyBefore - _um().totalSupply(), shares, "total supply fell by exactly the shares");
        assertEq(_us().balanceOf(bob) - bobUsdg, own, "real USDG received");
        assertEq(_st().balanceOf(bob), other, "real stock received");
        assertGt(other, 0, "a mixture");
        assertApproxEqRel(paidUsd, value * 1e12, 0.001e18, "paid the value at the oracle price, within 0.1%");
        assertEq(_us().balanceOf(bridgeAccount), bridgeBefore, "bridge repaid in full, same transaction");
        assertEq(_r().accountedBalance(address(_us())), reserveBefore, "no reserve used");
        assertEq(_v().ledger(_pair()).cumulativeLossUSDG, lossBefore, "no new loss recognized by the exchange");
        assertEq(_v().ledger(_pair()).stockPrincipal, stockPrincipal, "stock principal untouched");
        PairLedger memory l = _v().ledger(_pair());
        assertGe(l.stockIdle + _v().liquidityAdapter().positionState(_pair()).stockAmount, l.stockPrincipal, "stock side still fully backed");
    }

    function testExitOrderDoesNotChangeTheMixAndRemainingHoldersKeepTheirValue() public {
        _setUpInKind();
        _addCarol();
        _oneSided();
        address bob = _bob();
        uint256 half = _um().balanceOf(bob) / 2;
        uint256 alicePs = _sm().exchangeRateCurrent();
        uint256 snap = vm.snapshotState();

        uint256 rate0 = _um().exchangeRateCurrent();
        vm.prank(bob);
        (uint256 u1, uint256 s1) = redeemer.redeemInKind{ gas: 15_000_000 }(true, half, 0, 0, vm.getBlockTimestamp());
        vm.prank(carol);
        uint256 carolShares = _um().balanceOf(carol);
        vm.prank(carol);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, carolShares, 0, 0, vm.getBlockTimestamp());
        uint256 rateAfter = _um().exchangeRateStored();
        assertGe(rateAfter * 1000, rate0 * 999, "the remaining holder's exchange rate is preserved within 0.1%");
        assertLe(rate0 * 999, rateAfter * 1000);
        (uint256 availA, uint256 prinA) = _usdgGap();
        vm.revertToState(snap);

        vm.prank(carol);
        uint256 cs = _um().balanceOf(carol);
        vm.prank(carol);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, cs, 0, 0, vm.getBlockTimestamp());
        vm.prank(bob);
        (uint256 u2, uint256 s2) = redeemer.redeemInKind{ gas: 15_000_000 }(true, half, 0, 0, vm.getBlockTimestamp());
        (uint256 availB, uint256 prinB) = _usdgGap();
        emit log_named_uint("bob USDG, first / second", u1);
        emit log_named_uint("bob USDG if second", u2);
        emit log_named_uint("bob stock, first", s1);
        emit log_named_uint("bob stock if second", s2);
        assertApproxEqRel(u1 + 1, u2 + 1, 0.005e18, "same own-token part whichever order, within 0.5%");
        assertApproxEqRel(s1 + 1, s2 + 1, 0.005e18, "same other-token part whichever order, within 0.5%");
        assertApproxEqRel(prinA, prinB, 0.001e18, "same remaining principal");
        assertApproxEqAbs(availA, availB, 2e4, "same remaining scarce-token balance within 0.02 USDG");
        assertApproxEqRel(_sm().exchangeRateCurrent(), alicePs, 0.001e18, "the stock side's rate is not charged");
    }

    function testIndebtedAccountsAreRefusedAndKeepTheCollateralCheck() public {
        _setUpInKind();
        _enableBorrowing();
        address alice = _alice();
        vm.prank(alice);
        _um().borrow(40e6);
        uint256 shares = _sm().balanceOf(alice);
        vm.prank(alice);
        vm.expectRevert(InKindRedeemer.HasDebt.selector);
        redeemer.redeemInKind(false, shares, 0, 0, vm.getBlockTimestamp());
        // the ordinary path still applies the controller's collateral check
        vm.prank(alice);
        vm.expectRevert();
        _sm().redeem{ gas: 12_000_000 }(shares);
        assertEq(_sm().balanceOf(alice), shares);
    }

    function testStaleFeedRefusesTheInKindExitAndWithinCashStillWorks() public {
        _setUpInKind();
        _oneSided();
        address bob = _bob();
        uint256 shares = _um().balanceOf(bob);
        uint256 cash = _us().balanceOf(address(_um()));
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        bytes32 h = keccak256(abi.encode(_v().ledger(_pair())));
        vm.prank(bob);
        vm.expectRevert();
        redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
        assertEq(_um().balanceOf(bob), shares, "no stale price made the exit pass");
        assertEq(keccak256(abi.encode(_v().ledger(_pair()))), h);
        vm.prank(bob);
        _um().redeemUnderlying{ gas: 12_000_000 }(cash / 2);
    }

    function testPoolBeyondTheRemovalBoundRefusesTheExchange() public {
        _setUpInKind();
        _oneSided();
        address bob = _bob();
        uint256 shares = _um().balanceOf(bob);
        _poolOnly(1000); // about 10%: beyond the 6% removal bound, the feed unchanged
        vm.prank(bob);
        vm.expectRevert();
        redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
        assertEq(_um().balanceOf(bob), shares);
    }

    function testExchangeCannotExceedTheDeficitOrBeCalledByAnyoneElse() public {
        _setUpInKind();
        _oneSided();
        InKindExtension x = InKindExtension(address(_v()));
        vm.expectRevert(InKindExtension.NotOperator.selector);
        x.exchangeSurplus(_pair(), true, 1);
        (uint256 avail, uint256 principal) = _usdgGap();
        uint256 deficit = principal - avail;
        vm.startPrank(address(redeemer));
        vm.expectPartialRevert(InKindExtension.ExceedsDeficit.selector);
        x.exchangeSurplus(_pair(), true, deficit + 1);
        vm.stopPrank();
    }

    /// @dev Direct call, without the redeemer's own checkpoint in front of it: the exchange primitive itself enforces the removal bound and freshness.
    function testExchangeItselfEnforcesTheRemovalBoundAndFreshness() public {
        _setUpInKind();
        _oneSided();
        InKindExtension x = InKindExtension(address(_v()));
        deal(address(_us()), address(redeemer), 10e6);
        vm.prank(address(redeemer));
        _us().approve(address(_v()), type(uint256).max);
        uint256 snap = vm.snapshotState();
        vm.prank(address(redeemer));
        assertGt(x.exchangeSurplus(_pair(), true, 1e6), 0, "control: admitted when fresh and within the bound");
        vm.revertToState(snap);
        _poolOnly(1000);
        vm.prank(address(redeemer));
        vm.expectRevert();
        x.exchangeSurplus(_pair(), true, 1e6);
        vm.revertToState(snap);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.prank(address(redeemer));
        vm.expectRevert();
        x.exchangeSurplus(_pair(), true, 1e6);
    }

    function testLedgerSlotMatchesTheVaultGetter() public {
        _setUpInKind();
        bytes32 base = keccak256(abi.encode(_pair(), uint256(4)));
        PairLedger memory l = _v().ledger(_pair());
        assertEq(uint256(vm.load(address(_v()), base)), l.stockPrincipal);
        assertEq(uint256(vm.load(address(_v()), bytes32(uint256(base) + 2))), l.stockIdle);
        assertEq(uint256(vm.load(address(_v()), bytes32(uint256(base) + 3))), l.usdgIdle);
    }
}
