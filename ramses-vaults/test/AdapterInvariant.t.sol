// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { AdapterFixture } from "./Adapter.t.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";

abstract contract AdapterInvariantBase is AdapterFixture {
    uint256 public steps;
    uint256 public paidStock;
    uint256 public paidUsdg;

    function setUp() public override {
        super.setUp();
        bytes4[] memory selectors = new bytes4[](4);
        selectors[0] = this.actAdd.selector;
        selectors[1] = this.actRemove.selector;
        selectors[2] = this.actHarvest.selector;
        selectors[3] = this.actTime.selector;
        targetContract(address(this));
        targetSelector(FuzzSelector(address(this), selectors));
    }

    function actAdd(uint64 seed) external {
        _refresh();
        ++steps;
        uint256 s = bound(seed, 1e14, 1e17);
        uint256 u = 20e6;
        if (stock.balanceOf(address(this)) < s || usdg.balanceOf(address(this)) < u) return;
        adapter.addLiquidity(PAIR, s, u, block.timestamp);
    }

    function actRemove(uint64 seed) external {
        _refresh();
        ++steps;
        RamsesV3PairedAdapter.PositionState memory state = adapter.positionState(PAIR);
        if (state.liquidity == 0) return;
        uint128 l = uint128(bound(seed, 1, state.liquidity));
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        if (l == state.liquidity) adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function actHarvest() external {
        ++steps;
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        adapter.harvest(PAIR, block.timestamp);
        paidStock += stock.balanceOf(address(this)) - s;
        paidUsdg += usdg.balanceOf(address(this)) - u;
    }

    function actTime(uint32 seed) external {
        vm.warp(vm.getBlockTimestamp() + bound(seed, 0, 2 days));
    }

    function invariant_rewardsAreBackedAndSeparated() public view {
        assertEq(stock.balanceOf(address(adapter)), adapter.pendingRewards(PAIR, address(stock)));
        assertEq(usdg.balanceOf(address(adapter)), adapter.pendingRewards(PAIR, address(usdg)));
        assertEq(ram.balanceOf(address(adapter)), adapter.pendingRewards(PAIR, address(ram)));
    }

    function invariant_nativePrincipalConservedWithinRounding() public view {
        RamsesV3PairedAdapter.PositionState memory state = adapter.positionStateAt(PAIR, ref);
        assertApproxEqAbs(
            stock.balanceOf(address(this)) + state.stockAmount, 100e18 + paidStock, 2 * steps + 1
        );
        assertApproxEqAbs(
            usdg.balanceOf(address(this)) + state.usdgAmount, 100_000e6 + paidUsdg, 2 * steps + 1
        );
    }
}

contract AdapterStock0Invariant is AdapterInvariantBase {
    function stockFirst() internal pure override returns (bool) {
        return true;
    }
}

contract AdapterUsdg0Invariant is AdapterInvariantBase {
    function stockFirst() internal pure override returns (bool) {
        return false;
    }
}
