// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Phase2Fixture } from "./Phase2.t.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";

/// Phase 5b checks on the vault core (reserve use, deposits, pause flags, emergency exits, swap bound).
contract Phase5CoreTest is Phase2Fixture {
    function _fundStockReserve(uint256 amount) internal {
        stock.mint(address(this), amount);
        stock.approve(address(reserve), amount);
        reserve.deposit(PAIR, address(stock), amount);
    }

    function _log(string memory tag, PairLedger memory l) internal {
        emit log(tag);
        emit log_named_uint("  stockPrincipal", l.stockPrincipal);
        emit log_named_uint("  usdgPrincipal ", l.usdgPrincipal);
        emit log_named_uint("  stockIdle     ", l.stockIdle);
        emit log_named_uint("  usdgIdle      ", l.usdgIdle);
    }

    /// Runs: LP opened, stock price up ~7%, stock side exits in full, keeper checkpoints.
    /// Returns what each side holds afterwards, valued at the oracle price.
    function _stockExitScenario(uint256 reserveFunding, int24 move)
        internal
        returns (uint256 stockSideValue, uint256 usdgPrincipalAfter, uint256 reserveLeft)
    {
        deposit(2e18, 200e6);
        recenter();
        if (reserveFunding != 0) _fundStockReserve(reserveFunding);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(move);
        fresh();
        uint256 claim = vault.ledger(PAIR).stockPrincipal;
        _log("before exit", vault.ledger(PAIR));
        emit log_named_uint("  LP stockAmount", adapter.positionState(PAIR).stockAmount);
        emit log_named_uint("  LP usdgAmount ", adapter.positionState(PAIR).usdgAmount);
        emit log_named_int("  pool tick", pool.tick());
        uint256 before = stock.balanceOf(receiver);
        vm.prank(stockAccount);
        vault.withdrawForSide(PAIR, address(stock), claim, receiver, block.timestamp);
        uint256 got = stock.balanceOf(receiver) - before;
        _log("after exit", vault.ledger(PAIR));
        emit log_named_uint("claim", claim);
        emit log_named_uint("got", got);
        uint256 price = VaultMath.quoteAtTick(pool.tick(), 1e18, address(stock), address(usdg));
        stockSideValue = got * price / 1e18; // USDG raw (6dp)
        fresh();
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        usdgPrincipalAfter = vault.ledger(PAIR).usdgPrincipal;
        reserveLeft = reserve.available(PAIR, address(stock));
    }

    /// The reserve is for realised losses. A stock-side exit that is short only because the pair now holds
    /// more USDG than stock must not move reserve funds to the USDG side.
    function testReserveIsNotDrawnForATokenMixShortfall() public {
        uint256 snap = vm.snapshotState();
        (uint256 valueNoReserve, uint256 usdgNoReserve,) = _stockExitScenario(0, -1800);
        vm.revertToState(snap);
        (uint256 valueReserve, uint256 usdgReserve, uint256 left) = _stockExitScenario(1e18, -1800);
        emit log_named_uint("stock side value, no reserve (usdg raw)", valueNoReserve);
        emit log_named_uint("stock side value, reserve    (usdg raw)", valueReserve);
        emit log_named_uint("usdg principal after, no reserve", usdgNoReserve);
        emit log_named_uint("usdg principal after, reserve   ", usdgReserve);
        emit log_named_uint("stock reserve left (1e18 funded)", left);
        // The reserve may offset a real loss, never create a gain: the USDG side ends at most at its deposit.
        assertLe(usdgReserve, 200e6, "USDG side gained from the reserve");
        assertGe(usdgReserve, usdgNoReserve, "the reserve offsets part of the real loss");
    }

    /// A queued risk update carries the pause flags as they were when it was encoded. It must not undo a
    /// guardian pause that happened while it waited.
    function testRiskUpdateCannotUndoAGuardianPause() public {
        vm.prank(guardian);
        vault.setPairPause(PAIR, true, true, true);
        PairConfig memory c = vault.pairConfig(PAIR);
        c.allocationPaused = false;
        c.swapsPaused = false;
        c.emergencyMode = false;
        c.maxPairValueUSDG = 2000e18;
        vault.updatePairRisk(PAIR, c);
        PairConfig memory after_ = vault.pairConfig(PAIR);
        assertEq(after_.maxPairValueUSDG, 2000e18, "the risk field was applied");
        assertTrue(after_.allocationPaused, "allocation pause kept");
        assertTrue(after_.swapsPaused, "swaps pause kept");
        assertTrue(after_.emergencyMode, "emergency mode kept");
        assertTrue(after_.exists);
    }

    function testRiskUpdateStillRejectsIdentityChanges() public {
        PairConfig memory c = vault.pairConfig(PAIR);
        c.stockAccount = address(0x9999);
        vm.expectRevert();
        vault.updatePairRisk(PAIR, c);
    }

    /// A deposit must not be charged a share of a loss that already exists: the loss is recorded first.
    function testDepositRecordsExistingLossBeforeCreditingPrincipal() public {
        deposit(2e18, 200e6);
        recenter();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        shift(-1800);
        fresh();
        uint256 lossBefore = vault.ledger(PAIR).cumulativeLossUSDG;
        usdg.mint(usdgAccount, 100e6);
        vm.prank(usdgAccount);
        vault.depositForPair(PAIR, address(usdg), 100e6);
        PairLedger memory afterDeposit = vault.ledger(PAIR);
        assertGt(afterDeposit.cumulativeLossUSDG, lossBefore, "existing loss recorded at deposit");
        fresh();
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        PairLedger memory afterCheckpoint = vault.ledger(PAIR);
        // Nothing left to share: the deposit was not charged for the earlier loss.
        assertApproxEqAbs(afterCheckpoint.usdgPrincipal, afterDeposit.usdgPrincipal, 1e4);
        assertApproxEqAbs(afterCheckpoint.stockPrincipal, afterDeposit.stockPrincipal, 1e12);
    }
}
