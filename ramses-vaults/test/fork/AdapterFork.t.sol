// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { RamsesV3PairedAdapter } from "../../src/RamsesV3PairedAdapter.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { OracleDouble } from "../mocks/RamsesV3Double.sol";

interface IMixedQuoter {
    struct Quote {
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        int24 tickSpacing;
        uint160 limit;
    }
    function quoteExactInputSingleV3(Quote calldata)
        external
        returns (uint256, uint160, uint32, uint256);
    function factory() external view returns (address);
}

interface IUniversalRouter {
    function execute(bytes calldata, bytes[] calldata, uint256) external payable;
}

interface IFeed {
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
}

contract ForkTrader {
    IRamsesPool immutable pool;

    constructor(IRamsesPool p) {
        pool = p;
    }

    function move(bool zeroForOne, uint160 limit) external {
        pool.swap(address(this), zeroForOne, int256(1e25), limit, "");
    }

    function trade(bool zeroForOne, uint256 amount) external {
        pool.swap(
            address(this),
            zeroForOne,
            int256(amount),
            zeroForOne ? TickMath.MIN_SQRT_PRICE + 1 : TickMath.MAX_SQRT_PRICE - 1,
            ""
        );
    }

    function uniswapV3SwapCallback(int256 a, int256 b, bytes calldata) external {
        require(msg.sender == address(pool));
        if (a > 0) IERC20(pool.token0()).transfer(msg.sender, uint256(a));
        if (b > 0) IERC20(pool.token1()).transfer(msg.sender, uint256(b));
    }
}

contract AdapterForkTest is Test {
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    IRamsesPool constant pool = IRamsesPool(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b);
    IRamsesNfp constant pm = IRamsesNfp(0x2eBd7B85a4E08D5B508b04BA147976C94afE6590);
    IRamsesGauge constant gauge = IRamsesGauge(0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce);
    address constant RAM = 0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802;
    address constant ROUTER = 0xbFbb2BCBc9dfFA029C27A249ae9BE031E1d83b1C;
    IMixedQuoter constant quoter = IMixedQuoter(0x23B6EC50Fe0197FbE436717a0676BC07c54ba562);
    RamsesV3PairedAdapter adapter;
    OracleDouble oracle;
    ForkTrader trader;
    IERC20 stock;
    IERC20 usdg;
    uint160 ref;
    int24 center;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        stock = IERC20(pool.token1());
        usdg = IERC20(pool.token0());
        (ref, center,,,,,) = pool.slot0();
        center = center / 10 * 10;
        oracle = new OracleDouble();
        oracle.set(ref);
        adapter = new RamsesV3PairedAdapter(address(this), pm, oracle);
        adapter.registerPair(
            PAIR,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock), address(usdg), pool, gauge, 500, 50
            )
        );
        adapter.setRange(PAIR, center - 100, center + 100);
        deal(address(stock), address(this), 10e18);
        deal(address(usdg), address(this), 2000e6);
        stock.approve(address(adapter), type(uint256).max);
        usdg.approve(address(adapter), type(uint256).max);
        trader = new ForkTrader(pool);
        deal(address(stock), address(trader), 100_000e18);
        deal(address(usdg), address(trader), 10_000_000e6);
    }

    function _add() internal returns (uint128 l) {
        (,, l) = adapter.addLiquidity(PAIR, 1e18, 200e6, block.timestamp);
    }

    function _roundTrip(int24 offset) private {
        if (offset != 0) adapter.setRange(PAIR, center + offset - 50, center + offset + 50);
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 1800);
        l += _add();
        vm.warp(vm.getBlockTimestamp() + 1800);
        adapter.collectFees(PAIR, block.timestamp);
        (address[] memory tokens, uint256[] memory rewards) = adapter.harvest(PAIR, block.timestamp);
        assertEq(tokens.length, 3);
        assertEq(rewards[0], 0);
        assertEq(rewards[1], 0);
        if (offset == 0) assertGt(rewards[2], 0);
        else assertEq(rewards[2], 0);
        emit log_named_uint("RAM reward", rewards[2]);
        adapter.decreaseLiquidity(PAIR, l / 2, ref, block.timestamp);
        adapter.decreaseLiquidity(PAIR, l - l / 2, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        // No swap: only integer rounding, at most 6 raw units per asset for 2 adds/2 removes.
        assertApproxEqAbs(stock.balanceOf(address(this)), s, 6);
        assertApproxEqAbs(usdg.balanceOf(address(this)), u, 6);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
        assertEq(stock.allowance(address(adapter), address(pm)), 0);
        assertEq(usdg.allowance(address(adapter), address(pm)), 0);
    }

    function testFork_inRangeRoundTrip() public {
        _roundTrip(0);
    }

    function testFork_optInExitWithRevertingRewardManager() public {
        uint256 s = stock.balanceOf(address(this));
        uint256 u = usdg.balanceOf(address(this));
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        vm.mockCallRevert(
            address(pm),
            abi.encodeWithSelector(IRamsesNfp.getReward.selector),
            abi.encodeWithSignature("RewardUnavailable()")
        );
        vm.expectRevert(abi.encodeWithSignature("RewardUnavailable()"));
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.setAllowRewardForfeiture(PAIR, true);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
        assertApproxEqAbs(stock.balanceOf(address(this)), s, 2);
        assertApproxEqAbs(usdg.balanceOf(address(this)), u, 2);
        assertEq(adapter.pendingRewards(PAIR, RAM), 0);
        assertEq(stock.allowance(address(adapter), address(pm)), 0);
        assertEq(usdg.allowance(address(adapter), address(pm)), 0);
    }

    function testFork_aboveRangeRoundTrip() public {
        _roundTrip(300);
    }

    function testFork_belowRangeRoundTrip() public {
        _roundTrip(-300);
    }

    function _shock(bool up) private {
        (uint256 usedS, uint256 usedU, uint128 l) =
            adapter.addLiquidity(PAIR, 1e18, 200e6, block.timestamp);
        uint256 initial = _value(usedS, usedU);
        vm.warp(vm.getBlockTimestamp() + 1800);
        trader.move(!up, TickMath.getSqrtPriceAtTick(center + (up ? int24(150) : int24(-150))));
        vm.warp(vm.getBlockTimestamp() + 1800);
        adapter.collectFees(PAIR, block.timestamp);
        RamsesV3PairedAdapter.PositionState memory expected = adapter.positionState(PAIR);
        (uint256 s, uint256 u,) = adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertTrue(s == 0 || u == 0);
        assertApproxEqAbs(s, expected.stockAmount, 2);
        assertApproxEqAbs(u, expected.usdgAmount, 2);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        adapter.harvest(PAIR, block.timestamp);
        uint256 ending = _value(s, u);
        emit log_named_uint("deployed value USDG raw", initial);
        emit log_named_uint("exit principal value USDG raw", ending);
        // Compare deployed principal, excluding idle funds, fees and rewards. The donor's swap
        // can add value at the fixed oracle mark. Require absolute change <=1% for this shock.
        assertApproxEqAbs(ending, initial, initial / 100);
    }

    function testFork_shockAboveAndExit() public {
        _shock(true);
    }

    function testFork_shockBelowAndExit() public {
        _shock(false);
    }

    function _value(uint256 s, uint256 u) private view returns (uint256) {
        uint256 ratio = Math.mulDiv(ref, ref, 1 << 64);
        return u + Math.mulDiv(s, 1 << 128, ratio);
    }

    function testFork_automaticHarvestSurvivesFullExitAndBurn() public {
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 3600);
        pool._advancePeriod();
        uint256 id = adapter.positionState(PAIR).tokenId;
        uint256 expected = gauge.earned(RAM, id);
        assertGt(expected, 0);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        assertEq(adapter.pendingRewards(PAIR, RAM), expected);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
        adapter.claimPendingRewards(PAIR);
        assertEq(IERC20(RAM).balanceOf(address(this)), expected);
    }

    function testFork_weekBoundaryAccrualAndExit() public {
        uint128 l = _add();
        uint256 id = adapter.positionState(PAIR).tokenId;
        uint256 oldPeriod = vm.getBlockTimestamp() / 1 weeks;
        vm.warp((oldPeriod + 1) * 1 weeks + 60);
        oracle.set(ref);
        pool._advancePeriod();
        uint256 earned = gauge.periodEarned(oldPeriod, RAM, id);
        assertGt(earned, 0);
        adapter.harvest(PAIR, block.timestamp);
        assertGe(IERC20(RAM).balanceOf(address(this)), earned);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.burnEmptyPosition(PAIR, block.timestamp);
    }

    function testFork_feeSplitCollectReturnsNetLpFees() public {
        _add();
        (uint128 p0,) = pool.protocolFees();
        uint256 g0 = pool.feeGrowthGlobal0X128();
        uint256 fee = pool.fee();
        (,,,,, uint32 protocolShare,) = pool.slot0();
        assertLt(fee, 1_000_000);
        assertLe(protocolShare, 1_000_000);
        trader.trade(true, 1000e6);
        (uint128 after0,) = pool.protocolFees();
        // Governance-adjustable fee: Phase 2 observed 100, Phase 3 observed 175.
        // Assert against the pinned live fee/share, allowing step-boundary integer rounding.
        uint256 expectedProtocol = (1000e6 * fee / 1_000_000) * protocolShare / 1_000_000;
        assertApproxEqAbs(after0 - p0, expectedProtocol, 3);
        emit log_named_uint("pool fee ppm", fee);
        emit log_named_uint("protocol share ppm", protocolShare);
        assertGt(pool.feeGrowthGlobal0X128(), g0);
        (, uint256 fees) = adapter.collectFees(PAIR, block.timestamp);
        assertGt(fees, 0);
        emit log_named_uint("LP USDG fees", fees);
        emit log_named_uint("protocol USDG fees", after0 - p0);
        (, uint256 again) = adapter.collectFees(PAIR, block.timestamp);
        assertEq(again, 0);
    }

    function testFork_routerAndQuoterAbi() public {
        assertEq(quoter.factory(), pool.factory());
        (uint256 quote,,,) = quoter.quoteExactInputSingleV3(
            IMixedQuoter.Quote(address(usdg), address(stock), 1e6, 10, 0)
        );
        assertGt(quote, 0);
        bytes[] memory inputs = new bytes[](1);
        inputs[0] = abi.encode(
            address(this),
            uint256(1e6),
            quote,
            abi.encodePacked(address(usdg), int24(10), address(stock)),
            false
        );
        uint256 beforeBalance = stock.balanceOf(address(this));
        usdg.transfer(ROUTER, 1e6);
        IUniversalRouter(ROUTER).execute(hex"00", inputs, block.timestamp);
        assertEq(stock.balanceOf(address(this)) - beforeBalance, quote);
        emit log_named_uint("1 USDG quote in NVDA raw", quote);
    }

    function testFork_liveFeedAgeAndSimulatedStaleGuard() public {
        (, int256 answer,, uint256 updatedAt,) =
            IFeed(0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15).latestRoundData();
        assertGt(answer, 0);
        assertGt(updatedAt, 0);
        assertLe(updatedAt, block.timestamp);
        emit log_named_uint("live feed age seconds", block.timestamp - updatedAt);
        // Recent pins can be weekdays. Exercise the adapter's stale dependency deterministically;
        // this test oracle is not a production Chainlink guard.
        uint128 l = _add();
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.expectRevert(OracleDouble.StaleOracle.selector);
        adapter.addLiquidity(PAIR, 1e18, 200e6, block.timestamp);
        vm.expectRevert(OracleDouble.StaleOracle.selector);
        adapter.decreaseLiquidity(PAIR, l, ref, block.timestamp);
        adapter.harvest(PAIR, block.timestamp);
    }
}
