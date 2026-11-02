// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { SqrtPriceMath } from "@uniswap/v4-core/src/libraries/SqrtPriceMath.sol";
import { IRamsesPool } from "../../src/interfaces/IRamsesV3.sol";

/// @notice Handoff 01 item 6. Walks the initialized ticks around the current price of a real pool at the pinned
/// block and integrates the exact input needed to move the price by 1%, 2% and 5% in each direction (constant
/// liquidity between initialized ticks, exactly like the pool). It also emits every initialized tick with its
/// liquidityNet so a Python script can cross-check them against the Mint/Burn history.
///
/// Outputs are machine-readable lines starting with "DEPTH," and "TICK,".
abstract contract GroupDepthBase is Test {
    function poolAddr() internal pure virtual returns (address);
    function label() internal pure virtual returns (string memory);

    int24 constant WINDOW = 700; // ticks each side (about 7%)

    IRamsesPool pool;
    int24 spacing;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        pool = IRamsesPool(poolAddr());
        spacing = pool.tickSpacing();
    }

    function _net(int24 t) internal view returns (int128 net) {
        (bool ok, bytes memory data) =
            address(pool).staticcall(abi.encodeWithSignature("ticks(int24)", t));
        require(ok && data.length >= 64, "ticks()");
        uint256 gross;
        assembly {
            gross := mload(add(data, 32))
            net := mload(add(data, 64))
        }
        require(gross > 0, "uninitialized tick has no gross");
    }

    function _scan(int24 from, int24 to)
        internal
        view
        returns (int24[] memory ticks, int128[] memory nets, uint256 n)
    {
        ticks = new int24[](4096);
        nets = new int128[](4096);
        int24 cLo = _compress(from);
        int24 cHi = _compress(to);
        int16 wLo = int16(cLo >> 8);
        int16 wHi = int16(cHi >> 8);
        for (int16 w = wLo; w <= wHi; w++) {
            uint256 word = IBitmapD(address(pool)).tickBitmap(w);
            if (word == 0) continue;
            for (uint256 b = 0; b < 256; b++) {
                if ((word >> b) & 1 == 0) continue;
                int24 t = (int24(w) * 256 + int24(uint24(b))) * spacing;
                if (t < from || t > to) continue;
                ticks[n] = t;
                nets[n] = _net(t);
                n++;
            }
        }
    }

    function _compress(int24 t) internal view returns (int24 c) {
        c = t / spacing;
        if (t < 0 && t % spacing != 0) c--;
    }

    /// @dev amount of the input token needed to move the sqrt price from `s` to `target` (either direction), plus the
    /// output token obtained, walking the initialized ticks. Constant liquidity inside each interval.
    function _walk(
        uint160 s,
        int24 tick,
        uint128 liq,
        uint160 target,
        bool down,
        int24[] memory ticks,
        int128[] memory nets,
        uint256 n
    ) internal pure returns (uint256 amountIn, uint256 amountOut) {
        // collect ticks in walking order
        uint160 cur = s;
        uint128 L = liq;
        if (down) {
            // ticks at or below current tick, descending; crossing tick t subtracts liquidityNet
            for (uint256 k = n; k > 0; k--) {
                int24 t = ticks[k - 1];
                if (t > tick) continue;
                uint160 p = TickMath.getSqrtPriceAtTick(t);
                if (p <= target) break;
                if (p < cur) {
                    amountIn += SqrtPriceMath.getAmount0Delta(p, cur, L, true);
                    amountOut += SqrtPriceMath.getAmount1Delta(p, cur, L, false);
                    cur = p;
                }
                int128 net = nets[k - 1];
                L = net < 0 ? L + uint128(-net) : L - uint128(net);
            }
            if (cur > target) {
                amountIn += SqrtPriceMath.getAmount0Delta(target, cur, L, true);
                amountOut += SqrtPriceMath.getAmount1Delta(target, cur, L, false);
            }
        } else {
            for (uint256 k = 0; k < n; k++) {
                int24 t = ticks[k];
                if (t <= tick) continue;
                uint160 p = TickMath.getSqrtPriceAtTick(t);
                if (p >= target) break;
                if (p > cur) {
                    amountIn += SqrtPriceMath.getAmount1Delta(cur, p, L, true);
                    amountOut += SqrtPriceMath.getAmount0Delta(cur, p, L, false);
                    cur = p;
                }
                int128 net = nets[k];
                L = net < 0 ? L - uint128(-net) : L + uint128(net);
            }
            if (cur < target) {
                amountIn += SqrtPriceMath.getAmount1Delta(cur, target, L, true);
                amountOut += SqrtPriceMath.getAmount0Delta(cur, target, L, false);
            }
        }
    }

    function testFork_depthCurves() public {
        (uint160 s, int24 tick,,,,,) = pool.slot0();
        uint128 liq = pool.liquidity();
        emit log_named_string("DEPTH_POOL", label());
        emit log_named_uint("DEPTH_BLOCK", block.number);
        emit log_named_int("DEPTH_TICK", tick);
        emit log_named_uint("DEPTH_LIQ", liq);
        (int24[] memory ticks, int128[] memory nets, uint256 n) =
            _scan(tick - WINDOW, tick + WINDOW);
        emit log_named_uint("DEPTH_TICKCOUNT", n);
        for (uint256 i = 0; i < n; i++) {
            emit log_string(string.concat(
                    "TICK,",
                    label(),
                    ",",
                    vm.toString(int256(ticks[i])),
                    ",",
                    vm.toString(int256(nets[i]))
                ));
        }
        // price moves in percent of PRICE: price ratio r => sqrt ratio sqrt(r); use 1e6 fixed point
        uint256[3] memory pcts = [uint256(10_000), 20_000, 50_000]; // 1%, 2%, 5% in 1e6
        for (uint256 d = 0; d < 2; d++) {
            bool down = d == 0; // "down" = sqrt price falls = token0 in
            for (uint256 i = 0; i < 3; i++) {
                // target sqrt = s * sqrt(1 -/+ pct)
                uint256 ratio = down ? 1_000_000 - pcts[i] : 1_000_000 + pcts[i];
                // sqrt(ratio/1e6) = _sqrt(ratio * 1e6) / 1e6
                uint160 target = uint160(uint256(s) * _sqrt(ratio * 1e6) / 1e6);
                (uint256 amtIn, uint256 amtOut) = _walk(s, tick, liq, target, down, ticks, nets, n);
                emit log_string(string.concat(
                        "DEPTH,",
                        label(),
                        ",",
                        down ? "token0_in_sqrt_down" : "token1_in_sqrt_up",
                        ",",
                        vm.toString(pcts[i]),
                        ",",
                        vm.toString(amtIn),
                        ",",
                        vm.toString(amtOut)
                    ));
            }
        }
    }

    function _sqrt(uint256 x) internal pure returns (uint256 y) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}

interface IBitmapD {
    function tickBitmap(int16 wordPosition) external view returns (uint256);
}

contract GroupDepthSPY is GroupDepthBase {
    function poolAddr() internal pure override returns (address) {
        return 0x38453c115607463Ac284820Ce959831042f3Df4E;
    }

    function label() internal pure override returns (string memory) {
        return "SPY";
    }
}

contract GroupDepthGLD is GroupDepthBase {
    function poolAddr() internal pure override returns (address) {
        return 0x9896AF2F6fED9939aCc87637b973b6484E6619D8;
    }

    function label() internal pure override returns (string memory) {
        return "GLD";
    }
}

contract GroupDepthNVDA is GroupDepthBase {
    function poolAddr() internal pure override returns (address) {
        return 0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b;
    }

    function label() internal pure override returns (string memory) {
        return "NVDA";
    }
}
