// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import {
    QueueReopenAllocation,
    ExecuteReopenAllocation,
    OperateReopenedAllocation,
    IReopenVault,
    IReopenAdapter
} from "../../script/ReopenAllocation.s.sol";
import { LendingDelegateMarginCompatForkTest } from "./LendingDelegateMarginCompat.t.sol";

/// @notice The full reopen sequence run exactly as the governor will run it, on a local fork of
/// live state, followed by the ENTIRE margin compatibility suite (round trips, partial closes,
/// liquidations in both directions) and pToken withdrawals that must unwind the LP.
/// Every inherited test therefore runs with the LP position open.
contract ReopenAllocationForkTest is LendingDelegateMarginCompatForkTest {
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;

    uint256 internal usdRateBefore;
    uint256 internal stockRateBefore;
    PairLedger internal ledgerBefore;
    uint128 internal liquidity;

    function setUp() public override {
        super.setUp();
        new QueueReopenAllocation().run();
        vm.warp(block.timestamp + TimelockController(payable(TIMELOCK)).getMinDelay());
        vm.roll(block.number + 300);
        new ExecuteReopenAllocation().run();
        // Interest accrues on the live borrows while the timelock hour passes; settle it first so the
        // before/after comparison isolates what the LP rebalance itself changes.
        require(pUsd.accrueInterest() == 0 && pStock.accrueInterest() == 0, "ACCRUE");
        usdRateBefore = pUsd.exchangeRateStored();
        stockRateBefore = pStock.exchangeRateStored();
        ledgerBefore = IReopenVault(VAULT).ledger(PAIR);
        new OperateReopenedAllocation().run();
        liquidity = IReopenAdapter(ADAPTER).positionState(PAIR).liquidity;
        baseUsdBorrows = pUsd.totalBorrows();
        baseStockBorrows = pStock.totalBorrows();
        baseUsdShares = pUsd.totalBorrowShares();
        baseStockShares = pStock.totalBorrowShares();
        baseLocked = marginVault.lockedBalance(GOVERNOR, P_USD);
    }

    function testReopenedPairIsOpenAndAccountingIsConsistent() public {
        PairConfig memory config = IReopenVault(VAULT).pairConfig(PAIR);
        assertFalse(config.allocationPaused);
        assertTrue(config.swapsPaused, "settlement swaps stay paused");
        assertFalse(config.emergencyMode);
        assertGt(liquidity, 0);
        PairLedger memory l = IReopenVault(VAULT).ledger(PAIR);
        emit log_named_uint("stockPrincipal before", ledgerBefore.stockPrincipal);
        emit log_named_uint("stockPrincipal after", l.stockPrincipal);
        emit log_named_uint("usdgPrincipal before", ledgerBefore.usdgPrincipal);
        emit log_named_uint("usdgPrincipal after", l.usdgPrincipal);
        emit log_named_uint("stockIdle before", ledgerBefore.stockIdle);
        emit log_named_uint("stockIdle after", l.stockIdle);
        emit log_named_uint("usdgIdle before", ledgerBefore.usdgIdle);
        emit log_named_uint("usdgIdle after", l.usdgIdle);
        emit log_named_uint("liquidity", liquidity);
        emit log_named_uint("pUSDG rate before", usdRateBefore);
        emit log_named_uint("pUSDG rate after", pUsd.exchangeRateStored());
        emit log_named_uint("pNVDA rate before", stockRateBefore);
        emit log_named_uint("pNVDA rate after", pStock.exchangeRateStored());
        // Principal claims never grow from reopening; at most a recognized loss shrinks them.
        assertLe(l.stockPrincipal, ledgerBefore.stockPrincipal);
        assertLe(l.usdgPrincipal, ledgerBefore.usdgPrincipal);
        assertLt(l.stockIdle, ledgerBefore.stockIdle);
        assertLt(l.usdgIdle, ledgerBefore.usdgIdle);
        // Suppliers' exchange rates may only move by rounding-sized amounts (1e-9 relative).
        assertApproxEqRel(pUsd.exchangeRateStored(), usdRateBefore, 1e9);
        assertApproxEqRel(pStock.exchangeRateStored(), stockRateBefore, 1e9);
    }

    /// Withdrawals larger than the local buffer must reach through the open LP and still pay out.
    function testLargeRedemptionsUnwindTheLpAndPayExactly() public {
        _install();
        address[2] memory markets = [P_USD, P_STOCK];
        address[2] memory assets = [USD, STOCK];
        for (uint256 i; i < 2; ++i) {
            uint256 shares = IERC20Like(markets[i]).balanceOf(GOVERNOR);
            uint256 liquidityBefore = IReopenAdapter(ADAPTER).positionState(PAIR).liquidity;
            uint256 before = IERC20Like(assets[i]).balanceOf(GOVERNOR);
            uint256 rate = i == 0 ? pUsd.exchangeRateCurrent() : pStock.exchangeRateCurrent();
            uint256 take = shares / 10 * 9; // 90%: more than the local cash buffer holds
            uint256 expected = take * rate / 1e18;
            vm.prank(GOVERNOR);
            assertEq(i == 0 ? pUsd.redeem(take) : pStock.redeem(take), 0);
            uint256 paid = IERC20Like(assets[i]).balanceOf(GOVERNOR) - before;
            emit log_named_uint(i == 0 ? "pUSDG paid" : "pNVDA paid", paid);
            assertGe(paid, expected * 999 / 1000, "payout within 0.1% of the quoted value");
            assertLe(
                IReopenAdapter(ADAPTER).positionState(PAIR).liquidity,
                liquidityBefore,
                "LP only shrinks"
            );
        }
    }
}

interface IERC20Like {
    function balanceOf(address) external view returns (uint256);
}

/// @notice Guard rails of the procedure itself, each on a fresh fork.
contract ReopenAllocationProcedureForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    bytes32 constant PAIR = keccak256("NVDA/USDG");

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        assertTrue(IReopenVault(VAULT).pairConfig(PAIR).allocationPaused, "pre-state: paused");
    }

    function testExecutionBeforeTheDelayIsRejected() public {
        new QueueReopenAllocation().run();
        ExecuteReopenAllocation executor = new ExecuteReopenAllocation();
        vm.warp(block.timestamp + TimelockController(payable(TIMELOCK)).getMinDelay() - 1);
        vm.expectRevert("TIMELOCK_NOT_READY");
        executor.run();
        assertTrue(IReopenVault(VAULT).pairConfig(PAIR).allocationPaused);
    }

    function testQueueTwiceIsRefused() public {
        new QueueReopenAllocation().run();
        QueueReopenAllocation again = new QueueReopenAllocation();
        vm.expectRevert("OPERATION_ALREADY_EXISTS");
        again.run();
    }

    function testGuardianCannotUnpause() public {
        vm.prank(GOVERNOR); // holds GUARDIAN_ROLE but not CONFIG_ROLE
        vm.expectRevert();
        IReopenVault(VAULT).setPairPause(PAIR, false, true, false);
    }

    function testStaleFeedMakesTheOperatorStepFailClosed() public {
        new QueueReopenAllocation().run();
        vm.warp(block.timestamp + TimelockController(payable(TIMELOCK)).getMinDelay());
        new ExecuteReopenAllocation().run();
        OperateReopenedAllocation operate = new OperateReopenedAllocation();
        vm.warp(block.timestamp + 13 hours); // beyond the 12h feed bound, no new round
        vm.expectRevert();
        operate.run();
        assertEq(
            IReopenAdapter(0xadA73211711e4790bc83B5d6B39f47fE04D276f3)
            .positionState(PAIR)
            .liquidity,
            0
        );
    }

    function testSecondOperateRunAddsOnlyIdleAndNeverBreaksAccounting() public {
        new QueueReopenAllocation().run();
        vm.warp(block.timestamp + TimelockController(payable(TIMELOCK)).getMinDelay());
        new ExecuteReopenAllocation().run();
        new OperateReopenedAllocation().run();
        PairLedger memory afterFirst = IReopenVault(VAULT).ledger(PAIR);
        // A repeat finds little idle left: it may revert (nothing to add) but must not corrupt state.
        OperateReopenedAllocation again = new OperateReopenedAllocation();
        try again.run() { } catch { }
        PairLedger memory afterSecond = IReopenVault(VAULT).ledger(PAIR);
        assertEq(afterSecond.stockPrincipal, afterFirst.stockPrincipal);
        assertEq(afterSecond.usdgPrincipal, afterFirst.usdgPrincipal);
    }
}
