// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";

interface ISeqVault {
    function pairConfig(bytes32) external view returns (PairConfig memory);
    function ledger(bytes32) external view returns (PairLedger memory);
    function setPairPause(bytes32, bool, bool, bool) external;
    function rebalance(bytes32, uint256) external;
    function checkpoint(bytes32, uint256) external returns (int256);
    function hasRole(bytes32, address) external view returns (bool);
}

interface ISeqAdapter {
    struct PositionState {
        uint256 tokenId;
        uint128 liquidity;
        uint256 stockAmount;
        uint256 usdgAmount;
    }
    function positionState(bytes32) external view returns (PositionState memory);
}

interface ISeqMarket {
    function syncVault(uint256) external returns (uint256, uint256);
    function vaultOperator() external view returns (address);
    function exchangeRateCurrent() external returns (uint256);
    function totalSupply() external view returns (uint256);
}

interface ISeqTimelock {
    function schedule(address, uint256, bytes calldata, bytes32, bytes32, uint256) external;
    function execute(address, uint256, bytes calldata, bytes32, bytes32) external payable;
    function cancel(bytes32) external;
    function hashOperation(address, uint256, bytes calldata, bytes32, bytes32)
        external
        view
        returns (bytes32);
    function isOperationPending(bytes32) external view returns (bool);
    function getMinDelay() external view returns (uint256);
    function hasRole(bytes32, address) external view returns (bool);
}

/// @notice The sequential USDG-shortage recovery as it will be signed: every step from the Ledger, against the DEPLOYED
/// contracts at a fresh pin. Step 1 is a timelock schedule plus execute of setPairPause(true,false,false); step 2 is
/// pUSDG.syncVault(max) from the Ledger as vault operator; step 3 is the Ledger as guardian setting (true,true,false).
/// The feed is re-stamped with its real answer after the one-hour warp, as in LiveRecovery.t.sol. STATE lines are
/// machine-readable and feed the session runbook.
contract SequentialRecoveryLedgerForkTest is Test {
    bytes32 constant PAIR = 0xE2050352f4346597cc69D2776d99aE60C9440DfC28f9406ce66be0bbe3fb6b06;
    ISeqVault constant V = ISeqVault(0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f);
    ISeqAdapter constant A = ISeqAdapter(0xadA73211711e4790bc83B5d6B39f47fE04D276f3);
    ISeqTimelock constant T = ISeqTimelock(0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498);
    ISeqMarket constant PU = ISeqMarket(0x55aEd0569c8f0D166D71facE57B57C2f2624a563);
    IERC20 constant USDG = IERC20(0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168);
    address constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
    address constant LEDGER = 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD;
    address constant KEEPER = 0x82ABD8eE038c10F5bE00E5200C94265400999f7e;
    bytes32 constant SALT = keccak256("peridot-recovery-2026-11-sequential-1");
    uint256 constant SYNC_GAS = 3_000_000;
    int256 answer0;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        (, answer0,,,) = _feed();
    }

    // ------------------------------------------------------------------ helpers
    function _feed() internal view returns (uint80, int256, uint256, uint256, uint80) {
        (bool ok, bytes memory r) = FEED.staticcall(abi.encodeWithSignature("latestRoundData()"));
        require(ok);
        return abi.decode(r, (uint80, int256, uint256, uint256, uint80));
    }

    function _stamp(int256 answer) internal {
        uint256 t = vm.getBlockTimestamp();
        vm.mockCall(
            FEED,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), answer, t, t, uint80(9))
        );
    }

    function _pauseData(bool a, bool s, bool e) internal pure returns (bytes memory) {
        return abi.encodeCall(ISeqVault.setPairPause, (PAIR, a, s, e));
    }

    function _flags() internal view returns (bool a, bool s, bool e) {
        PairConfig memory c = V.pairConfig(PAIR);
        return (c.allocationPaused, c.swapsPaused, c.emergencyMode);
    }

    function _assertFlags(bool a, bool s, bool e) internal view {
        (bool fa, bool fs, bool fe) = _flags();
        assertEq(fa, a, "allocationPaused");
        assertEq(fs, s, "swapsPaused");
        assertEq(fe, e, "emergencyMode");
    }

    /// @dev The four reads the runbook asks for after every step, plus the market claim for context.
    function _state(string memory label) internal {
        (bool a, bool s, bool e) = _flags();
        PairLedger memory l = V.ledger(PAIR);
        ISeqAdapter.PositionState memory p = A.positionState(PAIR);
        uint256 claim = PU.totalSupply() * PU.exchangeRateCurrent() / 1e18;
        emit log_string(string.concat(
                "STATE,",
                label,
                ",flags=",
                a ? "1" : "0",
                s ? "1" : "0",
                e ? "1" : "0",
                ",usdgPrincipal=",
                vm.toString(l.usdgPrincipal),
                ",stockPrincipal=",
                vm.toString(l.stockPrincipal),
                ",liquidity=",
                vm.toString(uint256(p.liquidity)),
                ",cumulativeLossUSDG=",
                vm.toString(l.cumulativeLossUSDG),
                ",usdgIdle=",
                vm.toString(l.usdgIdle),
                ",stockIdle=",
                vm.toString(l.stockIdle),
                ",cashPU=",
                vm.toString(USDG.balanceOf(address(PU))),
                ",claimPU=",
                vm.toString(claim)
            ));
    }

    function _schedule() internal {
        uint256 delay = T.getMinDelay(); // read first: vm.prank applies to the next call only
        vm.prank(LEDGER);
        T.schedule(address(V), 0, _pauseData(true, false, false), bytes32(0), SALT, delay);
    }

    function _execute() internal {
        vm.warp(vm.getBlockTimestamp() + T.getMinDelay());
        _stamp(answer0);
        bytes memory data = _pauseData(true, false, false);
        vm.prank(LEDGER);
        T.execute(address(V), 0, data, bytes32(0), SALT);
    }

    uint256 constant MAX_GO_LOSS = 500_000; // 0.50 USDG: recovery_session.sh MAX_GO_LOSS (operator 2026-11-09)

    /// @dev recovery_session.sh's GO rule: something comes back, the claim is fully accounted for, the loss is bounded.
    function _go(uint256 returned, uint256 loss, uint256 usdgPrincipal) internal pure returns (bool) {
        return returned != 0 && returned + loss == usdgPrincipal && loss <= MAX_GO_LOSS;
    }

    /// @dev The go/no-go read: an eth_call of step 2 from the Ledger, then the state it would leave. Reverted afterwards.
    function _goNoGo()
        internal
        returns (uint256 returned, uint256 loss, uint128 liquidityAfter, uint256 lossAfter)
    {
        uint256 snap = vm.snapshotState();
        vm.prank(LEDGER);
        (returned, loss) = PU.syncVault{ gas: SYNC_GAS }(type(uint256).max);
        liquidityAfter = A.positionState(PAIR).liquidity;
        lossAfter = V.ledger(PAIR).cumulativeLossUSDG;
        vm.revertToState(snap);
    }

    // ------------------------------------------------------------------ preconditions the runbook checks first
    function testPreconditionsForTheLedgerPath() public view {
        assertTrue(T.hasRole(keccak256("PROPOSER_ROLE"), LEDGER), "Ledger proposes");
        assertTrue(T.hasRole(keccak256("EXECUTOR_ROLE"), LEDGER), "Ledger executes");
        assertTrue(T.hasRole(keccak256("CANCELLER_ROLE"), LEDGER), "Ledger cancels");
        assertTrue(V.hasRole(keccak256("CONFIG_ROLE"), address(T)), "timelock holds CONFIG_ROLE");
        assertFalse(V.hasRole(keccak256("CONFIG_ROLE"), LEDGER), "Ledger cannot unpause directly");
        assertTrue(V.hasRole(keccak256("GUARDIAN_ROLE"), LEDGER), "Ledger is guardian");
        assertEq(PU.vaultOperator(), LEDGER, "Ledger is the pUSDG vault operator");
        assertEq(T.getMinDelay(), 3600);
        _assertFlags(false, true, false);
    }

    // ------------------------------------------------------------------ the whole path, step by step
    function testSequentialPathFromTheLedger() public {
        _state("0-start");
        PairLedger memory l0 = V.ledger(PAIR);
        assertGt(A.positionState(PAIR).liquidity, 0, "position open at the start");

        _schedule();
        bytes32 id =
            T.hashOperation(address(V), 0, _pauseData(true, false, false), bytes32(0), SALT);
        assertTrue(T.isOperationPending(id));
        _assertFlags(false, true, false);
        _state("1a-scheduled");

        _execute();
        _assertFlags(true, false, false);
        _state("1b-executed-window-open");

        (uint256 ret, uint256 loss, uint128 liqAfter, uint256 lossAfter) = _goNoGo();
        emit log_named_uint("GONOGO returned (USDG raw)", ret);
        emit log_named_uint("GONOGO realizedLoss (USDG raw)", loss);
        emit log_named_uint("GONOGO liquidity after", liqAfter);
        emit log_named_uint("GONOGO cumulativeLoss after", lossAfter);
        assertEq(liqAfter, 0, "go: the position is fully unwound");
        assertGt(ret, 0, "go: assets came back");
        assertTrue(_go(ret, loss, V.ledger(PAIR).usdgPrincipal), "GO under the 0.50 USDG rule");
        assertLe(lossAfter - l0.cumulativeLossUSDG, MAX_GO_LOSS * 1e12, "booked loss within the limit");

        vm.prank(LEDGER);
        (uint256 ret2, uint256 loss2) = PU.syncVault{ gas: SYNC_GAS }(type(uint256).max);
        assertEq(ret2, ret, "the signed step matches the go/no-go read");
        assertEq(loss2, loss);
        assertEq(A.positionState(PAIR).liquidity, 0, "liquidity 0 after step 2");
        _assertFlags(true, false, false);
        _state("2-synced");
        emit log_named_uint("usdgPrincipal before", l0.usdgPrincipal);
        emit log_named_uint("cumulativeLoss before", l0.cumulativeLossUSDG);

        vm.prank(LEDGER);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
        _state("3-closed");

        // The keeper can no longer allocate; a checkpoint still works.
        vm.prank(KEEPER);
        vm.expectRevert();
        V.rebalance(PAIR, vm.getBlockTimestamp());
    }

    // ------------------------------------------------------------------ abort points
    /// @dev Before execute: the Ledger cancels the schedule. Nothing changed.
    function testAbortAfterScheduleByCancel() public {
        _schedule();
        bytes32 id =
            T.hashOperation(address(V), 0, _pauseData(true, false, false), bytes32(0), SALT);
        vm.prank(LEDGER);
        T.cancel(id);
        assertFalse(T.isOperationPending(id));
        vm.warp(vm.getBlockTimestamp() + 3600);
        vm.prank(LEDGER);
        vm.expectRevert();
        T.execute(address(V), 0, _pauseData(true, false, false), bytes32(0), SALT);
        _assertFlags(false, true, false);
        _state("abort-cancelled");
    }

    /// @dev Inside the window, go/no-go says no: the Ledger as guardian closes the window at once. No timelock needed,
    /// position stays open, no loss added.
    function testAbortInsideTheWindowByGuardian() public {
        _schedule();
        _execute();
        uint256 loss0 = V.ledger(PAIR).cumulativeLossUSDG;
        uint128 liq0 = A.positionState(PAIR).liquidity;
        vm.prank(LEDGER);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
        assertEq(V.ledger(PAIR).cumulativeLossUSDG, loss0);
        assertEq(A.positionState(PAIR).liquidity, liq0, "position untouched");
        _state("abort-window-closed");
    }

    /// @dev A failed settlement is silent: syncVault succeeds but leaves the position. The go/no-go read catches it.
    function testSilentFailureIsVisibleInTheGoNoGo() public {
        _schedule();
        _execute();
        uint128 liq0 = A.positionState(PAIR).liquidity;
        vm.prank(LEDGER);
        (bool ok,) = address(PU).call{ gas: 600_000 }(
            abi.encodeCall(ISeqMarket.syncVault, (type(uint256).max))
        );
        uint128 liq1 = A.positionState(PAIR).liquidity;
        emit log_named_uint("low-gas sync ok", ok ? 1 : 0);
        emit log_named_uint("liquidity before", liq0);
        emit log_named_uint("liquidity after", liq1);
        assertGt(liq1, 0, "the low-gas call did not unwind: only a state read shows it");
        vm.prank(LEDGER);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
    }

    /// @dev A stale feed (over 12 h) at step 2: the exit refuses. Abort with the guardian.
    function testStaleFeedAtStepTwo() public {
        _schedule();
        _execute();
        uint256 t = vm.getBlockTimestamp();
        vm.mockCall(
            FEED,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), answer0, t - 12 hours - 1, t - 12 hours - 1, uint80(9))
        );
        uint128 liq0 = A.positionState(PAIR).liquidity;
        vm.prank(LEDGER);
        (bool ok,) = address(PU).call{ gas: SYNC_GAS }(
            abi.encodeCall(ISeqMarket.syncVault, (type(uint256).max))
        );
        emit log_named_uint("stale-feed sync call ok", ok ? 1 : 0);
        emit log_named_uint("stale-feed liquidity after", A.positionState(PAIR).liquidity);
        assertEq(A.positionState(PAIR).liquidity, liq0, "nothing unwound on a stale feed");
        vm.prank(LEDGER);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
    }

    /// @dev Adverse oracle moves over the one-hour delay, measured with the go/no-go read only.
    function testGoNoGoAcrossOracleMoves() public {
        _schedule();
        _execute();
        int16[7] memory bps = [int16(-300), -100, -50, 0, 50, 100, 300];
        for (uint256 i; i < bps.length; i++) {
            _stamp(answer0 * (10_000 + bps[i]) / 10_000);
            (uint256 ret, uint256 loss, uint128 liq, uint256 lossAfter) = _goNoGo();
            bool go = _go(ret, loss, V.ledger(PAIR).usdgPrincipal);
            if (bps[i] <= 50) assertTrue(go, "GO from -300 to +50 bp: loss under 0.50 USDG");
            if (bps[i] >= 100) assertFalse(go, "NO-GO on the silent failure above +100 bp");
            emit log_string(string.concat(
                    go ? "GRID,GO," : "GRID,NO-GO,",
                    "bps=",
                    vm.toString(int256(bps[i])),
                    ",returned=",
                    vm.toString(ret),
                    ",loss=",
                    vm.toString(loss),
                    ",liquidityAfter=",
                    vm.toString(uint256(liq)),
                    ",cumulativeLossAfter=",
                    vm.toString(lossAfter)
                ));
        }
    }
}
