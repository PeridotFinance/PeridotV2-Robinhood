// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";

interface IBitmap {
    function tickBitmap(int16 wordPosition) external view returns (uint256);
}

interface IFeeCollector {
    function collectProtocolFees(address pool) external;
    function treasury() external view returns (address);
    function treasuryFees() external view returns (uint256);
    function voter() external view returns (address);
}

/// @dev Swaps directly through the pool and pays in the callback (fork-verified Ramses pattern).
contract GroupTrader {
    IRamsesPool public immutable pool;

    constructor(IRamsesPool p) {
        pool = p;
    }

    function swapExactIn(bool zeroForOne, uint256 amountIn, uint160 limit) external returns (int256 a0, int256 a1) {
        (a0, a1) = pool.swap(address(this), zeroForOne, int256(amountIn), limit, "");
    }

    function uniswapV3SwapCallback(int256 a, int256 b, bytes calldata) external {
        require(msg.sender == address(pool), "NOT_POOL");
        if (a > 0) IERC20(pool.token0()).transfer(msg.sender, uint256(a));
        if (b > 0) IERC20(pool.token1()).transfer(msg.sender, uint256(b));
    }
}

/// @notice Handoff 01 section 1a gate. For one real Ramses pool: mint the exact two-layer configuration we
/// intend (core and wide NFTs, both centred on the current tick), run real swaps in both directions through
/// the pool, then measure what the owner receives through `collect` and through the manager reward route,
/// against what the pool reports for the protocol share.
abstract contract GroupFeeEntitlementBase is Test {
    address constant NFP = 0x2eBd7B85a4E08D5B508b04BA147976C94afE6590;
    address constant RAM = 0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802;
    address constant FEE_COLLECTOR = 0x2Bef16A0081565E72100D73CBe19B1Bd2d802380;

    IRamsesNfp constant pm = IRamsesNfp(NFP);

    struct Cfg {
        address pool;
        address gauge;
        int24 spacing;
        int24 coreHalf;
        int24 wideHalf;
        uint256 capitalUsd; // per layer
        uint256 swapUsd; // per swap
    }

    function cfg() internal pure virtual returns (Cfg memory);

    IRamsesPool pool;
    IRamsesGauge gauge;
    GroupTrader trader;
    IERC20 t0;
    IERC20 t1;
    uint256 coreId;
    uint256 wideId;
    int24 lowerCore;
    int24 upperCore;

    struct Result {
        uint256 gross0;
        uint256 gross1;
        uint256 proto0;
        uint256 proto1;
        uint256 coll0;
        uint256 coll1;
        uint128 ourL;
        uint128 activeL;
        uint256 coreColl0;
        uint256 coreColl1;
    }

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663, "chain");
        Cfg memory c = cfg();
        pool = IRamsesPool(c.pool);
        gauge = IRamsesGauge(c.gauge);
        t0 = IERC20(pool.token0());
        t1 = IERC20(pool.token1());
        // identity checks (stop and report if any differs)
        assertEq(int256(pool.tickSpacing()), int256(c.spacing), "tickSpacing");
        assertEq(gauge.pool(), c.pool, "gauge.pool");
        assertEq(gauge.nfpManager(), NFP, "gauge.nfpManager");
        trader = new GroupTrader(pool);
    }

    /// @dev USD per stock token scaled 1e6, from the pool's own spot (used only to size test trades).
    function _priceUsdPerStock() internal view returns (uint256 pricex1e6, bool stockIs0, address stock) {
        (uint160 sq,,,,,,) = pool.slot0();
        stockIs0 = _dec(address(t0)) == 18;
        stock = stockIs0 ? address(t0) : address(t1);
        uint256 r = uint256(sq) * 1e12 / (1 << 96); // sqrt price scaled 1e12
        uint256 ratio1e24 = r * r; // token1 raw per token0 raw, scaled 1e24
        pricex1e6 = stockIs0 ? ratio1e24 / 1e6 : 1e42 / ratio1e24;
    }

    function _dec(address t) internal view returns (uint8) {
        return IDec(t).decimals();
    }

    function _mintLayer(int24 lo, int24 hi, uint256 usd) internal returns (uint256 id) {
        // desired amounts: generous on both sides (the manager takes what the range needs)
        (uint256 p, bool stockIs0,) = _priceUsdPerStock();
        uint256 stockAmt = usd * 1e6 * 1e18 / p * 2; // 2x USD worth of stock tokens (p is USD x1e6)
        uint256 usdAmt = usd * 1e6 * 2;
        uint256 a0 = stockIs0 ? stockAmt : usdAmt;
        uint256 a1 = stockIs0 ? usdAmt : stockAmt;
        deal(address(t0), address(this), t0.balanceOf(address(this)) + a0);
        deal(address(t1), address(this), t1.balanceOf(address(this)) + a1);
        t0.approve(NFP, type(uint256).max);
        t1.approve(NFP, type(uint256).max);
        (id,,,) = pm.mint(
            IRamsesNfp.MintParams(address(t0), address(t1), cfg().spacing, lo, hi, a0, a1, 0, 0, address(this), block.timestamp)
        );
    }

    function onERC721Received(address, address, uint256, bytes calldata) external pure returns (bytes4) {
        return this.onERC721Received.selector;
    }

    function _swapPlan(uint256 i) internal view returns (bool zeroForOne, uint256 amountIn) {
        (uint256 p, bool stockIs0,) = _priceUsdPerStock();
        Cfg memory c = cfg();
        bool buyStock = (i % 2 == 0); // USDG in, stock out
        // direction: USDG-in means zeroForOne iff USDG is token0 (i.e. stock is token1)
        zeroForOne = buyStock ? !stockIs0 : stockIs0;
        amountIn = buyStock ? c.swapUsd * 1e6 : (c.swapUsd * 1e6 * 1e18 / p); // stock raw for the same USD
    }

    /// @dev Nearest initialized ticks strictly below-or-at and strictly above the current tick, so the test can keep
    /// every swap inside one liquidity region (no tick crossing): measured shares are then exact, not bounded.
    function _bounds(int24 tick, int24 spacing) internal view returns (int24 lo, int24 hi) {
        int24 comp = tick / spacing;
        if (tick < 0 && tick % spacing != 0) comp--;
        // search down (at or below comp)
        int24 c = comp;
        bool found;
        for (uint256 i = 0; i < 64 && !found; i++) {
            int16 word = int16(c >> 8);
            uint8 bit = uint8(uint24(c) & 0xff);
            uint256 mask = (type(uint256).max >> (255 - bit)); // bits 0..bit
            uint256 masked = IBitmap(address(pool)).tickBitmap(word) & mask;
            if (masked != 0) {
                uint8 msb = _msb(masked);
                lo = (int24(word) * 256 + int24(uint24(msb))) * spacing;
                found = true;
            } else {
                c = int24(word) * 256 - 1;
            }
        }
        require(found, "no initialized tick below");
        // search up (strictly above comp)
        c = comp + 1;
        found = false;
        for (uint256 i = 0; i < 64 && !found; i++) {
            int16 word = int16(c >> 8);
            uint8 bit = uint8(uint24(c) & 0xff);
            uint256 mask = ~((uint256(1) << bit) - 1); // bits bit..255
            uint256 masked = IBitmap(address(pool)).tickBitmap(word) & mask;
            if (masked != 0) {
                uint8 lsb = _lsb(masked);
                hi = (int24(word) * 256 + int24(uint24(lsb))) * spacing;
                found = true;
            } else {
                c = (int24(word) + 1) * 256;
            }
        }
        require(found, "no initialized tick above");
    }

    function _msb(uint256 x) internal pure returns (uint8 r) {
        for (uint256 i = 255; ; i--) {
            if ((x >> i) & 1 == 1) return uint8(i);
            if (i == 0) break;
        }
    }

    function _lsb(uint256 x) internal pure returns (uint8 r) {
        for (uint256 i = 0; i < 256; i++) {
            if ((x >> i) & 1 == 1) return uint8(i);
        }
    }

    function _measure(uint256 nSwaps) internal returns (Result memory R) {
        Cfg memory c = cfg();
        (, int24 tick,,,,,) = pool.slot0();
        int24 center = (tick / c.spacing) * c.spacing;
        lowerCore = center - c.coreHalf;
        upperCore = center + c.coreHalf;
        coreId = _mintLayer(lowerCore, upperCore, c.capitalUsd * 7 / 10);
        wideId = _mintLayer(center - c.wideHalf, center + c.wideHalf, c.capitalUsd * 3 / 10);
        R.ourL = pm.positions(coreId).liquidity + pm.positions(wideId).liquidity;
        R.activeL = pool.liquidity();
        assertGt(R.activeL, R.ourL, "we are part of active liquidity");

        (uint128 p0a, uint128 p1a) = pool.protocolFees();
        // fund the trader with plenty of both tokens
        (uint256 px, bool stockIs0,) = _priceUsdPerStock();
        uint256 bigStock = 1_000_000 * 1e6 * 1e18 / px; // $1M worth
        uint256 bigUsdg = 1_000_000 * 1e6;
        deal(address(t0), address(trader), stockIs0 ? bigStock : bigUsdg);
        deal(address(t1), address(trader), stockIs0 ? bigUsdg : bigStock);
        (, int24 tkNow,,,,,) = pool.slot0();
        (int24 bLo, int24 bHi) = _bounds(tkNow, c.spacing);
        emit log_named_int("swap region lower initialized tick", bLo);
        emit log_named_int("swap region upper initialized tick", bHi);
        emit log_named_int("current tick", tkNow);
        // limits stay one price unit inside the initialized ticks, so no swap ever crosses a tick
        uint160 limLo = TickMath.getSqrtPriceAtTick(bLo) + 1;
        uint160 limHi = TickMath.getSqrtPriceAtTick(bHi) - 1;
        for (uint256 i = 0; i < nSwaps; i++) {
            (bool zfo, uint256 amt) = _swapPlan(i);
            (int256 a0, int256 a1) = trader.swapExactIn(zfo, amt, zfo ? limLo : limHi);
            if (a0 > 0) R.gross0 += uint256(a0) * pool.fee() / 1_000_000;
            if (a1 > 0) R.gross1 += uint256(a1) * pool.fee() / 1_000_000;
            (, int24 tk,,,,,) = pool.slot0();
            assertTrue(tk >= lowerCore && tk < upperCore, "price left the core range");
            assertEq(uint256(pool.liquidity()), uint256(R.activeL), "tick crossed: active liquidity changed");
        }
        (uint128 p0b, uint128 p1b) = pool.protocolFees();
        R.proto0 = p0b - p0a;
        R.proto1 = p1b - p1a;
    }

    /// Fee entitlement: protocol share, LP share and what `collect` pays.
    function testFork_feeEntitlement() public {
        Result memory R = _measure(12);
        (,,,,, uint24 feeProto,) = pool.slot0();
        emit log_named_uint("fee ppm in force", pool.fee());
        emit log_named_uint("slot0.feeProtocol ppm", feeProto);
        emit log_named_uint("gross fee token0 raw", R.gross0);
        emit log_named_uint("gross fee token1 raw", R.gross1);
        emit log_named_uint("protocol delta token0 raw", R.proto0);
        emit log_named_uint("protocol delta token1 raw", R.proto1);
        emit log_named_uint("our liquidity", R.ourL);
        emit log_named_uint("active liquidity", R.activeL);

        // collect both layers
        (uint256 a0, uint256 a1) = pm.collect(IRamsesNfp.CollectParams(coreId, address(this), type(uint128).max, type(uint128).max));
        (uint256 b0, uint256 b1) = pm.collect(IRamsesNfp.CollectParams(wideId, address(this), type(uint128).max, type(uint128).max));
        R.coll0 = a0 + b0;
        R.coll1 = a1 + b1;
        R.coreColl0 = a0;
        R.coreColl1 = a1;
        emit log_named_uint("collect token0 (both layers) raw", R.coll0);
        emit log_named_uint("collect token1 (both layers) raw", R.coll1);

        // 1. protocol share matches slot0.feeProtocol of the gross fee (allow rounding of a few raw units per swap)
        assertApproxEqAbs(R.proto0, R.gross0 * feeProto / 1_000_000, 8, "protocol share token0");
        assertApproxEqAbs(R.proto1, R.gross1 * feeProto / 1_000_000, 8, "protocol share token1");
        // 2. fees were really generated on at least one side
        assertGt(R.gross0 + R.gross1, 0, "no fees generated");
        // 3. LP side: our collect equals (gross - protocol) * ourL / activeL (within 1% + 64 raw units)
        uint256 lp0 = R.gross0 - R.proto0;
        uint256 lp1 = R.gross1 - R.proto1;
        uint256 exp0 = lp0 * R.ourL / R.activeL;
        uint256 exp1 = lp1 * R.ourL / R.activeL;
        emit log_named_uint("expected LP collect token0", exp0);
        emit log_named_uint("expected LP collect token1", exp1);
        assertApproxEqAbs(R.coll0, exp0, exp0 / 100 + 64, "LP collect token0");
        assertApproxEqAbs(R.coll1, exp1, exp1 / 100 + 64, "LP collect token1");
        // 4. nothing else pays pool-token fees: a second collect pays zero
        (uint256 z0, uint256 z1) = pm.collect(IRamsesNfp.CollectParams(coreId, address(this), type(uint128).max, type(uint128).max));
        assertEq(z0 + z1, 0, "second collect should be empty");
    }

    /// Gauge route: positions earn automatically (no staking), rewards paid through the manager.
    function testFork_gaugeRewardsThroughManager() public {
        // an out-of-range layer (far above the price) must earn nothing: eligibility is in-range time
        (, int24 tick0,,,,,) = pool.slot0();
        int24 spacing = cfg().spacing;
        int24 farLo = (tick0 / spacing) * spacing + 20 * spacing * 30;
        uint256 farId = _mintLayer(farLo, farLo + 100 * spacing, 1000);
        Result memory R = _measure(12);
        R; // swaps ran; fees accrue to the pool tokens, rewards accrue by time in range
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        address[] memory tokens = new address[](3);
        tokens[0] = address(t0);
        tokens[1] = address(t1);
        tokens[2] = RAM;
        uint256[3] memory eCore;
        uint256[3] memory eWide;
        for (uint256 i = 0; i < 3; i++) {
            eCore[i] = gauge.earned(tokens[i], coreId);
            eWide[i] = gauge.earned(tokens[i], wideId);
            emit log_named_address("reward token", tokens[i]);
            emit log_named_uint("  earned core layer", eCore[i]);
            emit log_named_uint("  earned wide layer", eWide[i]);
        }
        uint256[3] memory before;
        for (uint256 i = 0; i < 3; i++) {
            before[i] = IERC20(tokens[i]).balanceOf(address(this));
        }
        pm.getReward(coreId, tokens);
        pm.getReward(wideId, tokens);
        for (uint256 i = 0; i < 3; i++) {
            uint256 got = IERC20(tokens[i]).balanceOf(address(this)) - before[i];
            emit log_named_uint("  paid by manager getReward", got);
            // the route pays exactly what `earned` reported for the two layers
            assertApproxEqAbs(got, eCore[i] + eWide[i], 2, "manager pays what the gauge says is earned");
        }
        assertEq(gauge.earned(RAM, farId), 0, "out-of-range layer earns no RAM");
        (uint256 f0, uint256 f1) = pm.collect(IRamsesNfp.CollectParams(farId, address(this), type(uint128).max, type(uint128).max));
        assertEq(f0 + f1, 0, "out-of-range layer earns no swap fees");
        // RAM is paid for in-range time; both layers are in range
        assertGt(eCore[2], 0, "core earns RAM");
        assertGt(eWide[2], 0, "wide earns RAM");
        // reward flow of pool tokens is only non-zero if governance funded the gauge with them
        emit log_named_uint("rewardRate pool token0", gauge.rewardRate(address(t0)));
        emit log_named_uint("rewardRate pool token1", gauge.rewardRate(address(t1)));
        emit log_named_uint("rewardRate RAM", gauge.rewardRate(RAM));
    }

    /// Where the protocol share goes when someone collects it from the pool.
    function testFork_protocolShareDestination() public {
        _measure(12);
        IFeeCollector fc = IFeeCollector(FEE_COLLECTOR);
        address treasury = fc.treasury();
        emit log_named_address("feeCollector.treasury", treasury);
        emit log_named_uint("feeCollector.treasuryFees", fc.treasuryFees());
        emit log_named_address("feeCollector.voter", fc.voter());
        (uint128 p0, uint128 p1) = pool.protocolFees();
        uint256 tr0 = t0.balanceOf(treasury);
        uint256 tr1 = t1.balanceOf(treasury);
        uint256 g0 = t0.balanceOf(address(gauge));
        uint256 g1 = t1.balanceOf(address(gauge));
        uint256 fc0 = t0.balanceOf(FEE_COLLECTOR);
        uint256 fc1 = t1.balanceOf(FEE_COLLECTOR);
        emit log_named_uint("pool protocolFees token0", p0);
        emit log_named_uint("pool protocolFees token1", p1);
        try fc.collectProtocolFees(address(pool)) {
            emit log_string("collectProtocolFees: permissionless call succeeded");
        } catch (bytes memory err) {
            emit log_string("collectProtocolFees: reverted for an arbitrary caller");
            emit log_bytes(err);
            return;
        }
        (uint128 q0, uint128 q1) = pool.protocolFees();
        emit log_named_uint("pool protocolFees token0 after", q0);
        emit log_named_uint("pool protocolFees token1 after", q1);
        emit log_named_uint("treasury token0 delta", t0.balanceOf(treasury) - tr0);
        emit log_named_uint("treasury token1 delta", t1.balanceOf(treasury) - tr1);
        emit log_named_uint("gauge token0 delta", t0.balanceOf(address(gauge)) - g0);
        emit log_named_uint("gauge token1 delta", t1.balanceOf(address(gauge)) - g1);
        emit log_named_uint("feeCollector token0 delta", t0.balanceOf(FEE_COLLECTOR) - fc0);
        emit log_named_uint("feeCollector token1 delta", t1.balanceOf(FEE_COLLECTOR) - fc1);
        // observed state: the whole protocol share goes to the treasury; the gauge receives no pool tokens
        assertEq(t0.balanceOf(address(gauge)) - g0, 0, "gauge received token0");
        assertEq(t1.balanceOf(address(gauge)) - g1, 0, "gauge received token1");
        assertApproxEqAbs(t0.balanceOf(treasury) - tr0, p0, 2, "treasury token0");
        assertApproxEqAbs(t1.balanceOf(treasury) - tr1, p1, 2, "treasury token1");
        // the entitlement of our LP positions is untouched by the protocol collection
        (uint256 a0, uint256 a1) = pm.collect(IRamsesNfp.CollectParams(coreId, address(this), type(uint128).max, type(uint128).max));
        assertGt(a0 + a1, 0, "LP fees still collectable after the protocol share was swept");
    }
}

interface IDec {
    function decimals() external view returns (uint8);
}

contract GroupFeeEntitlementSPY is GroupFeeEntitlementBase {
    function cfg() internal pure override returns (Cfg memory) {
        return Cfg(0x38453c115607463Ac284820Ce959831042f3Df4E, 0x739E31798EFE8Cff3ef4c613D8ae751e179C7337, 1, 100, 490, 5000, 1000);
    }
}

contract GroupFeeEntitlementGLD is GroupFeeEntitlementBase {
    function cfg() internal pure override returns (Cfg memory) {
        return Cfg(0x9896AF2F6fED9939aCc87637b973b6484E6619D8, 0x7724Fa91Cd3BbF4bF1EEe4e8383dbb74E08A975C, 1, 100, 490, 5000, 100);
    }
}

contract GroupFeeEntitlementNVDA is GroupFeeEntitlementBase {
    function cfg() internal pure override returns (Cfg memory) {
        return Cfg(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b, 0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce, 10, 100, 490, 5000, 300);
    }
}
