// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PToken } from "peridot/PToken.sol";
import { PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { LendingFixture } from "../LendingFixture.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { RoutedVault } from "./RoutedVault.sol";
import { CoverageGuard } from "./CoverageGuard.sol";
import { InKindScenarios } from "./InKindScenarios.sol";
import { InKindExtension } from "./RoutedVault.sol";
import { InKindRedeemer } from "./InKindRedeemer.sol";
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

    function _v() internal view override returns (RamsesBoostedVault) { return vault; }
    function _g() internal view override returns (RamsesOracleGuard) { return guard; }
    function _r() internal view override returns (RamsesLossReserve) { return reserve; }
    function _sm() internal view override returns (RamsesBoostedDelegate) { return sMarket; }
    function _um() internal view override returns (RamsesBoostedDelegate) { return uMarket; }
    function _st() internal view override returns (IERC20) { return IERC20(address(stock)); }
    function _us() internal view override returns (IERC20) { return IERC20(address(usdg)); }
    function _pair() internal pure override returns (bytes32) { return PAIR; }
    function _keeper() internal view override returns (address) { return keeper; }
    function _alice() internal view override returns (address) { return alice; }
    function _bob() internal view override returns (address) { return bob; }
    function _ready() internal override { supply(); recenter(); }
    function _enableBorrowing() internal override { enableBorrow(); }
    function _stockFalls(int24 t) internal override { shift(t); }
    function _refreshFeed() internal override { fresh(); }
    function _poolOnly(int24 t) internal override { pool.setTick(pool.tick() + t); }

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
            abi.encodeCall(RamsesBoostedVault.withdrawForSide, (PAIR, address(usdg), 1, address(this), block.timestamp))
        ];
        for (uint256 i; i < 4; i++) {
            uint256 snap = vm.snapshotState();
            h.arm(address(vault), attempts[i]);
            vm.prank(address(redeemer));
            InKindExtension(address(vault)).exchangeSurplus(PAIR, true, 1e6);
            assertTrue(h.fired(), "the hook ran inside the exchange");
            assertFalse(h.innerOk(), "the reentrant vault call was refused");
            emit log_named_bytes("reentrant call revert data", h.innerRet());
            bytes4 expected = i == 2 ? InKindExtension.Reentrant.selector : bytes4(keccak256("ReentrancyGuardReentrantCall()"));
            assertEq(bytes4(h.innerRet()), expected, "refused by the guard specifically, not by some later check");
            vm.revertToState(snap);
        }
    }

    function testRedeemerRefusesReentry() public {
        _setUpInKind();
        _oneSided();
        HookToken h = _armHookToken();
        h.arm(address(redeemer), abi.encodeCall(InKindRedeemer.redeemInKind, (true, 1, 0, 0, block.timestamp)));
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
        (bool done,) = address(redeemer).call{ gas: 15_000_000 }(abi.encodeCall(InKindRedeemer.redeemInKind, (true, shares, 0, 0, block.timestamp)));
        if (avail == 0) {
            assertFalse(done, "nothing to fund the exchange with: refused, not stuck");
            assertEq(uMarket.balanceOf(bob), shares, "atomic: shares returned");
            assertEq(keccak256(abi.encode(vault.ledger(PAIR))), h, "atomic: ledger unchanged");
            // an optional same-transaction float solves it, and is repaid in full
            address bridgeA = address(0xb51d6e);
            usdg.mint(bridgeA, 1_000e6);
            InKindRedeemer r2 = new InKindRedeemer(vault, PAIR, sMarket, uMarket, bridgeA);
            _authorize(r2);
            vm.prank(bridgeA);
            usdg.approve(address(r2), type(uint256).max);
            vm.prank(bob);
            uMarket.approve(address(r2), type(uint256).max);
            vm.prank(bob);
            (uint256 own, uint256 other) = r2.redeemInKind{ gas: 15_000_000 }(true, shares, 0, 0, block.timestamp);
            emit log_named_uint("with a float: USDG paid", own);
            emit log_named_uint("with a float: stock paid", other);
            assertEq(usdg.balanceOf(bridgeA), 1_000e6, "repaid in full, in the same transaction");
            emit log_named_uint("with a float: dust shares returned", uMarket.balanceOf(bob));
            assertLe(uMarket.balanceOf(bob) * uMarket.exchangeRateStored() / 1e18, 3, "only rounding dust stays with the holder, as shares");
            assertGt(other, 0);
        }
    }

    // ------------------------------------------------------------------ contract size and overhead (reported, asserted against EIP-170)
    function testSizes() public {
        _setUpInKind();
        InKindExtension ext = new InKindExtension();
        emit log_named_uint("vault implementation runtime bytes", RoutedVault(payable(address(vault))).implementation().code.length);
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
        assertEq(uint256(gate.status()), uint256(CoverageGuard.Status.COVERED), "the monitor saw no exposure before, so it reports covered: only the atomic check looks at the result");
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
        assertFalse(vault.pairConfig(PAIR).allocationPaused, "a failed monitoring call is not a pause");
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
        vm.mockCallRevert(address(reserve), abi.encodeWithSelector(RamsesLossReserve.available.selector), "RESERVE_DOWN");
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
        assertLt(adapter.positionState(PAIR).liquidity, liq, "an LP-unwinding exit works while short");
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
}
