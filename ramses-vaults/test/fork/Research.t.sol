// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";

contract ResearchTest is Test {
    IRamsesPool constant pool = IRamsesPool(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b);
    IRamsesNfp constant pm = IRamsesNfp(0x2eBd7B85a4E08D5B508b04BA147976C94afE6590);
    IRamsesGauge constant gauge = IRamsesGauge(0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce);

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
    }

    function test_contractOwnershipAndRewardRoutes() public {
        address t0 = pool.token0();
        address t1 = pool.token1();
        deal(t0, address(this), 100e6);
        deal(t1, address(this), 1e18);
        IERC20(t0).approve(address(pm), 100e6);
        IERC20(t1).approve(address(pm), 1e18);
        (, int24 tick,,,,,) = pool.slot0();
        int24 center = tick / 10 * 10;
        (uint256 id, uint128 liq,,) = pm.mint(
            IRamsesNfp.MintParams(
                t0, t1, 10, center - 100, center + 100, 100e6, 1e18, 0, 0, address(this), block.timestamp
            )
        );
        assertEq(pm.ownerOf(id), address(this));
        assertGt(liq, 0);
        emit log_named_uint("tokenId", id);
        emit log_named_uint("liquidity", liq);
        address[] memory tokens = gauge.getRewardTokens();
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        for (uint256 i; i < tokens.length; i++) {
            emit log_named_address("reward", tokens[i]);
            emit log_named_uint("rate", gauge.rewardRate(tokens[i]));
            emit log_named_uint("earned", gauge.earned(tokens[i], id));
        }
        (bool direct, bytes memory reason) =
            address(gauge).call(abi.encodeCall(gauge.getReward, (id, tokens)));
        emit log_named_uint("direct success", direct ? 1 : 0);
        emit log_bytes(reason);
        assertFalse(direct);
        assertFalse(gauge.isAuthorizedClaimer(address(this)));
        assertTrue(gauge.isAuthorizedClaimer(address(pm)));
        assertTrue(gauge.isAuthorizedClaimer(gauge.voter()));
        uint256 expected = gauge.earned(tokens[2], id);
        assertGt(expected, 0);
        uint256 beforeRam = IERC20(tokens[2]).balanceOf(address(this));
        pm.getReward(id, tokens);
        assertEq(IERC20(tokens[2]).balanceOf(address(this)) - beforeRam, expected);
        emit log_named_uint(
            "manager RAM paid", IERC20(tokens[2]).balanceOf(address(this)) - beforeRam
        );
    }
}
