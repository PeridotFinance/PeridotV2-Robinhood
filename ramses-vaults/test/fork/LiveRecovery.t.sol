// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IPoolManager } from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import { IUnlockCallback } from "@uniswap/v4-core/src/interfaces/callback/IUnlockCallback.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";
import { Currency } from "@uniswap/v4-core/src/types/Currency.sol";
import { BalanceDelta } from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { StateLibrary } from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import { PoolIdLibrary } from "@uniswap/v4-core/src/types/PoolId.sol";

interface ILiveVault {
    function pairConfig(bytes32) external view returns (PairConfig memory);
    function ledger(bytes32) external view returns (PairLedger memory);
    function setPairPause(bytes32, bool, bool, bool) external;
    function checkpoint(bytes32, uint256) external returns (int256);
    function rebalance(bytes32, uint256) external;
    function recenter(bytes32, uint256) external;
}

interface ILiveAdapter {
    struct PositionState {
        uint256 tokenId;
        uint128 liquidity;
        uint256 stockAmount;
        uint256 usdgAmount;
    }
    function positionState(bytes32) external view returns (PositionState memory);
    function poolKey(bytes32) external view returns (PoolKey memory);
}

interface ILiveMarket {
    function syncVault(uint256) external returns (uint256, uint256);
    function redeem(uint256) external returns (uint256);
    function mint(uint256) external returns (uint256);
    function balanceOf(address) external view returns (uint256);
    function totalSupply() external view returns (uint256);
    function exchangeRateCurrent() external returns (uint256);
    function totalBorrowsCurrent() external returns (uint256);
    function totalReserves() external view returns (uint256);
    function queueSetVaultOperator(address) external returns (bytes32);
    function _setVaultOperator(address) external;
    function vaultOperator() external view returns (address);
    function actionDelay() external view returns (uint256);
}

interface ILiveReserve {
    function available(bytes32, address) external view returns (uint256);
}

interface ILiveTimelock {
    function scheduleBatch(address[] calldata, uint256[] calldata, bytes[] calldata, bytes32, bytes32, uint256) external;
    function executeBatch(address[] calldata, uint256[] calldata, bytes[] calldata, bytes32, bytes32) external payable;
    function getMinDelay() external view returns (uint256);
}

/// @dev A real swap through the real PoolManager, standing in for an adversary or for a market move.
contract PoolPusher is IUnlockCallback {
    IPoolManager constant PM = IPoolManager(0x8366a39CC670B4001A1121B8F6A443A643e40951);
    PoolKey key;

    constructor(PoolKey memory k) {
        key = k;
    }

    function push(bool zeroForOne, uint160 limit) external {
        PM.unlock(abi.encode(zeroForOne, limit));
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == address(PM));
        (bool zeroForOne, uint160 limit) = abi.decode(data, (bool, uint160));
        BalanceDelta d = PM.swap(key, IPoolManager.SwapParams(zeroForOne, -int256(1e30), limit), "");
        (int128 a0, int128 a1) = (d.amount0(), d.amount1());
        _settle(key.currency0, a0);
        _settle(key.currency1, a1);
        return "";
    }

    function _settle(Currency c, int128 a) private {
        if (a < 0) {
            PM.sync(c);
            IERC20(Currency.unwrap(c)).transfer(address(PM), uint256(uint128(-a)));
            PM.settle();
        } else if (a > 0) {
            PM.take(c, address(this), uint256(uint128(a)));
        }
    }
}

/// @notice Rehearsal of the live USDG-shortage recovery on a fork of the DEPLOYED contracts at a fresh pin. Nothing is deployed anywhere but the fork and nothing is sent.
/// The timelock delay and the market's action delay are real (time is warped). The feed is re-stamped with vm.mockCall because time is warped past its answer; the
/// answer is the real one unless a test shifts it. All amounts are read, not assumed. STATE, GRID and RUN lines are machine-readable.
contract LiveRecoveryForkTest is Test {
    using StateLibrary for IPoolManager;
    using PoolIdLibrary for PoolKey;

    bytes32 constant PAIR = 0xE2050352f4346597cc69D2776d99aE60C9440DfC28f9406ce66be0bbe3fb6b06;
    ILiveVault constant V = ILiveVault(0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f);
    ILiveAdapter constant A = ILiveAdapter(0xadA73211711e4790bc83B5d6B39f47fE04D276f3);
    ILiveReserve constant R = ILiveReserve(0x806b182B050f7EcF908758dD6bBF91DB8B2212aF);
    ILiveTimelock constant T = ILiveTimelock(0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498);
    ILiveMarket constant PU = ILiveMarket(0x55aEd0569c8f0D166D71facE57B57C2f2624a563);
    ILiveMarket constant PN = ILiveMarket(0xa155ccCB986774AE818b3F10F07d01D1b7A47b26);
    IERC20 constant USDG = IERC20(0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168);
    IERC20 constant NVDA = IERC20(0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC);
    address constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
    address constant GOV = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant KEEPER = 0x978e2ADF18da8B846552932c0aB2B1193da1fAc4;
    address constant MARGIN_VAULT = 0x04D4A5555b7a37017A67B4D21A1Da5838de28B9e;
    address constant HOLDER_X = 0xDc48FD39733af117a4FFDa35d7B325de9BdEDFDe;
    address constant INSURANCE = 0x17c72B8f171999C4d8863a3517D1C5d9cBfBb068;
    int256 answer0;

    function setUp() public {
        // RETIRED 2026-11-06. This rehearsal assumes the pre-incident operators: the old governor GOV and keeper 0x978e.
        // Both lost every role in the 2026-11-05 handover to the Ledger, so the suite can no longer run against live
        // state. The same recovery, signed by the Ledger, is covered by SequentialRecoveryLedger.t.sol on PR 20
        // (ops/solvency-sequential). Kept for its recorded evidence; never point it at a different signer.
        vm.skip(true);
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        (, answer0,,,) = _feed();
    }

    function _feed() internal view returns (uint80, int256, uint256, uint256, uint80) {
        (bool ok, bytes memory r) = FEED.staticcall(abi.encodeWithSignature("latestRoundData()"));
        require(ok);
        return abi.decode(r, (uint80, int256, uint256, uint256, uint80));
    }

    function _stamp(int256 answer) internal {
        uint256 t = vm.getBlockTimestamp();
        vm.mockCall(FEED, abi.encodeWithSignature("latestRoundData()"), abi.encode(uint80(9), answer, t, t, uint80(9)));
    }

    function _warp(uint256 s) internal {
        vm.warp(vm.getBlockTimestamp() + s);
    }

    function _state(string memory label) internal {
        PairConfig memory c = V.pairConfig(PAIR);
        PairLedger memory l = V.ledger(PAIR);
        ILiveAdapter.PositionState memory p = A.positionState(PAIR);
        uint256 ru = PU.exchangeRateCurrent();
        uint256 rn = PN.exchangeRateCurrent();
        emit log_string(string.concat("STATE,", label, ",flags(alloc/swap/emerg)=", c.allocationPaused ? "1" : "0", c.swapsPaused ? "1" : "0", c.emergencyMode ? "1" : "0",
            ",liq=", vm.toString(uint256(p.liquidity)), ",posNVDA=", vm.toString(p.stockAmount), ",posUSDG=", vm.toString(p.usdgAmount)));
        emit log_string(string.concat("STATE,", label, ",idleNVDA=", vm.toString(l.stockIdle), ",idleUSDG=", vm.toString(l.usdgIdle), ",prinNVDA=", vm.toString(l.stockPrincipal), ",prinUSDG=", vm.toString(l.usdgPrincipal),
            ",cumLoss=", vm.toString(l.cumulativeLossUSDG)));
        emit log_string(string.concat("STATE,", label, ",cashPU=", vm.toString(USDG.balanceOf(address(PU))), ",cashPN=", vm.toString(NVDA.balanceOf(address(PN))), ",claimPU=", vm.toString(PU.totalSupply() * ru / 1e18),
            ",claimPN=", vm.toString(PN.totalSupply() * rn / 1e18), ",borrowPU=", vm.toString(PU.totalBorrowsCurrent()), ",reservePU=", vm.toString(PU.totalReserves()),
            ",rateU=", vm.toString(ru), ",rateN=", vm.toString(rn), ",resUSDG=", vm.toString(R.available(PAIR, address(USDG))), ",resNVDA=", vm.toString(R.available(PAIR, address(NVDA)))));
    }

    function _flags() internal view returns (bool, bool, bool) {
        PairConfig memory c = V.pairConfig(PAIR);
        return (c.allocationPaused, c.swapsPaused, c.emergencyMode);
    }

    function _assertFlags(bool a, bool s, bool e) internal view {
        (bool fa, bool fs, bool fe) = _flags();
        assertEq(fa, a, "allocationPaused");
        assertEq(fs, s, "swapsPaused");
        assertEq(fe, e, "emergencyMode");
    }

    // ------------------------------------------------------------------ timelock helpers
    function _pause(bool a, bool s, bool e) internal pure returns (bytes memory) {
        return abi.encodeCall(ILiveVault.setPairPause, (PAIR, a, s, e));
    }

    function _batch(bytes[] memory calls, address[] memory targets, bytes32 salt) internal returns (uint256[] memory values) {
        values = new uint256[](calls.length);
        uint256 d = T.getMinDelay();
        vm.prank(GOV);
        T.scheduleBatch(targets, values, calls, bytes32(0), salt, d);
    }

    function _atomicCalls() internal pure returns (address[] memory t, bytes[] memory c) {
        t = new address[](3);
        c = new bytes[](3);
        (t[0], c[0]) = (address(V), _pause(true, false, false));
        (t[1], c[1]) = (address(PU), abi.encodeCall(ILiveMarket.syncVault, (type(uint256).max)));
        (t[2], c[2]) = (address(V), _pause(true, true, false));
    }

    /// @dev Real delays. The market's operator change has its own queue.
    function _operatorToTimelock() internal {
        vm.prank(GOV);
        PU.queueSetVaultOperator(address(T));
        _warp(3600); // actionDelay, asserted below
        assertEq(PU.actionDelay(), 3600);
        _stamp(answer0);
        vm.prank(GOV);
        PU._setVaultOperator(address(T));
        assertEq(PU.vaultOperator(), address(T));
    }

    function _runBatch(int256 answer, uint256 gasLimit) internal returns (bool ok, uint256 gasUsed) {
        (address[] memory t, bytes[] memory c) = _atomicCalls();
        uint256[] memory v = _batch(c, t, keccak256("recovery"));
        _warp(T.getMinDelay());
        _stamp(answer);
        vm.prank(GOV);
        uint256 g0 = gasleft();
        (ok,) = address(T).call{ gas: gasLimit }(abi.encodeCall(ILiveTimelock.executeBatch, (t, v, c, bytes32(0), keccak256("recovery"))));
        gasUsed = g0 - gasleft();
    }

    function _bankRun() internal returns (uint256 out) {
        address[4] memory hs = [MARGIN_VAULT, GOV, HOLDER_X, INSURANCE];
        for (uint256 i; i < 4; i++) {
            uint256 b0 = USDG.balanceOf(hs[i]);
            uint256 sh = PU.balanceOf(hs[i]);
            vm.prank(hs[i]);
            (bool ok,) = address(PU).call{ gas: 8_000_000 }(abi.encodeCall(ILiveMarket.redeem, (sh)));
            uint256 got = USDG.balanceOf(hs[i]) - b0;
            out += got;
            emit log_string(string.concat("RUN,", vm.toString(hs[i]), ",ok=", ok ? "1" : "0", ",shares=", vm.toString(sh), ",got=", vm.toString(got)));
        }
    }

    // ------------------------------------------------------------------ 1. flags
    function testWideFlagsLeaveAllocationOpenNarrowFlagsDoNot() public {
        _state("pin");
        _assertFlags(false, true, false);
        vm.prank(address(T));
        V.setPairPause(PAIR, false, false, false);
        _assertFlags(false, false, false); // the corrected proposal's error: allocation stays OPEN alongside swaps
        vm.prank(address(T));
        V.setPairPause(PAIR, true, false, false);
        _assertFlags(true, false, false); // swaps on, allocation off: the contract accepts this combination
        vm.prank(address(T));
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
    }

    function testGuardianCanPauseButCannotEnableSwaps() public {
        vm.prank(GOV);
        vm.expectRevert();
        V.setPairPause(PAIR, true, false, false);
        vm.prank(GOV);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
    }

    // ------------------------------------------------------------------ 2. is an atomic batch possible?
    function testBatchFailsAtomicallyWhileTheGovernorIsTheOperator() public {
        assertEq(PU.vaultOperator(), GOV);
        (address[] memory t, bytes[] memory c) = _atomicCalls();
        uint256[] memory v = _batch(c, t, keccak256("recovery"));
        vm.prank(GOV);
        vm.expectRevert();
        T.executeBatch(t, v, c, bytes32(0), keccak256("recovery")); // before the delay
        _warp(T.getMinDelay());
        _stamp(answer0);
        bytes32 h = keccak256(abi.encode(V.ledger(PAIR)));
        vm.prank(GOV);
        vm.expectRevert();
        T.executeBatch(t, v, c, bytes32(0), keccak256("recovery")); // OnlyVaultOperator inside the batch
        _assertFlags(false, true, false);
        assertEq(keccak256(abi.encode(V.ledger(PAIR))), h, "the unpause in step 1 was rolled back");
    }

    function testOperatorChangeHasItsOwnDelay() public {
        vm.prank(GOV);
        vm.expectRevert();
        PU._setVaultOperator(address(T));
        vm.prank(GOV);
        PU.queueSetVaultOperator(address(T));
        vm.prank(GOV);
        vm.expectRevert();
        PU._setVaultOperator(address(T));
    }

    // ------------------------------------------------------------------ 3. the reference atomic sequence
    function testAtomicRecoveryReference() public {
        emit log_named_int("feed answer at the pin (8 decimals)", answer0);
        _state("0-pin");
        uint256 cumBefore = V.ledger(PAIR).cumulativeLossUSDG;
        uint256 stockPrincipal = V.ledger(PAIR).stockPrincipal;
        _operatorToTimelock();
        (bool ok, uint256 gasUsed) = _runBatch(answer0, 6_000_000);
        assertTrue(ok, "batch executed");
        emit log_named_uint("executeBatch gas", gasUsed);
        _state("1-after-batch");
        _assertFlags(true, true, false);
        ILiveAdapter.PositionState memory p = A.positionState(PAIR);
        assertEq(p.liquidity, 0, "LP fully unwound");
        PairLedger memory l = V.ledger(PAIR);
        // syncVault(max) fixes the amount before fees are collected inside the same call, so the fee credited then stays as principal; it must be fully backed in USDG.
        emit log_named_uint("USDG principal left in the vault: fee credited during the call (raw)", l.usdgPrincipal);
        assertGe(l.usdgIdle, l.usdgPrincipal, "any principal left is fully backed in USDG: the shortage is gone");
        assertEq(USDG.balanceOf(address(V)), l.usdgIdle, "vault USDG balance equals the ledger");
        assertEq(NVDA.balanceOf(address(V)), l.stockIdle, "vault NVDA balance equals the ledger");
        assertGe(l.stockPrincipal, stockPrincipal, "stock principal is not reduced; it can only rise by the fee credit collected inside the same call");
        emit log_named_uint("stock principal credited by fees inside the batch (wei)", l.stockPrincipal - stockPrincipal);
        assertGe(l.stockIdle, l.stockPrincipal, "stock side covered");
        assertEq(l.cumulativeLossUSDG, cumBefore, "no loss recognized");
        // USDG left unavailable = what is lent out, exactly
        uint256 ru = PU.exchangeRateCurrent();
        uint256 claims = PU.totalSupply() * ru / 1e18;
        uint256 cash = USDG.balanceOf(address(PU));
        uint256 borrows = PU.totalBorrowsCurrent();
        uint256 reserves = PU.totalReserves();
        emit log_named_uint("pUSDG claims", claims);
        emit log_named_uint("pUSDG cash", cash);
        emit log_named_uint("pUSDG borrows", borrows);
        assertApproxEqAbs(cash + borrows - reserves + l.usdgPrincipal, claims, 2, "claims = cash + borrows - reserves");
        uint256 out = _bankRun();
        emit log_named_uint("bank run USDG out", out);
        _state("2-after-run");
    }

    // ------------------------------------------------------------------ 4. adverse movement across the delay
    function _gridRow(string memory label, int256 ans, bool preOk, uint256 g) internal {
        PairLedger memory l = V.ledger(PAIR);
        ILiveAdapter.PositionState memory p = A.positionState(PAIR);
        (bool a, bool s, bool e) = _flags();
        emit log_string(string.concat("GRID,", label, ",answer=", vm.toString(ans), ",batchOk=", preOk ? "1" : "0", ",gas=", vm.toString(g), ",flags=", a ? "1" : "0", s ? "1" : "0", e ? "1" : "0",
            ",liq=", vm.toString(uint256(p.liquidity)), ",prinUSDG=", vm.toString(l.usdgPrincipal), ",prinNVDA=", vm.toString(l.stockPrincipal), ",idleNVDA=", vm.toString(l.stockIdle), ",idleUSDG=", vm.toString(l.usdgIdle),
            ",cumLoss=", vm.toString(l.cumulativeLossUSDG), ",cashPU=", vm.toString(USDG.balanceOf(address(PU))), ",resUSDG=", vm.toString(R.available(PAIR, address(USDG)))));
    }

    /// @dev The oracle moves over the delay while the pool does not: the oracle-floored swap and the removal bound decide.
    function testAdverseOracleMoveGrid() public {
        _operatorToTimelock();
        uint256 snap = vm.snapshotState();
        int16[9] memory bps = [int16(-700), -300, -100, -50, 0, 50, 100, 300, 700];
        for (uint256 i; i < bps.length; i++) {
            int256 ans = answer0 * (10_000 + int256(bps[i])) / 10_000;
            (bool ok, uint256 g) = _runBatch(ans, 6_000_000);
            _gridRow(string.concat("oracle", vm.toString(int256(bps[i])), "bp"), ans, ok, g);
            // whatever happened, allocation cannot be open with swaps on, and no emergency state appears
            (bool a, bool s, bool e) = _flags();
            assertFalse(e, "emergency untouched");
            if (ok) assertTrue(a && s, "a batch that completes ends paused");
            else assertTrue(!a && s, "a batch that reverts leaves the original flags");
            vm.revertToState(snap);
        }
    }

    /// @dev Pool and feed move together, as in a real market move during the delay: a real swap on the real pool.
    function testPriceMovesWithThePoolDuringTheDelay() public {
        _operatorToTimelock();
        uint256 snap = vm.snapshotState();
        PoolKey memory k = A.poolKey(PAIR);
        int24[5] memory offs = [int24(2000), 1000, 300, -300, -1000]; // tick up = NVDA falls (NVDA is token1)
        for (uint256 i; i < offs.length; i++) {
            (, int24 tick,,) = IPoolManager(0x8366a39CC670B4001A1121B8F6A443A643e40951).getSlot0(k.toId());
            PoolPusher pp = new PoolPusher(k);
            deal(address(USDG), address(pp), 50_000_000e6);
            deal(address(NVDA), address(pp), 100_000e18);
            pp.push(offs[i] < 0, TickMath.getSqrtPriceAtTick(tick + offs[i]));
            // feed follows the pool: 1.0001^offset, NVDA token1 so tick up lowers the price
            int256 ans = answer0;
            uint256 w = 1e18;
            uint256 steps = uint256(int256(offs[i] < 0 ? -offs[i] : offs[i]));
            for (uint256 j; j < steps; j += 100) w = w * 1_010_050 / 1_000_000; // 1.0001^100 = 1.01005
            ans = offs[i] > 0 ? answer0 * 1e18 / int256(w) : answer0 * int256(w) / 1e18;
            (bool ok, uint256 g) = _runBatch(ans, 6_000_000);
            _gridRow(string.concat("pool+feed,tick", vm.toString(int256(offs[i]))), ans, ok, g);
            vm.revertToState(snap);
        }
    }

    // ------------------------------------------------------------------ 5. failed settlement
    /// @dev The market catches a failed vault call and returns zero: the batch can SUCCEED without settling. Reads must verify. The end state is still paused.
    function testSettlementFailureInsideTheBatchIsSilentAndEndsPaused() public {
        _operatorToTimelock();
        PairLedger memory before_ = V.ledger(PAIR);
        vm.mockCallRevert(address(R), abi.encodeWithSelector(ILiveReserve.available.selector), "RESERVE_DOWN");
        (bool ok, uint256 g) = _runBatch(answer0, 6_000_000);
        vm.clearMockedCalls();
        _stamp(answer0);
        _gridRow("settlement-call-failed", answer0, ok, g);
        assertTrue(ok, "the batch succeeds: the market swallowed the failure");
        _assertFlags(true, true, false);
        PairLedger memory l = V.ledger(PAIR);
        assertEq(l.usdgPrincipal, before_.usdgPrincipal, "nothing was settled, nothing was lost");
        assertEq(l.stockPrincipal, before_.stockPrincipal);
    }

    /// @dev Too little gas: the inner failure can be swallowed the same way. This is why an explicit gas limit and a state check are part of the procedure.
    function testLowGasLimitOutcomes() public {
        _operatorToTimelock();
        uint256 snap = vm.snapshotState();
        uint256[4] memory limits = [uint256(400_000), 800_000, 1_500_000, 3_000_000];
        for (uint256 i; i < limits.length; i++) {
            (bool ok, uint256 g) = _runBatch(answer0, limits[i]);
            _gridRow(string.concat("gasLimit", vm.toString(limits[i])), answer0, ok, g);
            vm.revertToState(snap);
        }
    }

    // ------------------------------------------------------------------ 6. no batch: the public window, tested
    /// @dev Without the operator change: timelock enables swaps; a window opens until the governor's sync and the guardian's re-pause.
    function testSequentialWindowInterveningCallsAndSafeStop() public {
        address[] memory t = new address[](1);
        bytes[] memory c = new bytes[](1);
        (t[0], c[0]) = (address(V), _pause(true, false, false));
        uint256[] memory v = _batch(c, t, keccak256("open"));
        _warp(T.getMinDelay());
        _stamp(answer0);
        vm.prank(GOV);
        T.executeBatch(t, v, c, bytes32(0), keccak256("open"));
        _assertFlags(true, false, false);
        _state("window-open");
        uint256 snap = vm.snapshotState();

        // keeper: new allocation is blocked, the checkpoint is not
        vm.prank(KEEPER);
        vm.expectRevert();
        V.rebalance(PAIR, vm.getBlockTimestamp());
        vm.prank(KEEPER);
        vm.expectRevert();
        V.recenter(PAIR, vm.getBlockTimestamp());
        vm.prank(KEEPER);
        V.checkpoint(PAIR, vm.getBlockTimestamp());

        // a public deposit into the market: the vault refuses the allocation, the market keeps the cash, the supplier is still credited
        deal(address(USDG), address(this), 5e6);
        USDG.approve(address(PU), type(uint256).max);
        uint256 prin = V.ledger(PAIR).usdgPrincipal;
        uint256 shares0 = PU.balanceOf(address(this));
        PU.mint(5e6);
        assertGt(PU.balanceOf(address(this)), shares0, "the supplier is credited");
        assertEq(V.ledger(PAIR).usdgPrincipal, prin, "nothing entered the vault");

        // an intervening public redeem above local cash reaches the settlement swap: anyone can trigger it inside the window
        uint256 lossBefore = V.ledger(PAIR).cumulativeLossUSDG;
        uint256 sh = PU.balanceOf(MARGIN_VAULT);
        uint256 b0 = USDG.balanceOf(MARGIN_VAULT);
        vm.prank(MARGIN_VAULT);
        PU.redeem{ gas: 8_000_000 }(sh);
        emit log_named_uint("window: intervening redeem received (USDG raw)", USDG.balanceOf(MARGIN_VAULT) - b0);
        _state("window-after-public-redeem");
        assertEq(V.ledger(PAIR).cumulativeLossUSDG, lossBefore, "no loss booked by the public redeem");

        // the operator's own sync finishes whatever remains
        vm.prank(GOV);
        (uint256 ret, uint256 loss) = PU.syncVault(type(uint256).max);
        emit log_named_uint("window: operator sync returned", ret);
        emit log_named_uint("window: operator sync realized loss", loss);
        vm.prank(GOV);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
        _state("window-closed");

        // SAFE STOP: if execution stops right after the window opens, the guardian alone closes it, no timelock needed
        vm.revertToState(snap);
        _assertFlags(true, false, false);
        vm.prank(GOV);
        V.setPairPause(PAIR, true, true, false);
        _assertFlags(true, true, false);
        _state("safe-stop");
    }

    /// @dev An adversary inside the window pushes the pool against the vault before the sync. The floor is the protection.
    function testAdversaryPushesThePoolInsideTheWindow() public {
        address[] memory t = new address[](1);
        bytes[] memory c = new bytes[](1);
        (t[0], c[0]) = (address(V), _pause(true, false, false));
        uint256[] memory v = _batch(c, t, keccak256("open"));
        _warp(T.getMinDelay());
        _stamp(answer0);
        vm.prank(GOV);
        T.executeBatch(t, v, c, bytes32(0), keccak256("open"));
        uint256 snap = vm.snapshotState();
        PoolKey memory k = A.poolKey(PAIR);
        int24[4] memory offs = [int24(30), 60, 100, 400]; // tick up = NVDA cheaper against USDG in the pool, so the vault's NVDA sale gets less
        for (uint256 i; i < offs.length; i++) {
            (, int24 tick,,) = IPoolManager(0x8366a39CC670B4001A1121B8F6A443A643e40951).getSlot0(k.toId());
            PoolPusher pp = new PoolPusher(k);
            deal(address(USDG), address(pp), 50_000_000e6);
            deal(address(NVDA), address(pp), 100_000e18);
            pp.push(false, TickMath.getSqrtPriceAtTick(tick + offs[i]));
            PairLedger memory b = V.ledger(PAIR);
            vm.prank(GOV);
            (uint256 ret, uint256 loss) = PU.syncVault{ gas: 6_000_000 }(type(uint256).max);
            PairLedger memory l = V.ledger(PAIR);
            emit log_string(string.concat("ADV,tick+", vm.toString(int256(offs[i])), ",returned=", vm.toString(ret), ",loss=", vm.toString(loss), ",prinUSDG=", vm.toString(l.usdgPrincipal), ",prinUSDGbefore=", vm.toString(b.usdgPrincipal),
                ",idleNVDA=", vm.toString(l.stockIdle), ",cumLoss=", vm.toString(l.cumulativeLossUSDG)));
            vm.revertToState(snap);
        }
    }

    // ------------------------------------------------------------------ 7. during the delay the keeper still runs if allocation was left open
    function testKeeperDuringTheDelayDoesNotBreakTheBatch() public {
        _operatorToTimelock();
        (address[] memory t, bytes[] memory c) = _atomicCalls();
        uint256[] memory v = _batch(c, t, keccak256("recovery"));
        _warp(T.getMinDelay());
        _stamp(answer0);
        vm.prank(KEEPER);
        V.checkpoint(PAIR, vm.getBlockTimestamp());
        vm.prank(KEEPER);
        try V.rebalance(PAIR, vm.getBlockTimestamp()) { emit log_string("keeper rebalance during delay: sent"); } catch { emit log_string("keeper rebalance during delay: refused"); }
        vm.prank(GOV);
        T.executeBatch{ gas: 6_000_000 }(t, v, c, bytes32(0), keccak256("recovery"));
        _assertFlags(true, true, false);
        assertEq(A.positionState(PAIR).liquidity, 0);
        assertGe(V.ledger(PAIR).usdgIdle, V.ledger(PAIR).usdgPrincipal);
    }

    /// @dev What follows a completed recovery: allocation stays paused until a SEPARATE timelock decision; the operator can be restored; nothing else depends on either.
    function testAfterRecoveryAllocationResumeIsSeparateAndOperatorCanBeRestored() public {
        _operatorToTimelock();
        (bool ok,) = _runBatch(answer0, 3_000_000);
        assertTrue(ok);
        _assertFlags(true, true, false);
        vm.prank(KEEPER);
        vm.expectRevert();
        V.rebalance(PAIR, vm.getBlockTimestamp());
        vm.prank(GOV);
        PU.queueSetVaultOperator(GOV);
        _warp(3600);
        _stamp(answer0);
        vm.prank(GOV);
        PU._setVaultOperator(GOV);
        assertEq(PU.vaultOperator(), GOV);
        vm.prank(address(T));
        V.setPairPause(PAIR, false, true, false);
        _assertFlags(false, true, false);
    }

    /// @dev The two delays can overlap: about one hour in total, not two.
    function testOperatorQueueAndBatchScheduleCanOverlap() public {
        uint256 t0 = vm.getBlockTimestamp();
        vm.prank(GOV);
        PU.queueSetVaultOperator(address(T));
        (address[] memory t, bytes[] memory c) = _atomicCalls();
        uint256[] memory v = _batch(c, t, keccak256("recovery"));
        _warp(3600);
        _stamp(answer0);
        vm.prank(GOV);
        PU._setVaultOperator(address(T));
        vm.prank(GOV);
        T.executeBatch{ gas: 3_000_000 }(t, v, c, bytes32(0), keccak256("recovery"));
        assertEq(vm.getBlockTimestamp() - t0, 3600);
        _assertFlags(true, true, false);
        assertEq(A.positionState(PAIR).liquidity, 0);
    }

    /// @dev A scheduled batch can be cancelled by the canceller while it waits.
    function testScheduledBatchCanBeCancelled() public {
        (address[] memory t, bytes[] memory c) = _atomicCalls();
        uint256[] memory v = _batch(c, t, keccak256("recovery"));
        bytes32 id = keccak256(abi.encode(t, v, c, bytes32(0), keccak256("recovery")));
        vm.prank(GOV);
        (bool ok,) = address(T).call(abi.encodeWithSignature("cancel(bytes32)", id));
        assertTrue(ok, "cancelled");
        _warp(3600);
        _stamp(answer0);
        vm.prank(GOV);
        vm.expectRevert();
        T.executeBatch(t, v, c, bytes32(0), keccak256("recovery"));
    }
}
