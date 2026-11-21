// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { AdapterForkTest } from "./AdapterFork.t.sol";
import { RamsesV3PairedAdapter } from "../../src/RamsesV3PairedAdapter.sol";
import { IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { RamsesVaultAdapter } from "../../src/RamsesVaultAdapter.sol";
import { PToken } from "peridot/PToken.sol";
import { IRewardRecovery } from "../mocks/IRewardRecovery.sol";

contract Phase5aAdapterForkTest is AdapterForkTest {
    function hasRole(bytes32 role, address who) external view returns (bool) {
        return role == keccak256("CONFIG_ROLE") && who == address(this);
    }

    function testPhase5aForkRewardListAndBrokenRamPrincipalExit() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        address[] memory tooMany = new address[](9);
        vm.mockCall(
            address(gauge),
            abi.encodeWithSelector(IRamsesGauge.getRewardTokens.selector),
            abi.encode(tooMany)
        );
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l / 2, ref, block.timestamp);
        vm.clearMockedCalls();
        vm.mockCallRevert(
            RAM,
            abi.encodeWithSelector(IERC20.balanceOf.selector, address(adapter)),
            "RAM balance restricted"
        );
        adapter.decreaseLiquidity(PAIR, l - l / 2, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
    }

    function testPhase5aForkRealRamExactClaimAndRestrictedRecipientRecovery() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        uint256 earned = gauge.earned(RAM, adapter.positionState(PAIR).tokenId);
        assertGt(earned, 0);
        adapter.decreaseLiquidity(PAIR, l / 2, ref, block.timestamp);
        assertEq(adapter.pendingRewards(PAIR, RAM), earned);
        vm.mockCallRevert(
            RAM,
            abi.encodeWithSelector(IERC20.transfer.selector, address(this)),
            "recipient restricted"
        );
        vm.expectRevert();
        adapter.claimPendingRewards(PAIR);
        IRewardRecovery(address(adapter)).recoverReward(PAIR, RAM, address(0xbeef), earned);
        assertEq(IERC20(RAM).balanceOf(address(0xbeef)), earned);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l - l / 2, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testPhase5aForkEarnedOneWeiDifferenceRollsBack() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        uint256 id = adapter.positionState(PAIR).tokenId;
        uint256 earned = gauge.earned(RAM, id);
        vm.mockCall(
            address(gauge),
            abi.encodeWithSelector(IRamsesGauge.earned.selector, RAM, id),
            abi.encode(earned + 1)
        );
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        adapter.harvest(PAIR, block.timestamp);
        assertEq(IERC20(RAM).balanceOf(address(adapter)), 0);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function _allocationEdge(int24 width, bool down) internal {
        adapter = new RamsesV3PairedAdapter(address(this), pm, oracle);
        adapter.registerPair(
            PAIR,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock), address(usdg), pool, gauge, 500, 300
            )
        );
        adapter.setRange(PAIR, center - width, center + width);
        stock.approve(address(adapter), type(uint256).max);
        usdg.approve(address(adapter), type(uint256).max);
        // USDG is token0. These are the two stock/USD oracle-band edges.
        uint160 edge = uint160(
            Math.mulDiv(ref, Math.sqrt(Math.mulDiv(10000, 1e36, down ? 9700 : 10300)), 1e18)
        );
        trader.move(!down, edge);
        (uint256 a0, uint256 a1) = VaultMath.amountsForLiquidity(
            edge,
            TickMath.getSqrtPriceAtTick(center - width),
            TickMath.getSqrtPriceAtTick(center + width),
            1e14
        );
        (uint256 r0, uint256 r1) = VaultMath.amountsForLiquidity(
            ref,
            TickMath.getSqrtPriceAtTick(center - width),
            TickMath.getSqrtPriceAtTick(center + width),
            1e14
        );
        uint256 ratio = Math.mulDiv(ref, ref, 1 << 64);
        uint256 cost = a0 + Math.mulDiv(a1, 1 << 128, ratio);
        uint256 value = r0 + Math.mulDiv(r1, 1 << 128, ratio);
        emit log_named_uint("oracle-marked return loss ppm", (cost - value) * 1e6 / cost);
        if ((cost - value) * 10000 > cost * 10) {
            vm.expectRevert(RamsesV3PairedAdapter.SlippageExceeded.selector);
            adapter.addLiquidity(PAIR, a1, a0, block.timestamp);
        } else {
            (uint256 usedS, uint256 usedU,) = adapter.addLiquidity(PAIR, a1, a0, block.timestamp);
            trader.move(down, ref);
            RamsesV3PairedAdapter.PositionState memory p = adapter.positionState(PAIR);
            uint256 actualCost = usedU + Math.mulDiv(usedS, 1 << 128, ratio);
            uint256 actualValue = p.usdgAmount + Math.mulDiv(p.stockAmount, 1 << 128, ratio);
            assertLe((actualCost - actualValue) * 10000, actualCost * 10);
        }
    }

    function testPhase5aForkWideAllocationUpper() public {
        _allocationEdge(2400, false);
    }

    function testPhase5aForkWideAllocationLower() public {
        _allocationEdge(2400, true);
    }

    function testPhase5aForkNarrowAllocationUpper() public {
        _allocationEdge(600, false);
    }

    function testPhase5aForkNarrowAllocationLower() public {
        _allocationEdge(600, true);
    }
}

