// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IRamsesPool } from "../../src/interfaces/IRamsesV3.sol";

/// @dev Pays the pool in its swap callback.
contract ReplayTrader {
    IRamsesPool public immutable pool;

    constructor(IRamsesPool p) {
        pool = p;
    }

    function swapExactIn(bool zeroForOne, uint256 amountIn, uint160 limit)
        external
        returns (int256 a0, int256 a1)
    {
        (a0, a1) = pool.swap(address(this), zeroForOne, int256(amountIn), limit, "");
    }

    function uniswapV3SwapCallback(int256 a, int256 b, bytes calldata) external {
        require(msg.sender == address(pool), "NOT_POOL");
        if (a > 0) IERC20(pool.token0()).transfer(msg.sender, uint256(a));
        if (b > 0) IERC20(pool.token1()).transfer(msg.sender, uint256(b));
    }
}

/// @notice Validates the offline quote walk (quote_math.py) against the DEPLOYED pool. At the pinned block it executes real swaps
/// of the exact integer amounts the Python side quoted (each from a fresh snapshot), in both directions, with and without a
/// sqrtPriceLimit, plus a sequential buy-then-sell, and prints machine-readable lines for replay_pool.py to compare:
///   REPLAY,<kind>,<index>,<requestedIn>,<consumedIn>,<out>,<sqrtPriceX96After>
/// Inputs (env): POOL, USDG, BUY_AMOUNTS / SELL_AMOUNTS (comma separated raw integers), BUY_LIMIT / SELL_LIMIT (uint160; 0 = none).
/// The public RPC keeps ~9 minutes of state, so the pin must be fresh; the saved log is the evidence, not a rerun.
contract PoolQuoteReplay is Test {
    uint160 constant MIN_SQRT = 4295128739;
    uint160 constant MAX_SQRT = 1461446703485210103287273052203988822378723970342;

    IRamsesPool pool;
    ReplayTrader trader;
    bool usdg0;

    function setUp() public {
        // Research replay, driven by env inputs (see above); skipped in a plain fork run.
        if (vm.envOr("POOL", address(0)) == address(0)) vm.skip(true);
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663, "chain");
        pool = IRamsesPool(vm.envAddress("POOL"));
        trader = new ReplayTrader(pool);
        usdg0 = pool.token0() == vm.envAddress("USDG");
        assertTrue(usdg0 || pool.token1() == vm.envAddress("USDG"), "USDG is neither token");
    }

    function _run(string memory kind, uint256 i, bool zfo, uint256 amount, uint160 limit) internal {
        address tokenIn = zfo ? pool.token0() : pool.token1();
        deal(tokenIn, address(trader), amount * 2 + 1);
        uint256 snap = vm.snapshot();
        (int256 a0, int256 a1) = trader.swapExactIn(zfo, amount, limit == 0 ? (zfo ? MIN_SQRT + 1 : MAX_SQRT - 1) : limit);
        (uint160 s,,,,,,) = pool.slot0();
        uint256 consumed = uint256(zfo ? a0 : a1);
        uint256 out = uint256(-(zfo ? a1 : a0));
        emit log_string(string.concat("REPLAY,", kind, ",", vm.toString(i), ",", vm.toString(amount), ",", vm.toString(consumed), ",", vm.toString(out), ",", vm.toString(uint256(s))));
        vm.revertTo(snap);
    }

    function testFork_replayQuotes() public {
        uint256[] memory buys = vm.envUint("BUY_AMOUNTS", ",");
        uint256[] memory sells = vm.envUint("SELL_AMOUNTS", ",");
        uint160 buyLimit = uint160(vm.envUint("BUY_LIMIT"));
        uint160 sellLimit = uint160(vm.envUint("SELL_LIMIT"));
        emit log_named_uint("REPLAY_BLOCK", block.number);
        for (uint256 i = 0; i < buys.length; i++) {
            _run("buy", i, usdg0, buys[i], 0);
            _run("sell", i, !usdg0, sells[i], 0);
            _run("buyLimited", i, usdg0, buys[i], buyLimit);
            _run("sellLimited", i, !usdg0, sells[i], sellLimit);
        }
        // sequential: buy, then sell exactly the stock obtained, in the same evolving pool state (no snapshot between)
        for (uint256 i = 0; i < buys.length; i++) {
            uint256 snap = vm.snapshot();
            address tokenIn = usdg0 ? pool.token0() : pool.token1();
            deal(tokenIn, address(trader), buys[i] * 2 + 1);
            (int256 a0, int256 a1) = trader.swapExactIn(usdg0, buys[i], usdg0 ? MIN_SQRT + 1 : MAX_SQRT - 1);
            uint256 stockOut = uint256(-(usdg0 ? a1 : a0));
            (int256 b0, int256 b1) = trader.swapExactIn(!usdg0, stockOut, !usdg0 ? MIN_SQRT + 1 : MAX_SQRT - 1);
            uint256 usdgBack = uint256(-(usdg0 ? b0 : b1));
            emit log_string(string.concat("REPLAY,sequential,", vm.toString(i), ",", vm.toString(buys[i]), ",", vm.toString(stockOut), ",", vm.toString(usdgBack), ",0"));
            vm.revertTo(snap);
        }
    }
}
