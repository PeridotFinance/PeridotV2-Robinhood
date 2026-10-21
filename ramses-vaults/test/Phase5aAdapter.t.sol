// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { AdapterFixture } from "./Adapter.t.sol";
import { TokenDouble, PoolDouble, GaugeDouble } from "./mocks/RamsesV3Double.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { IRamsesNfp, IRamsesPool, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";

interface IRewardRecovery {
    function recoverReward(bytes32 id, address token, address receiver, uint256 amount) external;
}

abstract contract Phase5aAdapterBase is AdapterFixture {
    function hasRole(bytes32 role, address who) external view returns (bool) {
        return role == keccak256("CONFIG_ROLE") && who == address(this);
    }

    function testPhase5aRewardLifetimeLimitExits() public {
        uint128 l = _add();
        for (uint256 i; i < 5; i++) {
            address[] memory ts = new address[](1);
            ts[0] = address(new TokenDouble("reward", 18));
            gauge.setTokens(ts);
            adapter.harvest(PAIR, block.timestamp);
        }
        assertEq(adapter.rewardTokens(PAIR).length, 8);
        address[] memory ninth = new address[](1);
        ninth[0] = address(new TokenDouble("ninth", 18));
        gauge.setTokens(ninth);
        vm.expectRevert(RamsesV3PairedAdapter.RewardSetChanged.selector);
        adapter.harvest(PAIR, block.timestamp);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
    }

    function testPhase5aOversizedLiveListExits() public {
        uint128 l = _add();
        gauge.setTokens(new address[](9));
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testPhase5aBrokenBalanceOfExits() public {
        uint128 l = _add();
        vm.mockCallRevert(address(ram), abi.encodeWithSelector(IERC20.balanceOf.selector), "broken");
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testPhase5aRewardViewRoundingFailsAtomicallyButExits() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        pool._advancePeriod();
        uint256 earned = gauge.earned(address(ram), 1);
        vm.mockCall(
            address(gauge),
            abi.encodeWithSignature("earned(address,uint256)", address(ram), 1),
            abi.encode(earned + 1)
        );
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        adapter.harvest(PAIR, block.timestamp);
        assertEq(ram.balanceOf(address(adapter)), 0);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testPhase5aRestrictedTransferRecoveryAndPrincipalExclusion() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        adapter.decreaseLiquidity(PAIR, l / 2, ref, block.timestamp);
        uint256 pending = adapter.pendingRewards(PAIR, address(ram));
        assertGt(pending, 0);
        vm.mockCallRevert(
            address(ram),
            abi.encodeWithSelector(IERC20.transfer.selector, address(this)),
            "restricted recipient"
        );
        vm.expectRevert();
        adapter.claimPendingRewards(PAIR);
        vm.prank(address(7));
        vm.expectRevert();
        IRewardRecovery(address(adapter)).recoverReward(PAIR, address(ram), address(8), pending);
        IRewardRecovery(address(adapter)).recoverReward(PAIR, address(ram), address(8), pending);
        assertEq(ram.balanceOf(address(8)), pending);
        assertEq(adapter.pendingRewards(PAIR, address(ram)), 0);
        vm.expectRevert();
        IRewardRecovery(address(adapter)).recoverReward(PAIR, address(stock), address(8), 1);
        vm.expectRevert();
        IRewardRecovery(address(adapter)).recoverReward(PAIR, address(usdg), address(8), 1);
        vm.expectRevert();
        IRewardRecovery(address(adapter)).recoverReward(PAIR, address(pm), address(8), 1);
        // An escrowed/vested variant sent outside the gauge's advertised set is recoverable too.
        TokenDouble escrow = new TokenDouble("escrow", 18);
        escrow.mint(address(adapter), 7e18);
        IRewardRecovery(address(adapter)).recoverReward(PAIR, address(escrow), address(8), 7e18);
        assertEq(escrow.balanceOf(address(8)), 7e18);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l - l / 2, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testPhase5aAlternatePayoutTokenClaimRollsBackAndExits() public {
        uint128 l=_add();
        TokenDouble escrow=new TokenDouble("escrow",18);
        escrow.mint(address(gauge),1000e18);
        gauge.setPayoutToken(address(ram),address(escrow));
        vm.warp(vm.getBlockTimestamp()+1 hours);
        vm.expectRevert(RamsesV3PairedAdapter.RewardClaimMismatch.selector);
        adapter.harvest(PAIR,block.timestamp);
        assertEq(escrow.balanceOf(address(adapter)),0);
        adapter.setAllowRewardForfeiture(PAIR,true);
        adapter.decreaseLiquidity(PAIR,l,ref,block.timestamp);
        adapter.burnEmptyPosition(PAIR,block.timestamp);
        assertEq(escrow.balanceOf(address(gauge)),1000e18);
    }

    function testPhase5aRecoveryCannotSpendAnotherPairsPendingRewards() public {
        uint128 l=_add(); vm.warp(vm.getBlockTimestamp()+1 hours);
        adapter.decreaseLiquidity(PAIR,l/2,ref,block.timestamp);
        uint256 pending=adapter.pendingRewards(PAIR,address(ram));
        assertGt(pending,0);
        PoolDouble other=new PoolDouble(pool.token0(),pool.token1(),address(factory),center);
        GaugeDouble g=new GaugeDouble(other,pm,new address[](0));
        factory.set(address(other),address(g));
        RamsesV3PairedAdapter.RegisterPairParams memory p=_params();
        p.pool=IRamsesPool(address(other)); p.gauge=IRamsesGauge(address(g));
        bytes32 second=keccak256("second"); adapter.registerPair(second,p);
        vm.expectRevert(); IRewardRecovery(address(adapter)).recoverReward(second,address(ram),address(8),pending);
        ram.mint(address(adapter),1e18); // Only uncredited excess is available through the other pair.
        IRewardRecovery(address(adapter)).recoverReward(second,address(ram),address(8),1e18);
        assertEq(adapter.totalPendingRewards(address(ram)),pending);
        assertEq(adapter.pendingRewards(PAIR,address(ram)),pending);
        adapter.claimPendingRewards(PAIR);
        assertEq(adapter.totalPendingRewards(address(ram)),0);
    }

    function _allocation(int24 width, int24 move) internal {
        adapter = new RamsesV3PairedAdapter(address(this), IRamsesNfp(address(pm)), oracle);
        RamsesV3PairedAdapter.RegisterPairParams memory p = _params();
        p.allocationDeviationBps = 300;
        adapter.registerPair(PAIR, p);
        adapter.setRange(PAIR, center - width, center + width);
        stock.approve(address(adapter), type(uint256).max);
        usdg.approve(address(adapter), type(uint256).max);
        pool.setTick(center + move);
        uint128 liquidity = 1e14;
        (uint256 a0, uint256 a1) = VaultMath.amountsForLiquidity(
            TickMath.getSqrtPriceAtTick(center + move),
            TickMath.getSqrtPriceAtTick(center - width),
            TickMath.getSqrtPriceAtTick(center + width),
            liquidity
        );
        (uint256 r0, uint256 r1) = VaultMath.amountsForLiquidity(
            ref,
            TickMath.getSqrtPriceAtTick(center - width),
            TickMath.getSqrtPriceAtTick(center + width),
            liquidity
        );
        uint256 cost = a1 + Math.mulDiv(a0, Math.mulDiv(ref, ref, 1 << 64), 1 << 128);
        uint256 value = r1 + Math.mulDiv(r0, Math.mulDiv(ref, ref, 1 << 64), 1 << 128);
        emit log_named_uint("loss in millionths of allocation", (cost - value) * 1e6 / cost);
        if ((cost - value) * 10000 > cost * 10) {
            vm.expectRevert(RamsesV3PairedAdapter.SlippageExceeded.selector);
        }
        adapter.addLiquidity(PAIR, stockFirst() ? a0 : a1, stockFirst() ? a1 : a0, block.timestamp);
    }

    function testPhase5aAllocationWideUpper() public {
        _allocation(2400, 295);
    }

    function testPhase5aAllocationWideLower() public {
        _allocation(2400, -304);
    }

    function testPhase5aAllocationNarrowUpper() public {
        _allocation(600, 295);
    }

    function testPhase5aAllocationNarrowLower() public {
        _allocation(600, -304);
    }

    function testPhase5aAllocationNarrowSafe() public {
        _allocation(600, 100);
    }
}

contract Phase5aAdapterStock0Test is Phase5aAdapterBase {
    function stockFirst() internal pure override returns (bool) {
        return true;
    }
}

contract Phase5aAdapterUsdg0Test is Phase5aAdapterBase {
    function stockFirst() internal pure override returns (bool) {
        return false;
    }
}
