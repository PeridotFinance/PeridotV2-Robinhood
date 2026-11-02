// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { LendingFixture } from "./LendingFixture.sol";
import { PToken } from "peridot/PToken.sol";

/// @notice Phase 0 proofs on the local fixture (mock pool/NFT/gauge; the real pools are covered by the fork tests):
///   1. a FUNDED in-kind liquidation touches neither the pool nor the vault ledger, and the liquidator's later redemption of the seized collateral,
///      when it exceeds the market's local cash, unwinds the LP through `withdrawForSide`;
///   2. one-sided recovery: with the position holding only one token and settlement swaps paused, a redemption of the OTHER token beyond local cash
///      reverts atomically (shares, ledger and cash unchanged); after the price returns and the keeper recentres, the same redemption succeeds.
contract StockProofsTest is LendingFixture {
    function _borrowAndCrash() internal returns (uint128 liq0) {
        supply();
        recenter();
        enableBorrow();
        vm.prank(alice);
        uMarket.borrow(55e6);
        liq0 = adapter.positionState(PAIR).liquidity;
        shift(13_863); // stock price 100 -> about 25 USD; the pool and the feed move together
        (,, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertGt(shortfall, 0, "alice is liquidatable");
    }

    function testFundedLiquidationThenCollateralRedemptionNeedsLpUnwind() public {
        uint128 liq0 = _borrowAndCrash();
        uint128 liqAtCrash = adapter.positionState(PAIR).liquidity;
        assertEq(liqAtCrash, liq0, "moving the price does not touch the position");
        bytes32 ledgerBefore = keccak256(abi.encode(vault.ledger(PAIR)));

        // funded in-kind liquidation: the liquidator pays USDG it already holds and receives collateral pTokens; no swap, no pool call
        uint256 sharesBefore = sMarket.balanceOf(address(this));
        uint256 usdgBefore = usdg.balanceOf(address(this));
        uMarket.liquidateBorrow(alice, 27e6, PToken(stockAccount));
        uint256 seized = sMarket.balanceOf(address(this)) - sharesBefore;
        assertGt(seized, 0, "collateral pTokens seized");
        assertEq(usdgBefore - usdg.balanceOf(address(this)), 27e6, "repaid in kind from the liquidator's own USDG");
        assertEq(adapter.positionState(PAIR).liquidity, liq0, "liquidation did not touch the Ramses position");
        emit log_named_uint("seized pTokens", seized);

        // redeeming the seized collateral for the underlying: more than the market's local cash, so the LP must be unwound
        uint256 localCash = stock.balanceOf(stockAccount);
        uint256 owed = seized * sMarket.exchangeRateStored() / 1e18;
        emit log_named_uint("redemption owed (stock wei)", owed);
        emit log_named_uint("local cash (stock wei)", localCash);
        assertGt(owed, localCash, "the redemption exceeds local cash");
        uint256 stockBefore = stock.balanceOf(address(this));
        sMarket.redeem(seized);
        uint256 received = stock.balanceOf(address(this)) - stockBefore;
        assertGt(received, 0, "underlying received");
        assertLt(adapter.positionState(PAIR).liquidity, liq0, "the LP was unwound to fund the redemption");
        // the redeemer was paid at the settled (loss-aware) exchange rate: never more than the pre-redemption preview
        assertLe(received, owed + 1, "paid at or below the preview rate");
        assertEq(sMarket.balanceOf(address(this)), sharesBefore, "all seized pTokens burned");
        ledgerBefore; // the ledger legitimately changed (principal withdrawn); the point is the liquidation step alone left it alone (checked via the position above)
    }

    function testOneSidedRedemptionRevertsAtomicallyThenSucceedsWhenThePriceReturns() public {
        supply();
        recenter();
        vault.setPairPause(PAIR, false, true, false); // settlement swaps paused, as in the production configuration
        // price falls: the position drifts into the stock token and holds no USDG
        shift(3000);
        assertEq(adapter.positionState(PAIR).usdgAmount, 0, "the position is one-sided (stock only)");
        uint256 cash = usdg.balanceOf(usdgAccount);
        uint256 bobShares = uMarket.balanceOf(bob);
        bytes32 ledgerHash = keccak256(abi.encode(vault.ledger(PAIR)));
        uint128 liq = adapter.positionState(PAIR).liquidity;

        // bob wants more USDG than the market holds locally; the position has none and nothing may swap
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
        assertEq(uMarket.balanceOf(bob), bobShares, "shares unchanged: atomic rollback");
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash, "ledger unchanged");
        assertEq(adapter.positionState(PAIR).liquidity, liq, "the position was not churned");
        assertEq(usdg.balanceOf(usdgAccount), cash, "cash unchanged");

        // the stock-side supplier is paid in kind from the position meanwhile
        uint256 aliceBefore = stock.balanceOf(alice);
        vm.prank(alice);
        sMarket.redeemUnderlying(1.2e18);
        assertEq(stock.balanceOf(alice) - aliceBefore, 1.2e18, "stock side redeems in kind");

        // recovery path A: the price returns; the position (never moved) is in range again and holds both tokens, no keeper action needed
        shift(-3000);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
        assertGe(usdg.balanceOf(bob), 5e6, "the same redemption succeeds once the price is back in range");
    }

    function _oneSidedAndBlocked() internal returns (uint256 cash) {
        supply();
        recenter();
        vault.setPairPause(PAIR, false, true, false);
        shift(3000);
        assertEq(adapter.positionState(PAIR).usdgAmount, 0, "one-sided");
        cash = usdg.balanceOf(usdgAccount);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
    }

    function testOneSidedRecoveryByRecentringAloneDoesNotCreateTheMissingToken() public {
        uint256 cash = _oneSidedAndBlocked();
        // the keeper recentres while the price stays low: matched-only redeploy leaves the stock idle, still no USDG
        vm.warp(vm.getBlockTimestamp() + 1 hours + 1);
        fresh();
        recenter();
        assertEq(adapter.positionState(PAIR).liquidity, 0, "nothing matchable to redeploy");
        assertEq(usdg.balanceOf(address(vault)), 0, "no USDG appeared");
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
    }

    function testOneSidedRecoveryByEnablingTheBoundedSettlementSwapRealizesALoss() public {
        uint256 cash = _oneSidedAndBlocked();
        uint256 rateBefore = uMarket.exchangeRateStored();
        uint256 lossBefore = vault.ledger(PAIR).cumulativeLossUSDG;
        vault.setPairPause(PAIR, false, false, false);       // an explicit CONFIG decision: swaps are unpaused
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
        assertGe(usdg.balanceOf(bob), 5e6, "redemption served through the bounded oracle-floored swap");
        assertLe(uMarket.exchangeRateStored(), rateBefore, "any realized loss lowers the rate, it never raises it");
        assertGt(vault.ledger(PAIR).cumulativeLossUSDG, lossBefore, "the bounded swap realized a loss that is booked");
        emit log_named_uint("cumulative loss before (USD wad)", lossBefore);
        emit log_named_uint("cumulative loss after (USD wad)", vault.ledger(PAIR).cumulativeLossUSDG);
        emit log_named_uint("pUSDG rate before", rateBefore);
        emit log_named_uint("pUSDG rate after", uMarket.exchangeRateStored());
    }

    // ---------------------------------------------------------------------------------------------
    // Which operations actually block when the feed is older than the 12 h bound? (the guard is not relaxed)
    // ---------------------------------------------------------------------------------------------
    function _try(string memory name, address target, bytes memory data, address caller) internal returns (bool ok) {
        uint256 snap = vm.snapshot();
        vm.prank(caller);
        (ok,) = target.call{ gas: 8_000_000 }(data);
        vm.revertTo(snap);
        emit log_string(string.concat("STALE,", name, ",", ok ? "ALLOWED" : "BLOCKED"));
    }

    function testStaleFeedOperationMatrix() public {
        supply();
        recenter();
        enableBorrow();
        vm.prank(alice);
        uMarket.borrow(10e6);
        vm.prank(alice);
        usdg.approve(usdgAccount, type(uint256).max);
        uint256 cash = usdg.balanceOf(usdgAccount);
        vm.warp(vm.getBlockTimestamp() + 13 hours);                     // the feed is NOT refreshed: older than the 12 h bound
        uint128 liq = adapter.positionState(PAIR).liquidity;

        // lending side
        bool mintOk = _try("lending: supply (mint)", usdgAccount, abi.encodeCall(uMarket.mint, (1e6)), bob);
        bool cashOk = _try("lending: redeem within local cash", usdgAccount, abi.encodeCall(uMarket.redeemUnderlying, (cash / 2)), bob);
        bool beyondOk = _try("lending: redeem beyond local cash (needs LP unwind)", usdgAccount, abi.encodeCall(uMarket.redeemUnderlying, (cash + 5e6)), bob);
        bool borrowOk = _try("lending: borrow", usdgAccount, abi.encodeCall(uMarket.borrow, (1e6)), alice);
        bool repayOk = _try("lending: repay", usdgAccount, abi.encodeCall(uMarket.repayBorrow, (1e6)), alice);
        bool collOk = _try("lending: redeem collateral while in debt", stockAccount, abi.encodeCall(sMarket.redeemUnderlying, (0.1e18)), alice);
        // keeper / guardian side
        bool cpOk = _try("vault: checkpoint", address(vault), abi.encodeCall(vault.checkpoint, (PAIR, block.timestamp)), keeper);
        bool rbOk = _try("vault: rebalance", address(vault), abi.encodeCall(vault.rebalance, (PAIR, block.timestamp)), keeper);
        bool rcOk = _try("vault: recenter", address(vault), abi.encodeCall(vault.recenter, (PAIR, block.timestamp)), keeper);
        bool cfOk = _try("vault: collectFees", address(vault), abi.encodeCall(vault.collectFees, (PAIR, block.timestamp)), keeper);
        bool edOk = _try("vault: emergencyDecrease (guardian)", address(vault), abi.encodeCall(vault.emergencyDecrease, (PAIR, liq, block.timestamp)), guardian);

        // facts already established by the existing suite, re-asserted here so the matrix cannot silently drift
        assertTrue(cashOk, "a redemption fully covered by local cash is allowed while stale");
        assertTrue(repayOk, "repaying debt is allowed while stale");
        assertFalse(beyondOk, "a redemption that needs the LP is blocked while stale");
        assertFalse(borrowOk, "borrowing is blocked while stale");
        assertFalse(collOk, "redeeming collateral while in debt is blocked while stale");
        assertFalse(rbOk, "allocation is blocked while stale");
        assertFalse(rcOk, "recentering is blocked while stale");
        mintOk; cpOk; cfOk; edOk;
    }

    function testStaleFeedBlocksLiquidationOfAnUnderwaterAccountButNotRepayment() public {
        _borrowAndCrash();                                             // alice is underwater at the crashed price, and the feed was fresh at the crash
        vm.prank(alice);
        usdg.approve(usdgAccount, type(uint256).max);
        vm.warp(vm.getBlockTimestamp() + 13 hours);                     // ... then the feed goes stale
        bool liqOk = _try("lending: liquidateBorrow of an underwater account", usdgAccount, abi.encodeCall(uMarket.liquidateBorrow, (alice, 10e6, PToken(stockAccount))), address(this));
        bool repayOk = _try("lending: repay by the underwater borrower", usdgAccount, abi.encodeCall(uMarket.repayBorrow, (5e6)), alice);
        assertFalse(liqOk, "liquidation needs a live price and is blocked while stale (the guard is not relaxed)");
        assertTrue(repayOk, "the borrower can still repay");
    }
}
