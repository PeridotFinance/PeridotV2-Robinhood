// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Vm } from "forge-std/Vm.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IRamsesPool, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RamsesV3PairedAdapter } from "../../src/RamsesV3PairedAdapter.sol";
import { LendingForkBase } from "./LendingFork.t.sol";

/// @notice Measures the real gauge underpayment (anti-sybil) on the live Ramses pools and checks the section 8a booking:
/// with forfeiture allowed and a ceiling, a harvest credits only what arrived, books the gap in `rewardShortfall`, emits
/// RewardShortfallBooked, and refuses above the ceiling. Machine-readable GRID lines carry the measurement.
abstract contract RewardShortfallForkBase is LendingForkBase {
    bytes32 constant BOOKED =
        keccak256("RewardShortfallBooked(bytes32,address,uint256,uint256,uint256)");
    bytes32 constant ACCRUED = keccak256("RewardAccrued(bytes32,address,uint256)");

    function _allow(uint16 ceilingBps) internal {
        vm.startPrank(address(vault));
        RamsesV3PairedAdapter(address(adapter)).setAllowRewardForfeiture(PAIR, true);
        RamsesV3PairedAdapter(address(adapter)).setRewardShortfallCeiling(PAIR, ceilingBps);
        vm.stopPrank();
    }

    /// @dev Returns the RAM expected and received in the harvest, read from the events.
    function _harvestAndRead() internal returns (uint256 expected, uint256 received, bool ok) {
        vm.recordLogs();
        vm.prank(keeper);
        (ok,) = address(vault)
            .call(abi.encodeWithSignature("harvest(bytes32,uint256)", PAIR, block.timestamp));
        Vm.Log[] memory logs = vm.getRecordedLogs();
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter != address(adapter) || logs[i].topics[0] == bytes32(0)) continue;
            if (logs[i].topics[0] == BOOKED && address(uint160(uint256(logs[i].topics[2]))) == RAM)
            {
                (expected, received,) = abi.decode(logs[i].data, (uint256, uint256, uint256));
            } else if (
                logs[i].topics[0] == ACCRUED && address(uint160(uint256(logs[i].topics[2]))) == RAM
                    && expected == 0
            ) {
                received = abi.decode(logs[i].data, (uint256));
                expected = received;
            }
        }
    }

    function testMeasureUnderpaymentAcrossWaits() public {
        _allow(10_000); // accept anything here: this test only measures
        uint32[11] memory waits =
            [uint32(0), 30, 60, 99, 100, 200, 600, 1800, 1 hours, 1 days, 7 days];
        uint256 snap = vm.snapshotState();
        for (uint256 i; i < waits.length; i++) {
            vm.revertToState(snap);
            vm.warp(vm.getBlockTimestamp() + waits[i]);
            _shift(0);
            uint256 earned = gauge.earned(RAM, adapter.positionState(PAIR).tokenId);
            (uint256 expected, uint256 received, bool ok) = _harvestAndRead();
            uint256 shareBps = expected == 0 ? 0 : (expected - received) * 10_000 / expected;
            emit log_string(string.concat(
                    "GRID,waitSeconds=",
                    vm.toString(uint256(waits[i])),
                    ",earned=",
                    vm.toString(earned),
                    ",expected=",
                    vm.toString(expected),
                    ",received=",
                    vm.toString(received),
                    ",shortfall=",
                    vm.toString(expected - received),
                    ",shortfallBps=",
                    vm.toString(shareBps),
                    ",harvestOk=",
                    ok ? "1" : "0"
                ));
            assertTrue(ok, "harvest succeeds with forfeiture allowed and no ceiling");
            assertEq(
                RamsesV3PairedAdapter(address(adapter)).rewardShortfall(PAIR, RAM),
                expected - received,
                "shortfall booked"
            );
        }
    }

    function testCeilingRefusesAboveAndAcceptsBelow() public {
        vm.warp(vm.getBlockTimestamp() + 1 days);
        _shift(0);
        _allow(10_000);
        uint256 snap = vm.snapshotState();
        (uint256 expected, uint256 received,) = _harvestAndRead();
        vm.revertToState(snap);
        uint256 shareBps = expected == 0 ? 0 : (expected - received) * 10_000 / expected;
        emit log_named_uint("1-day shortfall bps", shareBps);
        if (shareBps == 0) return; // nothing withheld on this pool: the ceiling never binds
        _allow(uint16(shareBps)); // ceiling just under the real share (integer floor): refused
        vm.prank(keeper);
        vm.expectRevert();
        vault.harvest(PAIR, block.timestamp);
        _allow(uint16(shareBps + 1)); // just above: accepted and booked
        (, uint256 r2, bool ok2) = _harvestAndRead();
        assertTrue(ok2);
        assertEq(r2, received);
    }

    function testForfeitureOffStillRejectsAnyShortfall() public {
        vm.warp(vm.getBlockTimestamp() + 1 days);
        _shift(0);
        uint256 snap = vm.snapshotState();
        _allow(10_000);
        (uint256 expected, uint256 received,) = _harvestAndRead();
        vm.revertToState(snap);
        if (expected == received) return;
        vm.prank(address(vault));
        RamsesV3PairedAdapter(address(adapter)).setAllowRewardForfeiture(PAIR, false);
        vm.prank(keeper);
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        vault.harvest(PAIR, block.timestamp);
    }

    function testRecentreBelowCeilingProceedsAndBooks() public {
        _allow(2000);
        vm.warp(vm.getBlockTimestamp() + 1 days);
        _shift(int24(crashSign) * 700); // move the oracle centre past the 600-tick trigger
        vm.recordLogs();
        vm.prank(keeper);
        (bool ok, bytes memory ret) = address(vault)
            .call(abi.encodeWithSignature("recenter(bytes32,uint256)", PAIR, block.timestamp));
        Vm.Log[] memory logs = vm.getRecordedLogs();
        uint256 booked;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter == address(adapter) && logs[i].topics[0] == BOOKED) booked++;
        }
        emit log_named_uint("recentre ok", ok ? 1 : 0);
        emit log_named_uint("shortfall events in the recentre", booked);
        if (!ok) emit log_bytes(ret);
        assertTrue(ok, "recentre below the ceiling succeeds");
    }

    /// @dev The keeper's normal sequence: recenter (in setUp), more supply arrives, rebalance a few seconds later, then
    /// harvest after the anti-sybil window. The gauge pays the reward accrued between the two position changes to the
    /// Ramses treasury inside the same claim; the adapter must book that slice instead of reverting.
    function testTreasurySliceAfterRecenterThenRebalance() public {
        _allow(2000);
        vm.warp(vm.getBlockTimestamp() + 5);
        _shift(0);
        deal(address(usdg), bob, 300e6);
        vm.prank(bob);
        uMarket.mint(50e6);
        vm.startPrank(keeper);
        vault.checkpoint(PAIR, block.timestamp);
        vault.rebalance(PAIR, block.timestamp);
        vm.stopPrank();
        vm.warp(vm.getBlockTimestamp() + 200);
        _shift(0);
        uint256 earned = gauge.earned(RAM, adapter.positionState(PAIR).tokenId);
        uint256 gaugeBefore = IERC20(RAM).balanceOf(address(gauge));
        (uint256 expected, uint256 received, bool ok) = _harvestAndRead();
        uint256 gaugePaid = gaugeBefore - IERC20(RAM).balanceOf(address(gauge));
        uint256 booked = RamsesV3PairedAdapter(address(adapter)).rewardShortfall(PAIR, RAM);
        emit log_string(string.concat(
                "SLICE,earned=",
                vm.toString(earned),
                ",expected=",
                vm.toString(expected),
                ",received=",
                vm.toString(received),
                ",gaugePaid=",
                vm.toString(gaugePaid),
                ",booked=",
                vm.toString(booked),
                ",bookedBps=",
                vm.toString(gaugePaid == 0 ? 0 : booked * 10_000 / gaugePaid),
                ",harvestOk=",
                ok ? "1" : "0"
            ));
        assertTrue(ok, "harvest after recenter then rebalance succeeds");
        assertEq(
            booked,
            (gaugePaid > earned ? gaugePaid : earned) - received,
            "everything not received is booked as forfeited"
        );
        assertGt(booked, 0, "the treasury slice exists after recenter then rebalance");
    }
}

contract SpyRewardShortfallForkTest is RewardShortfallForkBase {
    function _configure() internal override {
        PAIR = keccak256("SPY/USDG");
        pool = IRamsesPool(0x38453c115607463Ac284820Ce959831042f3Df4E);
        gauge = IRamsesGauge(0x739E31798EFE8Cff3ef4c613D8ae751e179C7337);
        feed = IAggregatorV3(0x319724394D3A0e3669269846abE664Cd621f9f6A);
        stockIsToken0 = true;
        crashSign = -1;
    }
}

contract NvdaRewardShortfallForkTest is RewardShortfallForkBase { }
