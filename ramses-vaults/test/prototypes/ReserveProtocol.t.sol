// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Vm } from "forge-std/Vm.sol";
import { LendingFixture } from "../LendingFixture.sol";
import { PairLedger, PairConfig } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { CoverageGuard } from "./CoverageGuard.sol";
import { InKindRedemptionModel, IDebtView } from "./InKindRedemptionModel.sol";

contract PairDebtView is IDebtView {
    LendingFixtureMarkets internal m;

    constructor(LendingFixtureMarkets markets) {
        m = markets;
    }

    function hasDebt(address a) external view returns (bool) {
        return m.stockDebt(a) != 0 || m.usdgDebt(a) != 0;
    }
}

interface LendingFixtureMarkets {
    function stockDebt(address) external view returns (uint256);
    function usdgDebt(address) external view returns (uint256);
}

/// @notice Phase 0 local prototypes (all under test/prototypes, nothing in src/):
///   1. reserve funding records: a dedicated seed, the vault's retained fee cut and a stray transfer are distinguishable with the EXISTING events and
///      accounting, so no new storage is needed to tell them apart;
///   2. CoverageGuard: restriction on NEW risk when the reserve does not cover the exposure;
///   3. InKindRedemptionModel: optional mixed-token redemption for debt-free suppliers, checked against the real vault's ledger after a recognized loss.
contract ReserveProtocolTest is LendingFixture, LendingFixtureMarkets {
    address internal seeder = address(0x5eed);
    CoverageGuard internal gate;

    function stockDebt(address a) external view returns (uint256) {
        return sMarket.borrowBalanceStored(a);
    }

    function usdgDebt(address a) external view returns (uint256) {
        return uMarket.borrowBalanceStored(a);
    }

    function setUp() public override {
        super.setUp();
        gate = new CoverageGuard(vault, reserve, guard, adapter, PAIR, 1000, 500); // 10% of exposure in USDG, 5% in the stock token (illustrative)
        vault.grantRole(vault.GUARDIAN_ROLE(), address(gate));
    }

    // ------------------------------------------------------------------ 1. funding records
    function testSeedRetainedEarningsAndStrayTransfersAreDistinguishable() public {
        supply();
        recenter();
        usdg.mint(seeder, 50e6);
        vm.startPrank(seeder);
        usdg.approve(address(reserve), type(uint256).max);
        vm.recordLogs();
        reserve.deposit(PAIR, address(usdg), 50e6);
        vm.stopPrank();
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bytes32 sig = keccak256("ReserveFunded(bytes32,address,address,uint256)");
        bool seen;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == sig) {
                seen = true;
                assertEq(address(uint160(uint256(logs[i].topics[3]))), seeder, "the seed's funder is the operator address");
                assertEq(abi.decode(logs[i].data, (uint256)), 50e6);
            }
        }
        assertTrue(seen);
        // the vault's retained earnings arrive through the same function with the VAULT as funder
        pool.accrueFees(100e6, 1e18);
        vm.recordLogs();
        vm.prank(keeper);
        vault.collectFees(PAIR, block.timestamp);
        logs = vm.getRecordedLogs();
        uint256 fromVault;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].topics[0] == sig) {
                assertEq(address(uint160(uint256(logs[i].topics[3]))), address(vault), "retained earnings: funder is the vault");
                fromVault++;
            }
        }
        assertGt(fromVault, 0, "the fee cut was recorded as a vault-funded event");
        // a plain transfer is NOT reserve protection: it is surplus, invisible to `available`
        uint256 availBefore = reserve.available(PAIR, address(usdg));
        usdg.mint(address(reserve), 7e6);
        assertEq(reserve.available(PAIR, address(usdg)), availBefore, "an unrecorded transfer does not count as protection");
        assertEq(IERC20Like(address(usdg)).balanceOf(address(reserve)) - reserve.accountedBalance(address(usdg)), 7e6, "it is visible as surplus");
        // supplier deposits never enter the reserve
        uint256 before_ = reserve.accountedBalance(address(usdg));
        vm.prank(bob);
        uMarket.mint(10e6);
        assertEq(reserve.accountedBalance(address(usdg)), before_, "ordinary supplier deposits are not reserve funding");
        // the seed cannot be withdrawn while the reserve is active (the admin can only take funds from a PAUSED reserve; seed and retained are fungible)
        vm.expectRevert(RamsesLossReserve.PairMustBePaused.selector);
        reserve.withdrawPausedReserve(PAIR, address(usdg), address(this), 1);
    }

    // ------------------------------------------------------------------ 2. restriction on new risk
    function _seedReserve(uint256 usdgRaw, uint256 stockWei) internal {
        usdg.mint(seeder, usdgRaw);
        stock.mint(seeder, stockWei);
        vm.startPrank(seeder);
        usdg.approve(address(reserve), type(uint256).max);
        stock.approve(address(reserve), type(uint256).max);
        reserve.deposit(PAIR, address(usdg), usdgRaw);
        reserve.deposit(PAIR, address(stock), stockWei);
        vm.stopPrank();
    }

    function testCoverageGuardLeavesAdequatelyProtectedPairsAlone() public {
        supply();
        recenter();
        (uint256 exposure,,) = gate.snapshot();
        assertGt(exposure, 100e18, "a real position exists");
        _seedReserve(50e6, 0.5e18); // 50 USDG and 0.5 stock ($50 at the fixture's $100) against about $200 of exposure
        assertTrue(gate.allowedAfter(0));
        assertFalse(gate.enforce(), "nothing to enforce");
        assertFalse(vault.pairConfig(PAIR).allocationPaused);
        assertGt(gate.headroomUsd(), 0, "the reserve supports some more liquidity");
        assertTrue(gate.allowedAfter(gate.headroomUsd() / 2));
        assertFalse(gate.allowedAfter(gate.headroomUsd() + 1e18), "more than the headroom would leave the exposure uncovered");
    }

    function testDepletedReserveRestrictsNewRiskOnlyAndTheGuardCanNeverUnpause() public {
        supply();
        recenter();
        _seedReserve(50e6, 0.5e18);
        // deplete: the reserve is paused and the admin withdraws it (the same effect as repeated cover() payouts)
        reserve.setPaused(PAIR, true);
        reserve.withdrawPausedReserve(PAIR, address(usdg), address(this), reserve.available(PAIR, address(usdg)));
        reserve.withdrawPausedReserve(PAIR, address(stock), address(this), reserve.available(PAIR, address(stock)));
        reserve.setPaused(PAIR, false);
        assertFalse(gate.allowedAfter(0), "uncovered");
        assertTrue(gate.enforce(), "enforce() pauses");
        PairConfig memory c = vault.pairConfig(PAIR);
        assertTrue(c.allocationPaused && c.swapsPaused && !c.emergencyMode, "allocation and swaps paused, nothing else changed");
        assertTrue(gate.enforce(), "idempotent");

        // new risk is blocked on every path...
        vm.prank(keeper);
        vm.expectRevert(RamsesBoostedVault.AllocationPaused.selector);
        vault.rebalance(PAIR, block.timestamp);
        vm.prank(keeper);
        vm.expectRevert(RamsesBoostedVault.AllocationPaused.selector);
        vault.recenter(PAIR, block.timestamp);
        stock.mint(stockAccount, 1e18);
        vm.startPrank(stockAccount);
        stock.approve(address(vault), type(uint256).max);
        vm.expectRevert(RamsesBoostedVault.AllocationPaused.selector);
        vault.depositForPair(PAIR, address(stock), 1e18);
        vm.stopPrank();
        // ...while exits, checkpoints and repayments are not: suppliers can still leave and losses are still recognized
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        uint256 cash = usdg.balanceOf(usdgAccount);
        uint256 got = usdg.balanceOf(bob);
        vm.prank(bob);
        uMarket.redeemUnderlying(cash / 2);
        assertEq(usdg.balanceOf(bob) - got, cash / 2, "a within-cash redemption still works");
        // the guard holds only the one-way guardian role: it cannot loosen anything
        vm.prank(address(gate));
        vm.expectRevert(RamsesBoostedVault.GuardianCannotUnpause.selector);
        vault.setPairPause(PAIR, false, false, false);
        // re-seeding does not unpause by itself: resuming is an admin decision
        _seedReserve(50e6, 0.5e18);
        assertTrue(gate.allowedAfter(0));
        assertTrue(vault.pairConfig(PAIR).allocationPaused, "still paused until the admin resumes");
        vault.setPairPause(PAIR, false, false, false);
        assertFalse(vault.pairConfig(PAIR).allocationPaused);
        assertFalse(gate.enforce());
    }

    function testCoverageGuardNeverPausesOnAStalePriceItRevertsInstead() public {
        supply();
        recenter();
        vm.warp(vm.getBlockTimestamp() + 13 hours); // feed older than 12 h; reserve empty
        vm.expectRevert();
        gate.enforce();
        assertFalse(vault.pairConfig(PAIR).allocationPaused, "no sticky pause can be forced during staleness");
    }

    // ------------------------------------------------------------------ 3. in-kind redemption
    function _modelFromTheRealVault() internal returns (InKindRedemptionModel m, InKindRedemptionModel.State memory st) {
        PairLedger memory l = vault.ledger(PAIR);
        uint160 ref = guard.referenceSqrtPriceX96(PAIR, false);
        (uint256 tokenId,, uint256 sAmt, uint256 uAmt) = _pos(ref);
        tokenId;
        st = InKindRedemptionModel.State(l.stockPrincipal, l.usdgPrincipal, l.stockIdle + sAmt, l.usdgIdle + uAmt);
        m = new InKindRedemptionModel(guard, PAIR, new PairDebtView(LendingFixtureMarkets(address(this))));
        m.load(st);
    }

    function _pos(uint160 ref) internal view returns (uint256, uint128, uint256, uint256) {
        (uint256 id, uint128 liq, uint256 s, uint256 u) = adapterState(ref);
        return (id, liq, s, u);
    }

    function adapterState(uint160 ref) internal view returns (uint256, uint128, uint256, uint256) {
        (bool ok, bytes memory ret) = address(adapter).staticcall(abi.encodeWithSignature("positionStateAt(bytes32,uint160)", PAIR, ref));
        require(ok);
        return abi.decode(ret, (uint256, uint128, uint256, uint256));
    }

    function testInKindModelMatchesTheRealLedgerAfterARecognizedLoss() public {
        supply();
        recenter();
        shift(3000); // the stock price falls about 26%; the position drifts into the stock and USDG becomes scarce
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        (InKindRedemptionModel m, InKindRedemptionModel.State memory st) = _modelFromTheRealVault();
        assertLt(st.usdgAssets, st.usdgPrincipal, "USDG is scarce: assets below principal");
        assertGe(st.stockAssets, st.stockPrincipal, "the stock side has a surplus");
        (uint256 ps,) = guard.pricesUSD18(PAIR);
        uint256 surplusUsd = (st.stockAssets - st.stockPrincipal) * ps / 1e18;
        uint256 deficitUsd = (st.usdgPrincipal - st.usdgAssets) * 1e12;
        emit log_named_uint("stock surplus (USD 1e18)", surplusUsd);
        emit log_named_uint("USDG deficit (USD 1e18)", deficitUsd);
        // the premise of the rule, measured on the real contracts: after the pro-rata write-down the surplus value equals the deficit
        assertApproxEqRel(surplusUsd, deficitUsd, 0.001e18, "surplus value equals the scarce side's deficit");
        // the whole USDG side leaves in kind
        (uint256 usdgOut, uint256 stockOut) = m.redeemInKind(bob, true, st.usdgPrincipal);
        emit log_named_uint("USDG paid", usdgOut);
        emit log_named_uint("stock paid (wei)", stockOut);
        assertEq(usdgOut, st.usdgAssets, "all the USDG the pair holds");
        assertApproxEqRel(usdgOut * 1e12 + stockOut * ps / 1e18, st.usdgPrincipal * 1e12, 1e12, "paid exactly the claim at the oracle price (rounding down only)");
        (,, uint256 sa, uint256 ua) = m.s();
        assertGe(sa, st.stockPrincipal, "the stock side's own claim is still fully covered");
        assertEq(ua, 0);
    }

    function testInKindRefusesStaleOraclesAndAccountsWithDebt() public {
        supply();
        recenter();
        shift(3000);
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        (InKindRedemptionModel m, InKindRedemptionModel.State memory st) = _modelFromTheRealVault();
        // debt: alice borrows against her stock collateral; she is not debt-free
        enableBorrow();
        vm.prank(alice);
        uMarket.borrow(5e6);
        vm.expectRevert(InKindRedemptionModel.HasDebt.selector);
        m.redeemInKind(alice, false, 1e15);
        // stale oracle: no estimate is substituted to make the exit pass
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.expectRevert();
        m.redeemInKind(bob, true, st.usdgPrincipal / 2);
        vm.expectRevert();
        m.quote(true, st.usdgPrincipal / 2);
    }

    // ------------------------------------------------------------------ properties of the rule (fuzzed)
    function _state(uint256 pu, uint256 deltaBps, uint256 psUsd, uint256 pStock) internal pure returns (InKindRedemptionModel.State memory st, uint256 ps) {
        pu = bound(pu, 1e6, 1e12);
        deltaBps = bound(deltaBps, 0, 9_999);
        ps = bound(psUsd, 1e18, 5e21) / 1e10 * 1e10; // the 8-decimal feed cannot carry finer prices: use exactly what the guard will return
        pStock = bound(pStock, 1e15, 1e21);
        uint256 au = pu - pu * deltaBps / 10_000;
        uint256 surplusUnits = (pu - au) * 1e12 * 1e18 / ps;
        st = InKindRedemptionModel.State(pStock, pu, pStock + surplusUnits, au);
    }

    function _modelAt(InKindRedemptionModel.State memory st, uint256 ps) internal returns (InKindRedemptionModel m) {
        feed.set(int256(ps / 1e10));
        m = new InKindRedemptionModel(guard, PAIR, new PairDebtView(LendingFixtureMarkets(address(this))));
        m.load(st);
    }

    function testFuzzTheMixDoesNotDependOnTheOrderOfExits(uint256 pu, uint256 delta, uint256 psUsd, uint256 pStock, uint256 split) public {
        (InKindRedemptionModel.State memory st, uint256 ps) = _state(pu, delta, psUsd, pStock);
        split = bound(split, 1, st.usdgPrincipal - 1);
        InKindRedemptionModel a = _modelAt(st, ps);
        InKindRedemptionModel b = _modelAt(st, ps);
        (uint256 u1, uint256 s1) = a.redeemInKind(bob, true, split);
        (uint256 u2, uint256 s2) = a.redeemInKind(carolLike(), true, st.usdgPrincipal - split);
        (uint256 u3, uint256 s3) = b.redeemInKind(carolLike(), true, st.usdgPrincipal - split);
        (uint256 u4, uint256 s4) = b.redeemInKind(bob, true, split);
        // the same claim gets the same mix whichever order: within one unit of rounding of the claim
        assertApproxEqAbs(u1, u4, 2, "USDG part of the same claim");
        assertApproxEqAbs(u2, u3, 2, "USDG part of the other claim");
        assertApproxEqAbs(s1, s4, 2 * 1e12 * 1e18 / ps + 2, "stock part of the same claim: only the one-unit USDG rounding can differ");
        assertApproxEqAbs(s2, s3, 2 * 1e12 * 1e18 / ps + 2, "and of the other claim");
    }

    function testFuzzConservationAndRemainingHoldersAreNotCharged(uint256 pu, uint256 delta, uint256 psUsd, uint256 pStock, uint256 take) public {
        (InKindRedemptionModel.State memory st, uint256 ps) = _state(pu, delta, psUsd, pStock);
        take = bound(take, 1, st.usdgPrincipal);
        InKindRedemptionModel m = _modelAt(st, ps);
        (uint256 uOut, uint256 sOut) = m.redeemInKind(bob, true, take);
        // never pays more than the claim at the oracle price
        assertLe(uOut * 1e12 + sOut * ps / 1e18, take * 1e12, "rounding favours the remaining holders");
        // within rounding it pays the claim
        assertGe(uOut * 1e12 + sOut * ps / 1e18 + ps / 1e18 + 1e12, take * 1e12, "and not materially less");
        (uint256 Ps, uint256 Pu, uint256 As, uint256 Au) = _read(m);
        assertGe(As, Ps, "the stock side's claim stays covered");
        assertEq(Ps, st.stockPrincipal, "stock principal untouched");
        // remaining USDG holders keep the same assets-to-principal ratio (cross-multiplied, one unit of rounding)
        if (Pu > 0) assertApproxEqAbs(Au * st.usdgPrincipal, st.usdgAssets * Pu, st.usdgPrincipal + Pu + 1, "same share of the deficit");
    }


    /// @dev The mirror case: the stock price ROSE, the STOCK is scarce and the USDG side holds the surplus.
    function testFuzzStockSideMirror(uint256 pStock, uint256 delta, uint256 psUsd, uint256 pu, uint256 take) public {
        pStock = bound(pStock, 1e15, 1e21);
        delta = bound(delta, 0, 9_999);
        uint256 ps = bound(psUsd, 1e18, 5e21) / 1e10 * 1e10;
        pu = bound(pu, 1e6, 1e12);
        uint256 as_ = pStock - pStock * delta / 10_000;
        uint256 surplusUsdg = ((pStock - as_) * ps / 1e18) / 1e12; // floor: the surplus never overstates the deficit
        InKindRedemptionModel.State memory st = InKindRedemptionModel.State(pStock, pu, as_, pu + surplusUsdg);
        take = bound(take, 1, pStock);
        InKindRedemptionModel m = _modelAt(st, ps);
        (uint256 uOut, uint256 sOut) = m.redeemInKind(bob, false, take);
        assertLe(uOut * 1e12 + sOut * ps / 1e18, take * ps / 1e18 + 1, "never more than the claim at the oracle price");
        (uint256 Ps, uint256 Pu, uint256 As, uint256 Au) = _read(m);
        assertGe(Au, Pu, "the USDG side's own claim stays covered");
        assertEq(Pu, st.usdgPrincipal, "USDG principal untouched");
        if (Ps > 0) assertApproxEqAbs(As * st.stockPrincipal, st.stockAssets * Ps, st.stockPrincipal + Ps + 1, "same share of the deficit");
    }


    function testBothTokensMustBeCoveredNotEither() public {
        supply();
        recenter();
        _seedReserve(500e6, 0); // plenty of USDG, no stock token at all
        assertFalse(gate.allowedAfter(0), "USDG alone is not enough: a price rise leaves the stock token scarce");
        assertTrue(gate.enforce());
        vault.setPairPause(PAIR, false, false, false);
        _seedReserve(0, 5e18); // now plenty of stock token but remove USDG coverage by using a fresh pair state
        assertTrue(gate.allowedAfter(0), "both tokens covered");
    }

    function testInKindRefusesWhenTheOtherSideHasNoSurplus() public {
        // inconsistent state on purpose: USDG is short by 50 USDG but the stock side holds no surplus at all
        InKindRedemptionModel.State memory st = InKindRedemptionModel.State(1e18, 100e6, 1e18, 50e6);
        InKindRedemptionModel m = _modelAt(st, 100e18);
        vm.expectRevert(InKindRedemptionModel.InsufficientSurplus.selector);
        m.redeemInKind(bob, true, 100e6);
        // and the mirror
        st = InKindRedemptionModel.State(1e18, 100e6, 0.5e18, 100e6);
        m = _modelAt(st, 100e18);
        vm.expectRevert(InKindRedemptionModel.InsufficientSurplus.selector);
        m.redeemInKind(bob, false, 1e18);
    }

    function testOneSidedInventoryPaysEntirelyInTheOtherToken() public {
        InKindRedemptionModel.State memory st = InKindRedemptionModel.State(1e18, 100e6, 1e18 + 1e18, 0); // all the USDG is gone; 1 stock of surplus at $100
        InKindRedemptionModel m = _modelAt(st, 100e18);
        (uint256 u, uint256 s) = m.redeemInKind(bob, true, 100e6);
        assertEq(u, 0);
        assertEq(s, 1e18, "100 USDG of claim paid as 1 stock at the oracle price");
    }

    function testDepletedReserveIsNotNeededAndNotTouched() public {
        supply();
        recenter();
        shift(3000);
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        assertEq(reserve.available(PAIR, address(usdg)), 0);
        (InKindRedemptionModel m, InKindRedemptionModel.State memory st) = _modelFromTheRealVault();
        uint256 before_ = reserve.accountedBalance(address(usdg));
        m.redeemInKind(bob, true, st.usdgPrincipal / 3);
        assertEq(reserve.accountedBalance(address(usdg)), before_, "no reserve was read or used");
    }

    function testNoDeficitMeansNoMixing() public {
        InKindRedemptionModel.State memory st = InKindRedemptionModel.State(1e18, 100e6, 1e18, 100e6);
        InKindRedemptionModel m = _modelAt(st, 100e18);
        (uint256 u, uint256 s) = m.redeemInKind(bob, true, 40e6);
        assertEq(u, 40e6);
        assertEq(s, 0, "fully covered claims are paid in their own token");
    }

    function _read(InKindRedemptionModel m) internal view returns (uint256 ps, uint256 pu, uint256 as_, uint256 au) {
        (ps, pu, as_, au) = m.s();
    }

    function carolLike() internal pure returns (address) {
        return address(0xca401);
    }
}

interface IERC20Like {
    function balanceOf(address) external view returns (uint256);
}
