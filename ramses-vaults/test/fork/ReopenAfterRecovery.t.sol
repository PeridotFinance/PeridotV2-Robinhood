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

/// @notice Reopening allocation after the sequential recovery (RECOVERY_SESSION.md), as it will be signed: every step from the Ledger, against the DEPLOYED
/// contracts at a fresh pin. Step 1 is a timelock schedule plus execute of setPairPause(true,false,false); step 2 is
/// pUSDG.syncVault(max) from the Ledger as vault operator; step 3 is the Ledger as guardian setting (true,true,false).
/// The feed is re-stamped with its real answer after the one-hour warp, as in LiveRecovery.t.sol. STATE lines are
/// machine-readable and feed the session runbook.
contract ReopenAfterRecoveryForkTest is Test {
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

    bytes32 constant REOPEN_SALT = keccak256("peridot-reopen-allocation-2026-11-1");

    function _recover() internal {
        _schedule();
        _execute();
        vm.prank(LEDGER);
        PU.syncVault{ gas: SYNC_GAS }(type(uint256).max);
        vm.prank(LEDGER);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
        assertEq(A.positionState(PAIR).liquidity, 0, "recovered: no liquidity");
    }

    function _scheduleReopen() internal {
        uint256 delay = T.getMinDelay();
        bytes memory data = _pauseData(false, true, false);
        vm.prank(LEDGER);
        T.schedule(address(V), 0, data, bytes32(0), REOPEN_SALT, delay);
    }

    function _executeReopen() internal {
        vm.warp(vm.getBlockTimestamp() + T.getMinDelay());
        _stamp(answer0);
        bytes memory data = _pauseData(false, true, false);
        vm.prank(LEDGER);
        T.execute(address(V), 0, data, bytes32(0), REOPEN_SALT);
    }

    /// @dev The full reopen: recovery, then timelock setPairPause(pair, false, true, false). Allocation opens; settlement
    /// swaps stay paused. The keeper can then checkpoint and rebalance, and it deploys only what is matched and idle.
    function testReopenAfterRecoveryAndWhatTheKeeperCanDeploy() public {
        _recover();
        _state("r0-recovered");
        _scheduleReopen();
        _assertFlags(true, true, false);
        _executeReopen();
        _assertFlags(false, true, false);
        _state("r1-reopened");
        PairLedger memory l = V.ledger(PAIR);
        vm.prank(KEEPER);
        V.checkpoint(PAIR, vm.getBlockTimestamp() + 120);
        vm.prank(KEEPER);
        (bool ok, bytes memory ret) = address(V).call{ gas: 3_000_000 }(
            abi.encodeCall(ISeqVault.rebalance, (PAIR, vm.getBlockTimestamp() + 120))
        );
        emit log_named_uint("keeper rebalance after reopen ok", ok ? 1 : 0);
        if (!ok) emit log_bytes(ret);
        _state("r2-after-keeper");
        emit log_named_uint("idle NVDA before", l.stockIdle);
        emit log_named_uint("idle USDG before", l.usdgIdle);
        emit log_named_uint("liquidity after keeper", uint256(A.positionState(PAIR).liquidity));
    }

    /// @dev Reopen before recovery is pointless and is refused by the operator check, not the contract: prove the
    /// contract would accept it, so the runbook precondition (flags 110, liquidity 0) is what protects the order.
    function testReopenScheduleIsAcceptedOnlyByTheProposerAndCanBeCancelled() public {
        _recover();
        uint256 delay = T.getMinDelay();
        bytes memory data = _pauseData(false, true, false);
        vm.prank(address(0xbad));
        vm.expectRevert();
        T.schedule(address(V), 0, data, bytes32(0), REOPEN_SALT, delay);
        _scheduleReopen();
        bytes32 id = T.hashOperation(address(V), 0, data, bytes32(0), REOPEN_SALT);
        vm.prank(LEDGER);
        T.cancel(id);
        assertFalse(T.isOperationPending(id));
        _assertFlags(true, true, false);
    }

    /// @dev A stale feed at execute: the timelock call itself does not read the oracle, so it succeeds; the keeper's
    /// rebalance then refuses until the feed is fresh. The runbook therefore executes only with a fresh answer.
    function testStaleFeedAfterReopenBlocksAllocationNotTheFlagChange() public {
        _recover();
        _scheduleReopen();
        vm.warp(vm.getBlockTimestamp() + T.getMinDelay());
        uint256 t = vm.getBlockTimestamp();
        vm.mockCall(
            FEED,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), answer0, t - 13 hours, t - 13 hours, uint80(9))
        );
        bytes memory data = _pauseData(false, true, false);
        vm.prank(LEDGER);
        T.execute(address(V), 0, data, bytes32(0), REOPEN_SALT);
        _assertFlags(false, true, false);
        vm.prank(KEEPER);
        (bool ok,) = address(V).call{ gas: 3_000_000 }(
            abi.encodeCall(ISeqVault.rebalance, (PAIR, vm.getBlockTimestamp() + 120))
        );
        assertFalse(ok, "no allocation on a stale feed");
        assertEq(A.positionState(PAIR).liquidity, 0);
    }
}
