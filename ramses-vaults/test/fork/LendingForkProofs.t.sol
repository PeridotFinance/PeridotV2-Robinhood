// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { LendingForkTest } from "./LendingFork.t.sol";
import { PToken } from "peridot/PToken.sol";

/// @notice Phase 0 proofs on a fork of the REAL NVDA/USDG pool and nonfungible position manager (the pool is moved by real swaps; the feed is the
/// real one, re-stamped by the same simulated update the existing fork tests use). The existing fork suite runs unchanged as a base; these are the
/// tests named `...Proof...`:
///   1. funded in-kind liquidation leaves the real position and the vault ledger untouched; redeeming the seized collateral beyond local cash unwinds
///      the real LP; balance and share deltas are asserted;
///   2. persistent one-sided state on the real pool: an atomic revert, which survives a keeper recentre, a price return and three days, and which
///      only an explicit reserve funding resolves.
contract LendingForkProofsTest is LendingForkTest {
    function testForkProofFundedLiquidationThenRedemptionUnwindsRealLp() public {
        _enableBorrow();
        vm.prank(alice);
        uMarket.borrow(50e6);
        uint128 liq0 = adapter.positionState(PAIR).liquidity;
        _shift(14_000);                                                 // real swaps: the stock price falls by about 75%, the feed follows
        (,, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertGt(shortfall, 0, "alice is underwater");
        assertEq(adapter.positionState(PAIR).liquidity, liq0, "price moves do not touch the position");
        bytes memory ledgerBefore = abi.encode(vault.ledger(PAIR));

        deal(address(usdg), address(this), 60e6);
        usdg.approve(address(uMarket), type(uint256).max);
        uint256 sharesBefore = sMarket.balanceOf(address(this));
        uint256 usdgBefore = usdg.balanceOf(address(this));
        uint256 debtBefore = uMarket.borrowBalanceStored(alice);
        // two funded rounds (close factor 50% each): the seized collateral exceeds local cash AND the vault's idle stock
        uMarket.liquidateBorrow(alice, 25e6, PToken(address(sMarket)));
        assertEq(adapter.positionState(PAIR).liquidity, liq0, "round 1 did not touch the real Ramses position");
        assertEq(abi.encode(vault.ledger(PAIR)), ledgerBefore, "the vault ledger is byte-identical immediately after round 1");
        uMarket.liquidateBorrow(alice, 6e6, PToken(address(sMarket)));
        uint256 seized = sMarket.balanceOf(address(this)) - sharesBefore;
        assertGt(seized, 0, "collateral seized");
        assertEq(usdgBefore - usdg.balanceOf(address(this)), 31e6, "repaid in kind from the liquidator's own USDG");
        assertEq(debtBefore - uMarket.borrowBalanceStored(alice), 31e6, "debt fell by exactly the repayments");
        assertEq(adapter.positionState(PAIR).liquidity, liq0, "liquidation did not touch the real Ramses position");
        assertEq(abi.encode(vault.ledger(PAIR)), ledgerBefore, "the vault ledger is byte-identical immediately after both rounds");

        uint256 localCash = stock.balanceOf(address(sMarket));
        uint256 preview = seized * sMarket.exchangeRateStored() / 1e18;
        emit log_named_uint("redemption owed (stock wei)", preview);
        emit log_named_uint("local cash (stock wei)", localCash);
        assertGt(preview, localCash, "the redemption exceeds local cash, so the real LP must be unwound");
        uint256 stockBefore = stock.balanceOf(address(this));
        uint256 supplyBefore = sMarket.totalSupply();
        uint256 principalBefore = vault.ledger(PAIR).stockPrincipal;
        sMarket.redeem{ gas: 12_000_000 }(seized);
        uint256 received = stock.balanceOf(address(this)) - stockBefore;
        assertApproxEqAbs(received, seized * sMarket.exchangeRateStored() / 1e18, 100, "paid at the settled rate");
        assertApproxEqRel(received, preview, 0.01e18, "within 1% of the pre-redemption preview (the settled rate can move either way: fees collected by the unwind raise it, recognized loss lowers it)");
        assertEq(sMarket.balanceOf(address(this)), sharesBefore, "all seized pTokens burned");
        assertEq(supplyBefore - sMarket.totalSupply(), seized, "total supply fell by exactly the seized shares");
        assertLt(adapter.positionState(PAIR).liquidity, liq0, "the real LP was unwound to fund it");
        assertLt(vault.ledger(PAIR).stockPrincipal, principalBefore, "stock principal fell");
        _assertApprovals();
    }

    function testForkProofPersistentOneSidedStateAndReserveRecovery() public {
        vault.setPairPause(PAIR, false, true, false);                   // settlement swaps paused, as in the production configuration
        _shift(3000);                                                   // real swaps: the position ends up holding only the stock token
        assertEq(adapter.positionState(PAIR).usdgAmount, 0, "one-sided on the real pool");
        uint256 cash = usdg.balanceOf(address(uMarket));
        uint256 bobShares = uMarket.balanceOf(bob);
        bytes32 ledgerHash = keccak256(abi.encode(vault.ledger(PAIR)));
        uint128 liq = adapter.positionState(PAIR).liquidity;
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 12_000_000 }(cash + 5e6);
        assertEq(uMarket.balanceOf(bob), bobShares, "atomic: shares unchanged");
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash, "atomic: ledger unchanged");
        assertEq(adapter.positionState(PAIR).liquidity, liq, "the real position was not churned");

        vm.warp(vm.getBlockTimestamp() + 1 hours + 1);
        _shift(0);                                                      // simulated feed update at the unchanged low price
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);                          // the keeper acts at the low price: nothing matchable, stock idle
        // matched-only redeploy: whatever stock/USDG could be paired is redeployed, the rest stays idle (the amount depends on the pin's idle balances)
        assertLt(adapter.positionState(PAIR).liquidity, liq, "the real position was replaced by a far smaller one");
        _shift(-3000);                                                  // real swaps: the price returns
        vm.warp(vm.getBlockTimestamp() + 3 days);
        _shift(0);                                                      // three days later, a fresh feed update
        uint256 usdgAvailable = adapter.positionState(PAIR).usdgAmount + vault.ledger(PAIR).usdgIdle;
        emit log_named_uint("USDG available to the pair after recentre and price return (raw)", usdgAvailable);
        assertLt(usdgAvailable, 5e6, "far less USDG than the 5 USDG the redemption needs beyond local cash");
        uint256 ledgerNow = uint256(keccak256(abi.encode(vault.ledger(PAIR))));
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 12_000_000 }(cash + 5e6);
        assertEq(uMarket.balanceOf(bob), bobShares, "persistent: still atomic");
        assertEq(uint256(keccak256(abi.encode(vault.ledger(PAIR)))), ledgerNow, "persistent: ledger unchanged");

        deal(address(usdg), address(this), 60e6);                       // an explicit, permissionless reserve funding
        usdg.approve(address(reserve), type(uint256).max);
        reserve.deposit(PAIR, address(usdg), 60e6);
        uint256 reserveBefore = reserve.available(PAIR, address(usdg));
        uint256 bobBefore = usdg.balanceOf(bob);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 12_000_000 }(cash + 5e6);
        assertGe(usdg.balanceOf(bob) - bobBefore, cash + 5e6 - 1, "bob received the full amount");
        assertLt(reserve.available(PAIR, address(usdg)), reserveBefore, "the reserve paid the deficit");
        assertLt(uMarket.balanceOf(bob), bobShares, "shares burned for it");
    }
}
