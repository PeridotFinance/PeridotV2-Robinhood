// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { Vm } from "forge-std/Vm.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { RoutedVault, InKindExtension } from "../../src/inkind/RoutedVault.sol";
import { InKindRedeemer } from "../../src/inkind/InKindRedeemer.sol";

/// @notice Shared by the local fixture and the SPY and NVDA mainnet forks. Real vault, adapter, markets, controller and reserve code; real token transfers.
abstract contract InKindScenarios is Test {
    InKindRedeemer internal redeemer;
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
        return RamsesBoostedVault(
            payable(address(
                    new RoutedVault(
                        address(new RamsesBoostedVault()), address(new InKindExtension())
                    )
                ))
        );
    }

    function _setUpInKind() internal {
        _ready();
        redeemer = new InKindRedeemer(_v(), _pair(), _sm(), _um()); // self-funded only; production has no float
        _authorize(redeemer);
        InKindExtension(address(_v())).setInKindLimit(_pair(), 1_000e18);
        vm.prank(_bob());
        _um().approve(address(redeemer), type(uint256).max);
        vm.prank(_alice());
        _sm().approve(address(redeemer), type(uint256).max);
    }

    function _authorize(InKindRedeemer who) internal {
        _v().grantRole(keccak256("IN_KIND_OPERATOR_ROLE"), address(who));
        _v().grantRole(_v().KEEPER_ROLE(), address(_v())); // the vault on itself, reachable only through inKindCheckpoint
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
        return
            (
                l.usdgIdle + _v().liquidityAdapter().positionState(_pair()).usdgAmount,
                l.usdgPrincipal
            );
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
        uint256 reserveBefore = _r().accountedBalance(address(_us()));
        uint256 lossBefore = _v().ledger(_pair()).cumulativeLossUSDG;
        uint256 stockPrincipal = _v().ledger(_pair()).stockPrincipal;
        // the value owed, from the same state the redeemer sees: a checkpoint first, then the current rate
        uint256 snap = vm.snapshotState();
        vm.prank(address(redeemer));
        InKindExtension(address(_v())).inKindCheckpoint(_pair(), vm.getBlockTimestamp());
        uint256 rate0 = _um().exchangeRateCurrent();
        vm.revertToState(snap);
        uint256 value = shares * rate0 / 1e18;

        vm.prank(bob);
        (uint256 own, uint256 other) =
            redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
        uint256 paidUsd = own * 1e12 + other * ps / 1e18;
        emit log_named_uint("value owed (USD 1e18)", value * 1e12);
        emit log_named_uint("paid in USDG (raw)", own);
        emit log_named_uint("paid in stock (wei)", other);
        emit log_named_uint("paid, valued at the oracle (USD 1e18)", paidUsd);
        emit log_named_int("paid minus owed (USD 1e18)", int256(paidUsd) - int256(value * 1e12));

        assertEq(_um().balanceOf(bob), 0, "all shares taken");
        assertEq(_um().balanceOf(address(redeemer)), 0, "and burned by the market");
        assertEq(
            supplyBefore - _um().totalSupply(), shares, "total supply fell by exactly the shares"
        );
        assertEq(_us().balanceOf(bob) - bobUsdg, own, "real USDG received");
        assertEq(_st().balanceOf(bob), other, "real stock received");
        assertGt(other, 0, "a mixture");
        assertEq(_us().balanceOf(address(redeemer)), 0, "the redeemer keeps no USDG");
        assertEq(_st().balanceOf(address(redeemer)), 0, "and no stock");
        // Explained bound: the exiter is never paid above the value owed, and below it only by rounding:
        //   2 raw USDG (two redeem floors) + 1 raw USDG (the ceil on the exchanged amount and first shares) + one stock wei at the oracle (the out-amount floor)
        //   + what the two-step redeem loses to rate truncation (1 raw per step).
        uint256 dust = 5e12 + ps / 1e18 + 1;
        assertLe(paidUsd, value * 1e12 + dust, "not above the value owed beyond rounding");
        assertGe(paidUsd + dust, value * 1e12, "not below it beyond rounding");
        assertEq(_r().accountedBalance(address(_us())), reserveBefore, "no reserve used");
        assertEq(
            _v().ledger(_pair()).cumulativeLossUSDG,
            lossBefore,
            "no new loss recognized by the exchange"
        );
        assertEq(_v().ledger(_pair()).stockPrincipal, stockPrincipal, "stock principal untouched");
        PairLedger memory l = _v().ledger(_pair());
        assertGe(
            l.stockIdle + _v().liquidityAdapter().positionState(_pair()).stockAmount,
            l.stockPrincipal,
            "stock side still fully backed"
        );
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
        (uint256 u1, uint256 s1) =
            redeemer.redeemInKind{ gas: 15_000_000 }(true, half, 0, 0, vm.getBlockTimestamp());
        vm.prank(carol);
        uint256 carolShares = _um().balanceOf(carol);
        vm.prank(carol);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, carolShares, 0, 0, vm.getBlockTimestamp());
        uint256 rateAfter = _um().exchangeRateStored();
        emit log_named_uint("rate before", rate0);
        emit log_named_uint("rate after", rateAfter);
        // Explained: rounding on each redeem leaves dust WITH the remaining holders, so the rate may only rise, by at most (raw units of dust x 1e18 / remaining supply).
        assertGe(rateAfter, rate0, "the remaining holders' rate never falls");
        uint256 remainingSupply = _um().totalSupply();
        assertLe(
            rateAfter - rate0,
            20 * 1e18 / remainingSupply + 1,
            "and rises only by the rounding dust (20 raw USDG at most)"
        );
        (uint256 availA, uint256 prinA) = _usdgGap();
        vm.revertToState(snap);

        vm.prank(carol);
        uint256 cs = _um().balanceOf(carol);
        vm.prank(carol);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, cs, 0, 0, vm.getBlockTimestamp());
        vm.prank(bob);
        (uint256 u2, uint256 s2) =
            redeemer.redeemInKind{ gas: 15_000_000 }(true, half, 0, 0, vm.getBlockTimestamp());
        (uint256 availB, uint256 prinB) = _usdgGap();
        emit log_named_uint("bob USDG, first / second", u1);
        emit log_named_uint("bob USDG if second", u2);
        emit log_named_uint("bob stock, first", s1);
        emit log_named_uint("bob stock if second", s2);
        (uint256 psx,) = _g().pricesUSD18(_pair());
        assertApproxEqAbs(
            u1,
            u2,
            4,
            "own-token part is order-independent to 4 raw units (each exit has two floors and one ceil)"
        );
        assertApproxEqAbs(
            s1,
            s2,
            4 * 1e12 * 1e18 / psx + 2,
            "the other-token part differs only by those USDG units at the oracle price"
        );
        assertApproxEqAbs(prinA, prinB, 8, "same remaining principal to rounding");
        assertApproxEqAbs(availA, availB, 8, "same remaining scarce-token balance to rounding");
        assertGe(_sm().exchangeRateCurrent(), alicePs, "the stock side's rate is not charged");
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
        assertGt(
            x.exchangeSurplus(_pair(), true, 1e6),
            0,
            "control: admitted when fresh and within the bound"
        );
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

    /// @dev The share of the deficit must be computed after any pending loss is written down, so the recognition has to come first and always.
    function testLossIsRecognizedBeforeTheExchange() public {
        _setUpInKind();
        _oneSided();
        address bob = _bob();
        uint256 shares = _um().balanceOf(bob);
        vm.recordLogs();
        vm.prank(bob);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 cp =
            keccak256("PairCheckpoint(bytes32,uint256,uint256,uint256,int256,uint256,uint256)");
        bytes32 tr = keccak256("Transfer(address,address,uint256)");
        uint256 firstCheckpoint = type(uint256).max;
        uint256 sharesTaken = type(uint256).max;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == cp && firstCheckpoint == type(uint256).max) {
                firstCheckpoint = i;
            }
            if (
                logs[i].topics[0] == tr && logs[i].emitter == address(_um())
                    && sharesTaken == type(uint256).max
                    && address(uint160(uint256(logs[i].topics[1]))) == bob
            ) sharesTaken = i;
        }
        assertLt(sharesTaken, type(uint256).max, "shares were taken");
        assertLt(
            firstCheckpoint,
            sharesTaken,
            "the loss is written down before the share of the deficit is computed"
        );
    }

    function testRedeemerHoldsNoKeeperPower() public {
        _setUpInKind();
        assertFalse(_v().hasRole(_v().KEEPER_ROLE(), address(redeemer)));
        vm.startPrank(address(redeemer));
        vm.expectRevert();
        _v().rebalance(_pair(), vm.getBlockTimestamp());
        vm.expectRevert();
        _v().recenter(_pair(), vm.getBlockTimestamp());
        vm.expectRevert();
        _v().collectFees(_pair(), vm.getBlockTimestamp());
        vm.expectRevert();
        _v().checkpoint(_pair(), vm.getBlockTimestamp());
        vm.stopPrank();
        vm.expectRevert(InKindExtension.NotOperator.selector);
        InKindExtension(address(_v())).inKindCheckpoint(_pair(), vm.getBlockTimestamp());
    }

    /// @dev Exit funds itself from market cash; no outside account participates.
    function testExitIsSelfFundedWhenThereIsNoBridge() public {
        _setUpInKind();
        _oneSided();
        uint256 shares = _um().balanceOf(_bob());
        vm.prank(_bob());
        (uint256 own, uint256 other) =
            redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
        assertGt(own, 0);
        assertGt(other, 0);
    }

    /// @dev Many small exits: no extra value through splitting, totals recorded, daily limit binds across calls.
    function testRepeatedSmallExitsCannotAccumulateUnnoticed() public {
        _setUpInKind();
        _addCarol();
        _oneSided();
        address bob = _bob();
        InKindExtension x = InKindExtension(address(_v()));
        (uint256 ps,) = _g().pricesUSD18(_pair());
        uint256 total = _um().balanceOf(bob);
        uint256 n = 25;
        uint256 slice = total / n;
        uint256 snap = vm.snapshotState();
        vm.prank(bob);
        (uint256 u1, uint256 s1) =
            redeemer.redeemInKind{ gas: 15_000_000 }(true, slice * n, 0, 0, vm.getBlockTimestamp());
        uint256 oneShotUsd = u1 * 1e12 + s1 * ps / 1e18;
        vm.revertToState(snap);

        uint256 rate0 = _um().exchangeRateCurrent();
        uint256 sumU;
        uint256 sumS;
        for (uint256 i; i < n; i++) {
            vm.prank(bob);
            (uint256 u, uint256 s) =
                redeemer.redeemInKind{ gas: 15_000_000 }(true, slice, 0, 0, vm.getBlockTimestamp());
            sumU += u;
            sumS += s;
        }
        uint256 splitUsd = sumU * 1e12 + sumS * ps / 1e18;
        emit log_named_uint("one exit, paid (USD 1e18)", oneShotUsd);
        emit log_named_uint("25 exits, paid (USD 1e18)", splitUsd);
        InKindExtension.Stats memory st = x.inKindStats(_pair());
        assertEq(st.calls, n, "every exchange is counted");
        assertEq(
            st.lifetimeUsdgIn * 1e12, st.lifetimeUsd, "lifetime value equals the USDG exchanged"
        );
        assertEq(st.lifetimeStockOut, sumS, "lifetime stock out equals what was paid");
        // Splitting cannot beat one exit beyond per-call rounding, which is bounded; dust stays with the remaining holders.
        uint256 perCall = 5e12 + ps / 1e18 + 1;
        assertLe(
            splitUsd,
            oneShotUsd + n * perCall,
            "splitting earns nothing beyond the bounded rounding"
        );
        assertGe(splitUsd + n * perCall, oneShotUsd, "and loses only that");
        assertGe(
            _um().exchangeRateCurrent() + 1,
            rate0,
            "remaining holders are not charged by the repetitions"
        );
        // the daily limit binds across calls: a low limit stops the next exchange even though each call is small
        InKindExtension(address(_v())).setInKindLimit(_pair(), st.usedTodayUsd);
        uint256 carolShares = _um().balanceOf(carol);
        vm.prank(carol);
        vm.expectPartialRevert(InKindExtension.InKindLimitExceeded.selector);
        redeemer.redeemInKind{ gas: 15_000_000 }(
            true, carolShares / 10, 0, 0, vm.getBlockTimestamp()
        );
        // a new UTC day resets it, and only CONFIG_ROLE can set it
        vm.prank(carol);
        vm.expectRevert(InKindExtension.NotConfig.selector);
        InKindExtension(address(_v())).setInKindLimit(_pair(), type(uint256).max);
    }

    function testZeroLimitRefusesEverything() public {
        _setUpInKind();
        _oneSided();
        InKindExtension(address(_v())).setInKindLimit(_pair(), 0);
        uint256 shares = _um().balanceOf(_bob());
        vm.prank(_bob());
        vm.expectPartialRevert(InKindExtension.InKindLimitExceeded.selector);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, vm.getBlockTimestamp());
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
