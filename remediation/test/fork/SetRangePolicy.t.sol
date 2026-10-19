// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IPoolManager } from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";
import { StateLibrary } from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import { PoolIdLibrary } from "@uniswap/v4-core/src/types/PoolId.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RobinhoodBoostedVaultV3 } from "../../src/RobinhoodBoostedVaultV3.sol";
import { UniswapV4PairedAdapterV3 } from "../../src/UniswapV4PairedAdapterV3.sol";
import {
    QueueRangePolicy,
    ExecuteRangePolicy,
    CancelRangePolicy
} from "../../script/SetRangePolicy.s.sol";
import { CompatPoolShock, IGuardPrices } from "./LendingDelegateMarginCompat.t.sol";

/// @notice The range-policy scripts run exactly as the governor runs them, on a fork of live state
/// (V3 already installed), then a real recenter picks up the new width.
contract SetRangePolicyForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
    address constant POOL_MANAGER = 0x8366a39CC670B4001A1121B8F6A443A643e40951;
    address constant USD = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    bytes32 constant PAIR = keccak256("NVDA/USDG");

    RobinhoodBoostedVaultV3 internal vault = RobinhoodBoostedVaultV3(VAULT);
    UniswapV4PairedAdapterV3 internal adapter = UniswapV4PairedAdapterV3(ADAPTER);

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        try IGuardPrices(GUARD).pricesUSD18(PAIR) returns (uint256 s, uint256 u) {
            if (s == 0 || u == 0) vm.skip(true);
        } catch {
            vm.skip(true);
        }
    }

    function _delay() internal view returns (uint256) {
        return TimelockController(payable(TIMELOCK)).getMinDelay();
    }

    function testQueueExecuteThenRecenterUsesNewWidth() public {
        bytes32 ledgerBefore = keccak256(abi.encode(vault.ledger(PAIR)));
        bytes32 configBefore = keccak256(abi.encode(vault.pairConfig(PAIR)));
        (,,, uint32 iv, uint8 pd, uint16 lb, uint128 cap) = vault.rangePolicy(PAIR);
        (int24 lowerBefore, int24 upperBefore, bool rangedBefore) = adapter.positionTicks(PAIR);
        assertTrue(rangedBefore);
        uint128 liquidityBefore = adapter.positionState(PAIR).liquidity;

        QueueRangePolicy queue = new QueueRangePolicy();
        queue.run();
        uint256 queuedAt = vm.getBlockTimestamp();
        vm.expectRevert("ALREADY_QUEUED");
        queue.run();

        // Queued only: the policy and the position are untouched.
        (, uint16 half0, uint16 trig0,,,,) = vault.rangePolicy(PAIR);
        assertEq(half0, 1200);
        assertEq(trig0, 300);
        (int24 l0, int24 u0,) = adapter.positionTicks(PAIR);
        assertEq(l0, lowerBefore);
        assertEq(u0, upperBefore);

        ExecuteRangePolicy exec = new ExecuteRangePolicy();
        vm.warp(queuedAt + _delay() - 1);
        vm.expectRevert("TIMELOCK_NOT_READY");
        exec.run();
        vm.warp(queuedAt + _delay());
        exec.run();

        (
            bool enabled,
            uint16 half,
            uint16 trigger,
            uint32 iv1,
            uint8 pd1,
            uint16 lb1,
            uint128 cap1
        ) = vault.rangePolicy(PAIR);
        assertTrue(enabled);
        assertEq(half, 2400);
        assertEq(trigger, 600);
        assertEq(iv1, iv);
        assertEq(pd1, pd);
        assertEq(lb1, lb);
        assertEq(cap1, cap);
        // Policy change moves nothing by itself.
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerBefore);
        assertEq(keccak256(abi.encode(vault.pairConfig(PAIR))), configBefore);
        (int24 l1, int24 u1,) = adapter.positionTicks(PAIR);
        assertEq(l1, lowerBefore);
        assertEq(u1, upperBefore);
        assertEq(adapter.positionState(PAIR).liquidity, liquidityBefore);

        // An executed operation cannot run twice, and the old-policy guard rejects a replay.
        vm.expectRevert("POLICY_ALREADY_CHANGED");
        exec.run();

        // The next recenter builds the wider band. Local conveniences: a funded mover pushes the pool
        // past the NEW 600-tick trigger and the feed answer tracks it; everything else is real.
        PoolKey memory key = IUniswapV4PairedAdapter(ADAPTER).poolKey(PAIR);
        _shock(key, 10_800); // +8% is about 770 ticks: past 600, inside the 2400 range
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _followFeed(key);
        vm.prank(GOVERNOR);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
        (int24 l2, int24 u2,) = adapter.positionTicks(PAIR);
        assertEq(int256(u2) - int256(l2), 2 * 2400 + 60, "new width");
        assertGt(adapter.positionState(PAIR).liquidity, 0);
        (, int24 tick,,) =
            StateLibrary.getSlot0(IPoolManager(POOL_MANAGER), PoolIdLibrary.toId(key));
        assertTrue(tick > l2 && tick < u2, "price inside the new range");
        emit log_named_int("new lower", l2);
        emit log_named_int("new upper", u2);
    }

    function _shock(PoolKey memory key, uint256 priceBps) internal {
        IPoolManager manager = IPoolManager(POOL_MANAGER);
        (uint160 current,,,) = StateLibrary.getSlot0(manager, PoolIdLibrary.toId(key));
        uint160 target = uint160(Math.mulDiv(current, 1e11, Math.sqrt(priceBps * 1e18)));
        CompatPoolShock mover = new CompatPoolShock(manager);
        deal(USD, address(mover), 10_000_000e6);
        deal(STOCK, address(mover), 100_000e18);
        bool zeroForOne = target < current;
        mover.move(key, target, zeroForOne, zeroForOne ? 10_000_000e6 : 100_000e18);
        _followFeed(key);
    }

    function _followFeed(PoolKey memory key) internal {
        (, int24 tick,,) =
            StateLibrary.getSlot0(IPoolManager(POOL_MANAGER), PoolIdLibrary.toId(key));
        uint256 answer = VaultMath.quoteAtTick(tick, 1e18, STOCK, USD) * 100;
        vm.mockCall(
            FEED,
            abi.encodeCall(IAggregatorV3.latestRoundData, ()),
            abi.encode(uint80(1), int256(answer), block.timestamp, block.timestamp, uint80(1))
        );
    }

    function testCancelBeforeExecution() public {
        QueueRangePolicy queue = new QueueRangePolicy();
        CancelRangePolicy cancel = new CancelRangePolicy();
        ExecuteRangePolicy exec = new ExecuteRangePolicy();
        queue.run();
        cancel.run();
        vm.warp(vm.getBlockTimestamp() + _delay());
        vm.expectRevert("TIMELOCK_NOT_READY");
        exec.run();
        (, uint16 half,,,,,) = vault.rangePolicy(PAIR);
        assertEq(half, 1200);
    }
}
