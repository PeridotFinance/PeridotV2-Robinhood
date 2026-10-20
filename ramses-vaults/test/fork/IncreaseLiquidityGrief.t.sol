// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IRamsesPool, IRamsesNfp } from "../../src/interfaces/IRamsesV3.sol";

/// The adapter's `_validatePosition` requires the NFT's liquidity to equal its own record. That is only
/// safe while nobody else can add liquidity to the vault's NFT. An independent review raised the Uniswap-v3
/// behaviour (anyone may `increaseLiquidity`); the real Ramses manager enforces ERC721 approval instead.
/// This pins that assumption on the live manager.
contract IncreaseLiquidityGriefTest is Test {
    IRamsesPool constant pool = IRamsesPool(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b);
    IRamsesNfp constant pm = IRamsesNfp(0x2eBd7B85a4E08D5B508b04BA147976C94afE6590);

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
    }

    function test_nonOwnerCannotIncreaseLiquidity() public {
        address owner = address(0xA11CE);
        address attacker = address(0xBAD);
        address t0 = pool.token0();
        address t1 = pool.token1();
        deal(t0, owner, 100e6);
        deal(t1, owner, 1e18);
        deal(t0, attacker, 10e6);
        deal(t1, attacker, 1e17);
        (, int24 tick,,,,,) = pool.slot0();
        int24 center = tick / 10 * 10;
        vm.startPrank(owner);
        IERC20(t0).approve(address(pm), 100e6);
        IERC20(t1).approve(address(pm), 1e18);
        (uint256 id, uint128 liq,,) = pm.mint(
            IRamsesNfp.MintParams(
                t0, t1, 10, center - 100, center + 100, 100e6, 1e18, 0, 0, owner, block.timestamp
            )
        );
        vm.stopPrank();
        uint128 before = pm.positions(id).liquidity;
        vm.startPrank(attacker);
        IERC20(t0).approve(address(pm), 10e6);
        IERC20(t1).approve(address(pm), 1e17);
        (bool ok, bytes memory reason) = address(pm)
            .call(
                abi.encodeCall(
                    pm.increaseLiquidity,
                    (IRamsesNfp.IncreaseLiquidityParams(id, 1e6, 1e15, 0, 0, block.timestamp))
                )
            );
        vm.stopPrank();
        uint128 afterLiq = pm.positions(id).liquidity;
        emit log_named_uint("increase by non-owner succeeded", ok ? 1 : 0);
        emit log_named_uint("liquidity before", before);
        emit log_named_uint("liquidity after", afterLiq);
        if (!ok) emit log_bytes(reason);
        assertEq(before, liq);
        assertFalse(ok, "non-owner increase must fail");
        assertEq(afterLiq, before);
        // ERC721InsufficientApproval(operator, tokenId)
        assertEq(bytes4(reason), bytes4(0x177e802f));
    }
}
