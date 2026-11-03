// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { LendingForkBase } from "./LendingFork.t.sol";
import { IRamsesPool, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { PToken } from "peridot/PToken.sol";

/// @notice Phase 0 MECHANISM proofs on a fork of the REAL pool, the REAL nonfungible position manager and the REAL gauge of a stock/USDG pair
/// (NVDA and SPY). Precisely: the pool is moved by real swaps from a funded test trader; the vault, adapter, markets and reserve are the repository's
/// own contracts deployed on the fork; the FIRST feed read (pair registration) is the real pinned Chainlink feed, and every LATER feed update is
/// SIMULATED by `vm.mockCall` re-stamping the answer to the new pool price (there is no real subsequent feed update on a fork). The tests do not
/// establish liquidation CAPACITY: sizes are tens of USDG, the pin is one block, and measured depth (README section 3) is a separate statement.
///   1. funded in-kind liquidation leaves the real position and the vault ledger untouched; redeeming the seized collateral beyond local cash unwinds
///      the real LP; balance and share deltas are asserted;
///   2. persistent one-sided state: an atomic revert that survives a keeper recentre, a price return and three days, and that only explicit reserve
///      funding resolves; with the funding actually consumed, the exchange-rate effect on the remaining suppliers, and the funding-exhausted case.
abstract contract StockForkProofs is LendingForkBase {
    function _crash(int24 magnitude) internal {
        _shift(crashSign * magnitude);
    }

    function _recover(int24 magnitude) internal {
        _shift(-crashSign * magnitude);
    }

    function testForkProofFundedLiquidationThenRedemptionUnwindsRealLp() public {
        _enableBorrow();
        vm.prank(alice);
        uMarket.borrow(50e6);
        uint128 liq0 = adapter.positionState(PAIR).liquidity;
        _crash(14_000);                                                 // real swaps: the stock price falls by about 75%, the feed follows
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

    /// @dev Swaps paused (production configuration), the price shocked until the position holds only the stock token, a keeper recentre, the price
    /// back, three days later: returns the market's local USDG cash. Asserts the atomic revert at each stage.
    function _driveToPersistentOneSidedState() internal returns (uint256 cash) {
        vault.setPairPause(PAIR, false, true, false);                   // settlement swaps paused, as in the production configuration
        _crash(3000);                                                   // real swaps: the position ends up holding only the stock token
        assertEq(adapter.positionState(PAIR).usdgAmount, 0, "one-sided on the real pool");
        cash = usdg.balanceOf(address(uMarket));
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
        assertLt(adapter.positionState(PAIR).liquidity, liq, "the real position was replaced by a far smaller one");
        _recover(3000);                                                 // real swaps: the price returns
        vm.warp(vm.getBlockTimestamp() + 3 days);
        _shift(0);                                                      // three days later, a fresh (simulated) feed update
        uint256 usdgAvailable = adapter.positionState(PAIR).usdgAmount + vault.ledger(PAIR).usdgIdle;
        emit log_named_uint("USDG available to the pair after recentre and price return (raw)", usdgAvailable);
        assertLt(usdgAvailable, 5e6, "far less USDG than the 5 USDG the redemption needs beyond local cash");
        uint256 ledgerNow = uint256(keccak256(abi.encode(vault.ledger(PAIR))));
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 12_000_000 }(cash + 5e6);
        assertEq(uMarket.balanceOf(bob), bobShares, "persistent: still atomic");
        assertEq(uint256(keccak256(abi.encode(vault.ledger(PAIR)))), ledgerNow, "persistent: ledger unchanged");
    }

    /// @dev Funding is a permissionless deposit by `this`; the numbers below say what it paid, to whom, and what the remaining suppliers saw.
    function testForkProofPersistentOneSidedStateAndReserveRecovery() public {
        uint256 reserveAtStart = reserve.available(PAIR, address(usdg));
        uint256 cash = _driveToPersistentOneSidedState();
        uint256 request = cash + 5e6;
        uint256 uRateBefore;
        uint256 sRateBefore;
        uint256 lossBefore;
        uint256 bobShares = uMarket.balanceOf(bob);

        deal(address(usdg), address(this), 60e6);                       // an explicit, permissionless reserve funding: NOT a funded recovery programme
        usdg.approve(address(reserve), type(uint256).max);
        reserve.deposit(PAIR, address(usdg), 60e6);
        uint256 reserveBefore = reserve.available(PAIR, address(usdg));
        uRateBefore = uMarket.exchangeRateStored();
        sRateBefore = sMarket.exchangeRateStored();
        lossBefore = vault.ledger(PAIR).cumulativeLossUSDG;
        uint256 bobBefore = usdg.balanceOf(bob);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 12_000_000 }(request);
        uint256 got = usdg.balanceOf(bob) - bobBefore;
        uint256 consumed = reserveBefore - reserve.available(PAIR, address(usdg));
        assertGe(got, request - 1, "bob received the full amount");
        assertGt(consumed, 0, "the reserve paid part of it");
        assertLt(consumed, 5e6 + 1e5, "the reserve paid about the 5 USDG beyond local cash plus rounding, not the whole redemption");
        assertLt(uMarket.balanceOf(bob), bobShares, "shares burned for it");
        emit log_named_uint("reserve at start of the test (raw USDG)", reserveAtStart);
        emit log_named_uint("reserve funding deposited (raw USDG)", 60e6);
        emit log_named_uint("redemption requested (raw USDG)", request);
        emit log_named_uint("reserve funding CONSUMED (raw USDG)", consumed);
        emit log_named_uint("reserve funding left (raw USDG)", reserve.available(PAIR, address(usdg)));
        emit log_named_uint("bob shares burned (pToken raw)", bobShares - uMarket.balanceOf(bob));
        emit log_named_uint("USDG market exchange rate before", uRateBefore);
        emit log_named_uint("USDG market exchange rate after", uMarket.exchangeRateStored());
        emit log_named_uint("stock market exchange rate before", sRateBefore);
        emit log_named_uint("stock market exchange rate after", sMarket.exchangeRateStored());
        emit log_named_uint("cumulative recognized loss before (USD 1e18)", lossBefore);
        emit log_named_uint("cumulative recognized loss after (USD 1e18)", vault.ledger(PAIR).cumulativeLossUSDG);
        // who bears it: the reserve funders (the funds left the reserve); no principal write-down was recognized for the remaining suppliers
        assertEq(vault.ledger(PAIR).cumulativeLossUSDG, lossBefore, "no loss was recognized: the reserve absorbed the deficit");
        assertGe(uMarket.exchangeRateStored(), uRateBefore, "remaining USDG suppliers' exchange rate did not fall");
        assertGe(sMarket.exchangeRateStored(), sRateBefore, "remaining stock suppliers' exchange rate did not fall");
    }

    /// @dev The reserve is smaller than the deficit. Records what happens instead of assuming it.
    function testForkProofReserveExhaustedWhatHappens() public {
        uint256 cash = _driveToPersistentOneSidedState();
        uint256 request = cash + 5e6;
        deal(address(usdg), address(this), 60e6);
        usdg.approve(address(reserve), type(uint256).max);
        reserve.deposit(PAIR, address(usdg), 1e6);                      // only 1 USDG of the roughly 5 USDG deficit
        uint256 reserveBefore = reserve.available(PAIR, address(usdg));
        uint256 uRateBefore = uMarket.exchangeRateStored();
        uint256 sRateBefore = sMarket.exchangeRateStored();
        uint256 lossBefore = vault.ledger(PAIR).cumulativeLossUSDG;
        uint256 bobShares = uMarket.balanceOf(bob);
        uint256 bobBefore = usdg.balanceOf(bob);
        bytes32 ledgerHash = keccak256(abi.encode(vault.ledger(PAIR)));
        vm.prank(bob);
        (bool ok,) = address(uMarket).call{ gas: 12_000_000 }(abi.encodeWithSignature("redeemUnderlying(uint256)", request));
        emit log_named_uint("reserve available before (raw USDG)", reserveBefore);
        emit log_named_string("exact-output redemption beyond what the pair can pay", ok ? "SUCCEEDED" : "REVERTED");
        // MEASURED: with the reserve smaller than the deficit and swaps paused, the whole redemption reverts. Nothing is partially paid, no loss is
        // socialized onto the remaining suppliers through this path, and the reserve is not drawn. The exiting supplier is simply blocked until the
        // price returns, the reserve is funded further, or the (paused) settlement swap is enabled by a governance decision.
        assertFalse(ok, "reserve exhausted: the redemption reverts");
        assertEq(uMarket.balanceOf(bob), bobShares, "atomic: bob's shares unchanged");
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash, "atomic: ledger unchanged");
        assertEq(reserve.available(PAIR, address(usdg)), reserveBefore, "atomic: the reserve was not drawn");
        assertEq(usdg.balanceOf(bob), bobBefore, "atomic: bob received nothing");
        assertEq(uMarket.exchangeRateStored(), uRateBefore, "no loss was pushed onto the remaining USDG suppliers");
        assertEq(sMarket.exchangeRateStored(), sRateBefore, "no loss was pushed onto the stock suppliers");
        assertEq(vault.ledger(PAIR).cumulativeLossUSDG, lossBefore, "no loss recognized");
    }
}

/// @notice NVDA/USDG (stock = token1). Name kept: the committed log and README refer to it.
contract LendingForkProofsTest is StockForkProofs {}

/// @notice SPY/USDG CL1 (stock = token0): the opposite token order, so every tick offset that moves the STOCK price has the opposite sign.
contract SpyForkProofsTest is StockForkProofs {
    function _configure() internal override {
        PAIR = keccak256("SPY/USDG");
        pool = IRamsesPool(0x38453c115607463Ac284820Ce959831042f3Df4E);
        gauge = IRamsesGauge(0x739E31798EFE8Cff3ef4c613D8ae751e179C7337);
        feed = IAggregatorV3(0x319724394D3A0e3669269846abE664Cd621f9f6A);
        stockIsToken0 = true;
        crashSign = -1;
    }
}
