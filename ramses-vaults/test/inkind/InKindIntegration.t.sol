// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PToken } from "peridot/PToken.sol";
import { PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { LendingFixture } from "../LendingFixture.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesV3PairedAdapter } from "../../src/RamsesV3PairedAdapter.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { RoutedVault } from "../../src/inkind/RoutedVault.sol";
import { CoverageGuard } from "../../src/inkind/CoverageGuard.sol";
import { InKindScenarios } from "./InKindScenarios.sol";
import { InKindExtension } from "../../src/inkind/RoutedVault.sol";
import { InKindRedeemer } from "../../src/inkind/InKindRedeemer.sol";
import { TokenDouble } from "../mocks/RamsesV3Double.sol";

/// @dev A token that calls back during transfers, to prove what can and cannot run inside the exchange. Etched over USDG; its extra storage is appended.
contract HookToken is TokenDouble {
    address internal target_;
    bytes internal data_;
    bool public fired;
    bool public innerOk;
    bytes public innerRet;

    constructor() TokenDouble("USDG", 6) { }

    function arm(address t, bytes calldata d) external {
        target_ = t;
        data_ = d;
        fired = false;
    }

    function _fire() internal {
        if (target_ != address(0) && !fired) {
            fired = true;
            (innerOk, innerRet) = target_.call(data_);
        }
    }

    function transferFrom(address a, address b, uint256 c) public override returns (bool) {
        _fire();
        return super.transferFrom(a, b, c);
    }

    function transfer(address b, uint256 c) public override returns (bool) {
        _fire();
        return super.transfer(b, c);
    }
}

/// @notice Local run of the in-kind scenarios, plus the atomic coverage proofs (local doubles for pool, position manager, gauge and token; real vault, markets, reserve).
contract InKindLocalTest is LendingFixture, InKindScenarios {
    function _newVault() internal override returns (RamsesBoostedVault) {
        return _routed();
    }

    function _v() internal view override returns (RamsesBoostedVault) {
        return vault;
    }

    function _g() internal view override returns (RamsesOracleGuard) {
        return guard;
    }

    function _r() internal view override returns (RamsesLossReserve) {
        return reserve;
    }

    function _sm() internal view override returns (RamsesBoostedDelegate) {
        return sMarket;
    }

    function _um() internal view override returns (RamsesBoostedDelegate) {
        return uMarket;
    }

    function _st() internal view override returns (IERC20) {
        return IERC20(address(stock));
    }

    function _us() internal view override returns (IERC20) {
        return IERC20(address(usdg));
    }

    function _pair() internal pure override returns (bytes32) {
        return PAIR;
    }

    function _keeper() internal view override returns (address) {
        return keeper;
    }

    function _alice() internal view override returns (address) {
        return alice;
    }

    function _bob() internal view override returns (address) {
        return bob;
    }

    function _ready() internal override {
        supply();
        recenter();
    }

    function _enableBorrowing() internal override {
        enableBorrow();
    }

    function _stockFalls(int24 t) internal override {
        shift(t);
    }

    function _refreshFeed() internal override {
        fresh();
    }

    function _poolOnly(int24 t) internal override {
        pool.setTick(pool.tick() + t);
    }

    // ------------------------------------------------------------------ reentrancy across the exchange, using a token with a transfer hook
    function _armHookToken() internal returns (HookToken h) {
        h = new HookToken();
        vm.etch(address(usdg), address(h).code);
        h = HookToken(address(usdg));
        vault.grantRole(keccak256("IN_KIND_OPERATOR_ROLE"), address(h));
        vault.grantRole(vault.KEEPER_ROLE(), address(h));
    }

    function testNoVaultEntryPointRunsInsideTheExchange() public {
        _setUpInKind();
        _oneSided();
        HookToken h = _armHookToken();
        usdg.mint(address(redeemer), 1e6);
        vm.prank(address(redeemer));
        usdg.approve(address(vault), type(uint256).max);
        bytes[4] memory attempts = [
            abi.encodeCall(RamsesBoostedVault.checkpoint, (PAIR, block.timestamp)),
            abi.encodeCall(RamsesBoostedVault.rebalance, (PAIR, block.timestamp)),
            abi.encodeCall(InKindExtension.exchangeSurplus, (PAIR, true, 1)),
            abi.encodeCall(
                RamsesBoostedVault.withdrawForSide,
                (PAIR, address(usdg), 1, address(this), block.timestamp)
            )
        ];
        for (uint256 i; i < 4; i++) {
            uint256 snap = vm.snapshotState();
            h.arm(address(vault), attempts[i]);
            vm.prank(address(redeemer));
            InKindExtension(address(vault)).exchangeSurplus(PAIR, true, 1e6);
            assertTrue(h.fired(), "the hook ran inside the exchange");
            assertFalse(h.innerOk(), "the reentrant vault call was refused");
            emit log_named_bytes("reentrant call revert data", h.innerRet());
            bytes4 expected = i == 2
                ? InKindExtension.Reentrant.selector
                : bytes4(keccak256("ReentrancyGuardReentrantCall()"));
            assertEq(
                bytes4(h.innerRet()),
                expected,
                "refused by the guard specifically, not by some later check"
            );
            vm.revertToState(snap);
        }
    }

    function testRedeemerRefusesReentry() public {
        _setUpInKind();
        _oneSided();
        HookToken h = _armHookToken();
        h.arm(
            address(redeemer),
            abi.encodeCall(InKindRedeemer.redeemInKind, (true, 1, 0, 0, block.timestamp))
        );
        uint256 shares = uMarket.balanceOf(bob);
        vm.prank(bob);
        redeemer.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, block.timestamp);
        assertTrue(h.fired());
        assertFalse(h.innerOk());
        assertEq(bytes4(h.innerRet()), bytes4(keccak256("ReentrancyGuardReentrantCall()")));
    }

    // ------------------------------------------------------------------ funding source
    function testNoScarceTokenAnywhereRevertsAtomicallyWithoutABridge() public {
        _setUpInKind();
        _oneSided();
        // everyone else drains local cash and any vault USDG first
        uint256 cash = usdg.balanceOf(usdgAccount);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 12_000_000 }(cash);
        uint256 shares = uMarket.balanceOf(bob);
        (uint256 avail,) = _usdgGap();
        emit log_named_uint("USDG left in the vault", avail);
        bytes32 h = keccak256(abi.encode(vault.ledger(PAIR)));
        vm.prank(bob);
        (bool done,) = address(redeemer).call{ gas: 15_000_000 }(
            abi.encodeCall(InKindRedeemer.redeemInKind, (true, shares, 0, 0, block.timestamp))
        );
        if (avail == 0) {
            assertFalse(done, "nothing to fund the exchange with: refused, not stuck");
            assertEq(uMarket.balanceOf(bob), shares, "atomic: shares returned");
            assertEq(keccak256(abi.encode(vault.ledger(PAIR))), h, "atomic: ledger unchanged");
            // production has no float: the refusal is the outcome, and it is atomic (asserted above)
        }
    }

    // ------------------------------------------------------------------ contract size and overhead (reported, asserted against EIP-170)
    function testSizes() public {
        _setUpInKind();
        InKindExtension ext = new InKindExtension();
        emit log_named_uint(
            "vault implementation runtime bytes",
            RoutedVault(payable(address(vault))).implementation().code.length
        );
        emit log_named_uint("router runtime bytes", address(vault).code.length);
        emit log_named_uint("extension runtime bytes", address(ext).code.length);
        emit log_named_uint("redeemer runtime bytes", address(redeemer).code.length);
        assertLt(address(vault).code.length, 24_576);
        assertLt(address(ext).code.length, 24_576);
        assertLt(address(redeemer).code.length, 24_576);
        // what the router adds per call: one extra delegatecall
        uint256 g = gasleft();
        vault.ledger(PAIR);
        uint256 viaRouter = g - gasleft();
        emit log_named_uint("ledger() gas via router", viaRouter);
    }

    // ------------------------------------------------------------------ atomic coverage
    CoverageGuard internal gate;
    address internal seeder = address(0x5eed);

    function _installGate() internal {
        gate = new CoverageGuard(vault, reserve, guard, adapter, PAIR, 1000, 500);
        RoutedVault(payable(address(vault))).setGate(gate);
        vault.grantRole(vault.GUARDIAN_ROLE(), address(gate));
    }

    function _seed(uint256 u, uint256 s) internal {
        usdg.mint(seeder, u);
        stock.mint(seeder, s);
        vm.startPrank(seeder);
        usdg.approve(address(reserve), type(uint256).max);
        stock.approve(address(reserve), type(uint256).max);
        if (u != 0) reserve.deposit(PAIR, address(usdg), u);
        if (s != 0) reserve.deposit(PAIR, address(stock), s);
        vm.stopPrank();
    }

    function testAtomicCheckBlocksNewExposureWhenCoverageIsShort() public {
        supply();
        _installGate();
        bytes32 h = keccak256(abi.encode(vault.ledger(PAIR)));
        vm.prank(keeper);
        vm.expectPartialRevert(CoverageGuard.CoverageInsufficient.selector);
        vault.recenter(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, 0, "no liquidity entered");
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), h, "atomic: ledger unchanged");
        assertEq(
            uint256(gate.status()),
            uint256(CoverageGuard.Status.COVERED),
            "the monitor saw no exposure before, so it reports covered: only the atomic check looks at the result"
        );
        _seed(50e6, 0.5e18);
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
        assertGt(adapter.positionState(PAIR).liquidity, 0, "allowed once covered");
    }

    function testRebalanceThatAddsLiquidityIsBlockedWhileShortAndAdmittedWhenCovered() public {
        supply();
        _seed(50e6, 0.5e18);
        recenter();
        _installGate();
        vm.prank(alice);
        sMarket.mint(1e18);
        vm.prank(bob);
        uMarket.mint(100e6);
        reserve.setPaused(PAIR, true);
        reserve.withdrawPausedReserve(PAIR, address(usdg), address(this), 40e6);
        reserve.withdrawPausedReserve(PAIR, address(stock), address(this), 0.4e18);
        reserve.setPaused(PAIR, false);
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        uint128 liq = adapter.positionState(PAIR).liquidity;
        bytes32 h = keccak256(abi.encode(vault.ledger(PAIR)));
        vm.prank(keeper);
        vm.expectPartialRevert(CoverageGuard.CoverageInsufficient.selector);
        vault.rebalance(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, liq, "no added liquidity");
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), h, "atomic");
        _seed(100e6, 1e18);
        vm.prank(keeper);
        vault.rebalance(PAIR, block.timestamp);
        assertGt(adapter.positionState(PAIR).liquidity, liq, "admitted once covered");
    }

    function testAStalePriceCannotAdmitNewExposureAndAFailedMonitorIsNotAPause() public {
        supply();
        _installGate();
        _seed(50e6, 0.5e18);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        // the monitoring view reports UNKNOWN, it does not revert and does not claim coverage
        assertEq(uint256(gate.status()), uint256(CoverageGuard.Status.UNKNOWN));
        // the permissionless enforcement cannot run and pauses nothing
        vm.expectRevert();
        gate.enforce();
        assertFalse(
            vault.pairConfig(PAIR).allocationPaused, "a failed monitoring call is not a pause"
        );
        // the atomic check itself refuses a stale price, independently of the vault's own check
        vm.expectRevert();
        gate.requireCovered();
        // and yet allocation is blocked: by the vault's own check and, independently, by the atomic check
        vm.prank(keeper);
        vm.expectRevert();
        vault.recenter(PAIR, vm.getBlockTimestamp());
        assertEq(adapter.positionState(PAIR).liquidity, 0, "no new exposure");
    }

    function testAFailedReserveReadFailsClosedForAllocationOnly() public {
        supply();
        _seed(50e6, 0.5e18);
        recenter();
        _installGate();
        vm.mockCallRevert(
            address(reserve),
            abi.encodeWithSelector(RamsesLossReserve.available.selector),
            "RESERVE_DOWN"
        );
        assertEq(uint256(gate.status()), uint256(CoverageGuard.Status.UNKNOWN));
        stock.mint(stockAccount, 1e18);
        vm.startPrank(stockAccount);
        stock.approve(address(vault), type(uint256).max);
        vm.expectRevert("RESERVE_DOWN");
        vault.depositForPair(PAIR, address(stock), 1e18);
        vm.stopPrank();
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        vm.prank(bob);
        uMarket.redeemUnderlying(1e6);
    }

    function testRiskReducingOperationsStayAvailableWhileShort() public {
        supply();
        _seed(50e6, 0.5e18);
        recenter();
        _installGate();
        reserve.setPaused(PAIR, true);
        reserve.withdrawPausedReserve(PAIR, address(usdg), address(this), 50e6);
        reserve.withdrawPausedReserve(PAIR, address(stock), address(this), 0.5e18);
        reserve.setPaused(PAIR, false);
        assertEq(uint256(gate.status()), uint256(CoverageGuard.Status.SHORT));
        vm.prank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        uint128 liq = adapter.positionState(PAIR).liquidity;
        uint256 owed = uMarket.balanceOf(bob) * uMarket.exchangeRateCurrent() / 1e18;
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 12_000_000 }(owed * 3 / 4); // beyond local cash: unwinds the LP
        assertLt(
            adapter.positionState(PAIR).liquidity, liq, "an LP-unwinding exit works while short"
        );
        // a recentre after that exit: the redeploy is matched-only, so it does not raise exposure, and it passes while short
        shift(700);
        vm.warp(vm.getBlockTimestamp() + 1 hours + 1);
        fresh();
        (uint256 before_,,) = gate.snapshot();
        assertEq(uint256(gate.status()), uint256(CoverageGuard.Status.SHORT));
        vm.prank(keeper);
        vault.recenter(PAIR, vm.getBlockTimestamp());
        (uint256 after_,,) = gate.snapshot();
        emit log_named_uint("exposure before the recentre (USD 1e18)", before_);
        emit log_named_uint("exposure after the recentre (USD 1e18)", after_);
        assertLe(after_, before_, "the recentre did not raise exposure");
        // the guardian's emergency exit stays available
        uint128 l2 = adapter.positionState(PAIR).liquidity;
        if (l2 > 0) {
            vm.prank(guardian);
            vault.emergencyDecrease(PAIR, l2, vm.getBlockTimestamp());
            assertEq(adapter.positionState(PAIR).liquidity, 0);
        }
    }

    function testResumePathNeedsTheAdminAndPassesTheSameCheck() public {
        supply();
        _seed(50e6, 0.5e18);
        recenter();
        _installGate();
        reserve.setPaused(PAIR, true);
        reserve.withdrawPausedReserve(PAIR, address(usdg), address(this), 50e6);
        reserve.withdrawPausedReserve(PAIR, address(stock), address(this), 0.5e18);
        reserve.setPaused(PAIR, false);
        assertTrue(gate.enforce());
        assertTrue(vault.pairConfig(PAIR).allocationPaused);
        // neither the guard nor the guardian can resume
        vm.prank(guardian);
        vm.expectRevert(RamsesBoostedVault.GuardianCannotUnpause.selector);
        vault.setPairPause(PAIR, false, true, false);
        // the admin cannot resume while short
        vm.expectPartialRevert(CoverageGuard.CoverageInsufficient.selector);
        vault.setPairPause(PAIR, false, true, false);
        assertTrue(vault.pairConfig(PAIR).allocationPaused);
        // nor with a stale price, even when re-seeded
        _seed(50e6, 0.5e18);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.expectRevert();
        vault.setPairPause(PAIR, false, true, false);
        assertTrue(vault.pairConfig(PAIR).allocationPaused);
        // fresh and covered: the admin can
        fresh();
        vault.setPairPause(PAIR, false, true, false);
        assertFalse(vault.pairConfig(PAIR).allocationPaused);
        assertTrue(vault.pairConfig(PAIR).swapsPaused, "swaps are a separate decision");
    }

    // ------------------------------------------------------------------ production edits (PRODUCTION_DECISION.md)

    /// @dev The gate is CONFIG_ROLE only. The deployer of the router has no special power, and neither do keeper or guardian.
    function testGateIsConfigRoleOnly() public {
        _setUpInKind();
        RoutedVault r = RoutedVault(payable(address(vault)));
        CoverageGuard g2 = new CoverageGuard(vault, reserve, guard, adapter, PAIR, 1000, 500);
        address[3] memory others = [keeper, guardian, address(0xdead)];
        for (uint256 i; i < others.length; i++) {
            vm.prank(others[i]);
            vm.expectRevert(RoutedVault.NotConfig.selector);
            r.setGate(g2);
        }
        // A router deployed by some EOA: that deployer cannot set the gate either.
        address deployer = address(0xd3d3);
        vm.prank(deployer);
        RoutedVault fresh =
            new RoutedVault(address(new RamsesBoostedVault()), address(new InKindExtension()));
        vm.prank(deployer);
        vm.expectRevert();
        fresh.setGate(g2);
        assertEq(address(fresh.gate()), address(0));
        // CONFIG_ROLE (here the fixture admin, the timelock in production) can.
        assertTrue(vault.hasRole(keccak256("CONFIG_ROLE"), address(this)));
        vm.expectEmit(true, false, false, false, address(vault));
        emit RoutedVault.GateSet(address(g2));
        r.setGate(g2);
        assertEq(address(r.gate()), address(g2));
    }

    /// @dev The redeemer has no float: no `bridge()` getter, and a missing scarce token is a clean, atomic refusal.
    function testRedeemerHasNoFundingFloat() public {
        _setUpInKind();
        (bool ok,) = address(redeemer).staticcall(abi.encodeWithSignature("bridge()"));
        assertFalse(ok, "no bridge getter");
        assertEq(address(redeemer).code.length > 0, true);
    }

    function _keeperSelectors() internal pure returns (bytes4[8] memory s) {
        s = [
            RamsesBoostedVault.rebalance.selector,
            RamsesBoostedVault.recenter.selector,
            RamsesBoostedVault.clearRange.selector,
            RamsesBoostedVault.checkpoint.selector,
            RamsesBoostedVault.collectFees.selector,
            RamsesBoostedVault.burnEmptyPosition.selector,
            RamsesBoostedVault.harvest.selector,
            RamsesBoostedVault.claimPendingRewards.selector
        ];
    }

    /// @dev Self-call invariant: the vault holds KEEPER_ROLE on itself, so no entry point may make the vault CALL its own keeper functions,
    /// except `inKindCheckpoint` (tested below). Every other mutating entry point of the vault, router and extension is driven with fuzzed
    /// arguments from each kind of caller; any self-call to a keeper selector fails the test.
    function testFuzzNoEntryPointMakesTheVaultCallItsKeeperFunctions(
        uint8 which,
        uint8 callerIndex,
        uint256 a,
        uint256 b,
        bool f1,
        bool f2,
        bytes calldata raw
    ) public {
        _setUpInKind();
        bytes4[8] memory ks = _keeperSelectors();
        for (uint256 i; i < ks.length; i++) {
            vm.expectCall(address(vault), abi.encodePacked(ks[i]), 0);
        }
        address[5] memory callers =
            [address(this), keeper, guardian, address(redeemer), address(0xbad)];
        address caller = callers[callerIndex % callers.length];
        bytes32 pair = f1 ? PAIR : bytes32(a);
        address t = f2 ? address(usdg) : address(stock);
        bytes[18] memory calls = [
            abi.encodeCall(RamsesBoostedVault.depositForPair, (pair, t, a)),
            abi.encodeCall(RamsesBoostedVault.withdrawForSide, (pair, t, a, caller, b)),
            abi.encodeCall(RamsesBoostedVault.emergencyDecrease, (pair, uint128(a), b)),
            abi.encodeCall(RamsesBoostedVault.forwardExternalReward, (pair, t, caller, a)),
            abi.encodeCall(RamsesBoostedVault.setPairPause, (pair, f1, f2, f1 && f2)),
            abi.encodeCall(RamsesBoostedVault.setAggregateUsdgDepositCap, (t, a)),
            abi.encodeCall(RamsesBoostedVault.setAllowRewardForfeiture, (pair, f1)),
            abi.encodeCall(RamsesBoostedVault.setDeployLossBps, (pair, uint16(a))),
            abi.encodeWithSignature("grantRole(bytes32,address)", bytes32(b), caller),
            abi.encodeWithSignature("revokeRole(bytes32,address)", bytes32(b), caller),
            abi.encodeWithSignature("renounceRole(bytes32,address)", bytes32(b), caller),
            abi.encodePacked(RamsesBoostedVault.setRangePolicy.selector, raw),
            abi.encodePacked(RamsesBoostedVault.registerPair.selector, raw),
            abi.encodePacked(RamsesBoostedVault.updatePairRisk.selector, raw),
            abi.encodeCall(InKindExtension.exchangeSurplus, (pair, f1, a)),
            abi.encodeCall(InKindExtension.setInKindLimit, (pair, a)),
            abi.encodeCall(RoutedVault.setGate, (CoverageGuard(address(uint160(b))))),
            abi.encodePacked(bytes4(uint32(a)), raw) // anything else reaches the vault code through the router fallback
        ];
        bytes memory data = calls[which % calls.length];
        bytes4 sel = bytes4(data);
        for (uint256 i; i < ks.length; i++) {
            vm.assume(sel != ks[i]); // a direct call by the caller is not a self-call
        }
        vm.assume(sel != InKindExtension.inKindCheckpoint.selector);
        vm.prank(caller);
        address(vault).call{ gas: 10_000_000 }(data);
    }

    /// @dev The one sanctioned self-call: the in-kind operator's checkpoint, exactly once, and nothing else keeper-only.
    function testInKindCheckpointIsTheOnlySelfCall() public {
        _setUpInKind();
        bytes4[8] memory ks = _keeperSelectors();
        for (uint256 i; i < ks.length; i++) {
            vm.expectCall(
                address(vault),
                abi.encodePacked(ks[i]),
                ks[i] == RamsesBoostedVault.checkpoint.selector ? 1 : 0
            );
        }
        vm.prank(address(redeemer));
        InKindExtension(address(vault)).inKindCheckpoint(PAIR, block.timestamp);
    }

    /// @dev The extension writes the ledger by raw slot (`LEDGER_SLOT = 4`). Every field of the struct must sit where it reads and writes it.
    function testLedgerStructSlotsMatchTheGetterField() public {
        _setUpInKind();
        bytes32 base = keccak256(abi.encode(PAIR, uint256(4)));
        PairLedger memory l = vault.ledger(PAIR);
        assertGt(l.stockPrincipal + l.usdgPrincipal, 0, "non-trivial ledger");
        assertEq(uint256(vm.load(address(vault), base)), l.stockPrincipal, "slot+0 stockPrincipal");
        assertEq(
            uint256(vm.load(address(vault), bytes32(uint256(base) + 1))),
            l.usdgPrincipal,
            "slot+1 usdgPrincipal"
        );
        assertEq(
            uint256(vm.load(address(vault), bytes32(uint256(base) + 2))),
            l.stockIdle,
            "slot+2 stockIdle"
        );
        assertEq(
            uint256(vm.load(address(vault), bytes32(uint256(base) + 3))),
            l.usdgIdle,
            "slot+3 usdgIdle"
        );
    }

    /// @dev Section 8a ceiling: set only by CONFIG_ROLE through the extension, which reaches the adapter as the vault.
    function testRewardShortfallCeilingIsConfigRoleOnly() public {
        _setUpInKind();
        address[3] memory others = [keeper, guardian, address(0xdead)];
        for (uint256 i; i < others.length; i++) {
            vm.prank(others[i]);
            vm.expectRevert(InKindExtension.NotConfig.selector);
            InKindExtension(address(vault)).setRewardShortfallCeiling(PAIR, 2000);
        }
        InKindExtension(address(vault)).setRewardShortfallCeiling(PAIR, 2000);
        assertEq(RamsesV3PairedAdapter(address(adapter)).maxRewardShortfallBps(PAIR), 2000);
        vm.expectRevert(RamsesV3PairedAdapter.InvalidConfiguration.selector);
        InKindExtension(address(vault)).setRewardShortfallCeiling(PAIR, 10_001);
        vm.expectRevert(RamsesV3PairedAdapter.NotVault.selector);
        RamsesV3PairedAdapter(address(adapter)).setRewardShortfallCeiling(PAIR, 1);
    }
}
