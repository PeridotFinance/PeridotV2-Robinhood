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
        assertEq(adapter.positionState(PAIR).liquidity, liq0, "moving the price does not touch the position");
        bytes memory ledgerBefore = abi.encode(vault.ledger(PAIR));

        // funded in-kind liquidation: the liquidator pays USDG it already holds and receives collateral pTokens; no swap, no pool call
        uint256 sharesBefore = sMarket.balanceOf(address(this));
        uint256 usdgBefore = usdg.balanceOf(address(this));
        uint256 borrowBefore = uMarket.borrowBalanceStored(alice);
        uMarket.liquidateBorrow(alice, 27e6, PToken(stockAccount));
        uint256 seized = sMarket.balanceOf(address(this)) - sharesBefore;
        assertGt(seized, 0, "collateral pTokens seized");
        assertEq(usdgBefore - usdg.balanceOf(address(this)), 27e6, "repaid in kind from the liquidator's own USDG");
        assertEq(borrowBefore - uMarket.borrowBalanceStored(alice), 27e6, "the borrower's debt fell by exactly the repayment");
        assertEq(adapter.positionState(PAIR).liquidity, liq0, "liquidation did not touch the Ramses position");
        // the vault ledger saved BEFORE the liquidation equals the ledger IMMEDIATELY AFTER it, byte for byte
        assertEq(abi.encode(vault.ledger(PAIR)), ledgerBefore, "the liquidation step left the vault ledger untouched");
        emit log_named_uint("seized pTokens", seized);

        // redeeming the seized collateral for the underlying: more than the market's local cash, so the LP must be unwound
        uint256 localCash = stock.balanceOf(stockAccount);
        uint256 previewOwed = seized * sMarket.exchangeRateStored() / 1e18;
        assertGt(previewOwed, localCash, "the redemption exceeds local cash");
        uint256 stockBefore = stock.balanceOf(address(this));
        uint256 totalSupplyBefore = sMarket.totalSupply();
        uint256 principalBefore = vault.ledger(PAIR).stockPrincipal;
        sMarket.redeem(seized);
        uint256 received = stock.balanceOf(address(this)) - stockBefore;
        uint256 settledRate = sMarket.exchangeRateStored();
        // balance deltas
        assertApproxEqAbs(received, seized * settledRate / 1e18, 10, "the redeemer received shares x settled rate (wei rounding of the post-redemption rate)");
        assertLe(received, previewOwed, "paid at or below the pre-redemption preview");
        assertGe(received, previewOwed * 99 / 100, "within 1% of the preview (the settled rate absorbs any recognized loss)");
        assertEq(stock.balanceOf(address(this)) - stockBefore, received);
        // share changes
        assertEq(sMarket.balanceOf(address(this)), sharesBefore, "all seized pTokens burned: the liquidator is back to its starting shares");
        assertEq(totalSupplyBefore - sMarket.totalSupply(), seized, "total pToken supply fell by exactly the seized shares");
        // the LP was unwound to fund it, and the principal fell by about what left the vault
        assertLt(adapter.positionState(PAIR).liquidity, liq0, "the LP was unwound to fund the redemption");
        assertLt(vault.ledger(PAIR).stockPrincipal, principalBefore, "stock principal fell");
        assertGe(principalBefore - vault.ledger(PAIR).stockPrincipal, received - localCash, "at least the part above local cash came out of the vault");
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

    /// @dev PERSISTENCE: once the keeper has recentred at the low price the stock sits idle and no position holds USDG, so a later price return does not
    /// restore the missing token; only an explicit funding of the loss reserve (or the bounded swap) does.
    function testOneSidedStatePersistsAfterRecentreAndPriceReturnReserveFundingRecovers() public {
        uint256 cash = _oneSidedAndBlocked();
        vm.warp(vm.getBlockTimestamp() + 1 hours + 1);
        fresh();
        recenter();                                                     // the keeper acts at the low price: the position is gone, stock idle
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        shift(-3000);                                                   // the price returns to where it was
        vm.warp(vm.getBlockTimestamp() + 3 days);                       // ... and a long time passes with fresh feed updates
        fresh();
        assertEq(adapter.positionState(PAIR).usdgAmount, 0, "still no USDG in any position");
        bytes32 ledgerHash = keccak256(abi.encode(vault.ledger(PAIR)));
        uint256 bobShares = uMarket.balanceOf(bob);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
        assertEq(uMarket.balanceOf(bob), bobShares, "still atomic: shares unchanged");
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash, "still atomic: ledger unchanged");

        // an explicit, permissionless USDG deposit into the loss reserve recovers it, without any swap
        usdg.approve(address(reserve), type(uint256).max);
        reserve.deposit(PAIR, address(usdg), 50e6);
        uint256 reserveBefore = reserve.available(PAIR, address(usdg));
        uint256 bobUsdgBefore = usdg.balanceOf(bob);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 8_000_000 }(cash + 5e6);
        assertGe(usdg.balanceOf(bob) - bobUsdgBefore, cash + 5e6 - 1, "bob received the full amount");
        assertLt(reserve.available(PAIR, address(usdg)), reserveBefore, "the reserve paid the USDG deficit");
        assertLt(uMarket.balanceOf(bob), bobShares, "bob's shares were burned for it");
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
    // Every operation is run twice on identical state: with the feed STALE (expected rejection with a specific reason, or success for the
    // operations that do not need a price) and after a FRESH-feed control (must succeed), so an unrelated failure cannot masquerade as a block.
    // ---------------------------------------------------------------------------------------------
    function _probe(address target, bytes memory data, address caller) internal returns (bool ok, bytes memory ret) {
        uint256 snap = vm.snapshot();
        vm.prank(caller);
        (ok, ret) = target.call{ gas: 8_000_000 }(data);
        vm.revertTo(snap);
    }

    function _sel(bytes memory ret) internal pure returns (bytes4 sel) {
        if (ret.length >= 4) assembly { sel := mload(add(ret, 32)) }
    }

    struct Op {
        string name;
        address target;
        bytes data;
        address caller;
        bool allowedWhenStale;
        bytes4 reason; // expected revert selector when blocked (0 = any revert is accepted only if explicitly marked in the test)
    }

    function _staleState() internal returns (uint256 cash, uint128 liq) {
        supply();
        recenter();
        enableBorrow();
        vm.prank(alice);
        uMarket.borrow(10e6);
        vm.prank(alice);
        usdg.approve(usdgAccount, type(uint256).max);
        vm.warp(vm.getBlockTimestamp() + 1 hours + 1);
        shift(700);                                                    // the oracle centre moved 700 ticks: a recenter is due; the feed follows the pool
        vm.prank(alice);
        stock.approve(stockAccount, type(uint256).max);
        liq = adapter.positionState(PAIR).liquidity;
        // add REAL supply and push the excess above the 50% buffer into the vault, so `rebalance` has idle to deploy and the fresh control can succeed
        vm.prank(bob);
        uMarket.mint(100e6);
        vm.prank(alice);
        sMarket.mint(1e18);
        vm.prank(keeper);
        uMarket.rebalanceVault();
        vm.prank(keeper);
        sMarket.rebalanceVault();
        cash = usdg.balanceOf(usdgAccount);                             // measured AFTER the top-up so `cash + x` really exceeds local cash
        vm.warp(vm.getBlockTimestamp() + 13 hours);                     // the feed is NOT refreshed: older than the 12 h bound
    }

    function testStaleFeedOperationMatrixWithFreshControlsAndSpecificReasons() public {
        (uint256 cash, uint128 liq) = _staleState();
        bytes4 staleErr = bytes4(keccak256("StaleOracle(address,uint256)"));
        Op[] memory ops = new Op[](11);
        ops[0] = Op("lending: supply (mint)", usdgAccount, abi.encodeCall(uMarket.mint, (1e6)), bob, false, staleErr);
        ops[1] = Op("lending: redeem within local cash", usdgAccount, abi.encodeCall(uMarket.redeemUnderlying, (cash / 4)), bob, true, 0);
        ops[2] = Op("lending: redeem beyond local cash (needs LP unwind)", usdgAccount, abi.encodeCall(uMarket.redeemUnderlying, (cash + 5e6)), bob, false, bytes4(keccak256("RedeemTransferOutNotPossible()")));
        ops[3] = Op("lending: borrow", usdgAccount, abi.encodeCall(uMarket.borrow, (1e6)), alice, false, staleErr);
        ops[4] = Op("lending: repay", usdgAccount, abi.encodeCall(uMarket.repayBorrow, (1e6)), alice, true, 0);
        ops[5] = Op("lending: redeem collateral while in debt", stockAccount, abi.encodeCall(sMarket.redeemUnderlying, (0.1e18)), alice, false, staleErr);
        ops[6] = Op("vault: checkpoint", address(vault), abi.encodeCall(vault.checkpoint, (PAIR, block.timestamp)), keeper, false, staleErr);
        ops[7] = Op("vault: rebalance", address(vault), abi.encodeCall(vault.rebalance, (PAIR, block.timestamp)), keeper, false, staleErr);
        ops[8] = Op("vault: recenter", address(vault), abi.encodeCall(vault.recenter, (PAIR, block.timestamp)), keeper, false, staleErr);
        ops[9] = Op("vault: collectFees", address(vault), abi.encodeCall(vault.collectFees, (PAIR, block.timestamp)), keeper, true, 0);
        ops[10] = Op("vault: emergencyDecrease (guardian)", address(vault), abi.encodeCall(vault.emergencyDecrease, (PAIR, liq, block.timestamp)), guardian, false, staleErr);

        bool[] memory staleOk = new bool[](ops.length);
        bytes[] memory staleRet = new bytes[](ops.length);
        for (uint256 i; i < ops.length; i++) {
            (staleOk[i], staleRet[i]) = _probe(ops[i].target, ops[i].data, ops[i].caller);
        }
        fresh();                                                       // the control: SAME state, feed re-stamped
        for (uint256 i; i < ops.length; i++) {
            (bool freshOk, bytes memory freshRet) = _probe(ops[i].target, ops[i].data, ops[i].caller);
            assertTrue(freshOk, string.concat("fresh-feed control must succeed: ", ops[i].name, " reason=", vm.toString(_sel(freshRet))));
            if (ops[i].allowedWhenStale) {
                assertTrue(staleOk[i], string.concat("allowed while stale: ", ops[i].name));
            } else {
                assertFalse(staleOk[i], string.concat("blocked while stale: ", ops[i].name));
                assertEq(_sel(staleRet[i]), ops[i].reason, string.concat("specific rejection reason: ", ops[i].name));
            }
            emit log_string(string.concat("STALE,", ops[i].name, ",stale=", staleOk[i] ? "ALLOWED" : "BLOCKED", ",fresh=ALLOWED,reason=", vm.toString(_sel(staleRet[i]))));
        }
    }

    function testStaleFeedBlocksLiquidationOfAnUnderwaterAccountButNotRepayment() public {
        _borrowAndCrash();                                             // alice is underwater at the crashed price, and the feed was fresh at the crash
        vm.prank(alice);
        usdg.approve(usdgAccount, type(uint256).max);
        bytes memory liqCall = abi.encodeCall(uMarket.liquidateBorrow, (alice, 10e6, PToken(stockAccount)));
        bytes memory repayCall = abi.encodeCall(uMarket.repayBorrow, (5e6));
        (bool freshLiq,) = _probe(usdgAccount, liqCall, address(this));
        assertTrue(freshLiq, "control: liquidation works with a fresh feed");
        vm.warp(vm.getBlockTimestamp() + 13 hours);                     // ... then the feed goes stale
        (bool liqOk, bytes memory liqRet) = _probe(usdgAccount, liqCall, address(this));
        (bool repayOk,) = _probe(usdgAccount, repayCall, alice);
        assertFalse(liqOk, "liquidation needs a live price and is blocked while stale (the guard is not relaxed)");
        assertEq(_sel(liqRet), bytes4(keccak256("StaleOracle(address,uint256)")), "rejected because the oracle is stale");
        assertTrue(repayOk, "the borrower can still repay");
        fresh();
        (bool liqFresh,) = _probe(usdgAccount, liqCall, address(this));
        assertTrue(liqFresh, "re-stamping the feed restores liquidation");
    }
}
