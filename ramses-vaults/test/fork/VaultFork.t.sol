// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { Vm } from "forge-std/Vm.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesVaultAdapter } from "../../src/RamsesVaultAdapter.sol";
import { RamsesV3PairedAdapter } from "../../src/RamsesV3PairedAdapter.sol";
import { RangePolicy } from "../../src/libraries/RangeTypes.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { ForkTrader } from "./AdapterFork.t.sol";

contract VaultForkTest is Test {
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    IRamsesPool constant pool = IRamsesPool(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b);
    IRamsesNfp constant pm = IRamsesNfp(0x2eBd7B85a4E08D5B508b04BA147976C94afE6590);
    IRamsesGauge constant gauge = IRamsesGauge(0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce);
    IAggregatorV3 constant feed = IAggregatorV3(0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15);
    address constant RAM = 0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802;
    address keeper = address(0x123);
    address guardian = address(0x456);
    RamsesOracleGuard guard;
    RamsesBoostedVault vault;
    RamsesLossReserve reserve;
    RamsesVaultAdapter adapter;
    IERC20 stock;
    IERC20 usdg;
    ForkTrader trader;
    uint256 initialStock;
    uint256 initialUsdg = 200e6;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        stock = IERC20(pool.token1());
        usdg = IERC20(pool.token0());
        guard = new RamsesOracleGuard(address(this));
        guard.configurePair(
            PAIR,
            RamsesOracleGuard.FeedConfig(
                address(stock), address(usdg), feed, pool, gauge, 12 hours, 300, 600, 18, 6, 8, true
            )
        );
        (uint256 price,) = guard.pricesUSD18(PAIR); // Uses the actual pinned feed: stale state fails this rehearsal.
        uint256 rawRatio = Math.mulDiv(Math.mulDiv(1e18, 1e18, price), 1e18, 1e6);
        uint160 ref = uint160(Math.sqrt(Math.mulDiv(rawRatio, 1 << 128, 1e18)) << 32);
        trader = new ForkTrader(pool);
        deal(address(stock), address(trader), 100000e18);
        deal(address(usdg), address(trader), 10000000e6);
        (uint160 spot,,,,,,) = pool.slot0();
        if (spot != ref) trader.move(spot > ref, ref); // Actual pool swap; no storage overwrite.
        vault = new RamsesBoostedVault();
        reserve = new RamsesLossReserve();
        adapter = new RamsesVaultAdapter(address(vault), pm, guard);
        reserve.initialize(address(this), address(vault));
        vault.initialize(address(this), keeper, guardian, guard, reserve, adapter);
        reserve.configurePair(
            PAIR,
            RamsesLossReserve.ReserveConfig(
                address(stock), address(usdg), 1000e18, 10000e18, 10000, false, true
            )
        );
        PairConfig memory c = PairConfig(
            address(stock),
            address(usdg),
            address(this),
            address(this),
            1000e18,
            1000e18,
            1 days,
            0,
            300,
            2000,
            100,
            200,
            18,
            6,
            false,
            false,
            false,
            true
        );
        vault.registerPair(
            PAIR,
            c,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock), address(usdg), pool, gauge, 400, 300
            )
        );
        vault.setDeployLossBps(PAIR, 10);
        vault.setRangePolicy(PAIR, RangePolicy(true, 2400, 600, 1 hours, 4, 10, type(uint128).max));
        initialStock = Math.mulDiv(200e18, 1e18, price);
        deal(address(stock), address(this), initialStock);
        deal(address(usdg), address(this), initialUsdg);
        stock.approve(address(vault), initialStock);
        usdg.approve(address(vault), initialUsdg);
        vault.depositForPair(PAIR, address(stock), initialStock);
        vault.depositForPair(PAIR, address(usdg), initialUsdg);
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
    }

    function _shift(int24 offset) internal {
        (, int24 tick,,,,,) = pool.slot0();
        int24 target = tick + offset;
        trader.move(offset < 0, TickMath.getSqrtPriceAtTick(target));
        // Simulated later feed update, necessary for the 600-tick oracle-driven trigger.
        uint256 quote = VaultMath.quoteAtTick(target, 1e18, address(stock), address(usdg));
        vm.mockCall(
            address(feed),
            abi.encodeWithSelector(IAggregatorV3.latestRoundData.selector),
            abi.encode(uint80(1), int256(quote * 100), block.timestamp, block.timestamp, uint80(1))
        );
    }

    function _assertApprovals() internal view {
        assertEq(stock.allowance(address(vault), address(adapter)), 0);
        assertEq(usdg.allowance(address(vault), address(adapter)), 0);
        assertEq(stock.allowance(address(adapter), address(pm)), 0);
        assertEq(usdg.allowance(address(adapter), address(pm)), 0);
    }

    function testForkVaultLiveFeedMintRealOwnerClaimerAndRam() public {
        uint256 id = adapter.positionState(PAIR).tokenId;
        assertEq(pm.ownerOf(id), address(adapter));
        assertFalse(gauge.isAuthorizedClaimer(address(adapter)));
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        address[] memory tokens = gauge.getRewardTokens();
        vm.prank(address(adapter));
        vm.expectRevert();
        gauge.getReward(id, tokens);
        vm.prank(keeper);
        vault.harvest(PAIR, block.timestamp);
        uint256 ram = vault.externalRewards(PAIR, RAM);
        assertGt(ram, 0);
        assertEq(IERC20(RAM).balanceOf(address(vault)), ram);
        assertEq(IERC20(RAM).balanceOf(address(adapter)), 0);
        emit log_named_uint("vault RAM raw", ram);
        vault.forwardExternalReward(PAIR, RAM, address(this), ram);
        assertEq(IERC20(RAM).balanceOf(address(this)), ram);
        _assertApprovals();
    }

    function testForkVaultRecenterBothDirections() public {
        uint256 first = adapter.positionState(PAIR).tokenId;
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _shift(700);
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
        uint256 second = adapter.positionState(PAIR).tokenId;
        assertGt(second, first);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _shift(-1400);
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
        assertGt(adapter.positionState(PAIR).tokenId, second);
        assertGt(vault.externalRewards(PAIR, RAM), 0);
        _assertApprovals();
    }

    function _redeem(bool stockFirst) internal {
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _shift(stockFirst ? int24(-700) : int24(700));
        address first = stockFirst ? address(stock) : address(usdg);
        address second = stockFirst ? address(usdg) : address(stock);
        vm.recordLogs();
        uint256 claim = vault.accountedAssets(PAIR, first);
        (uint256 paid,) = vault.withdrawForSide(PAIR, first, claim, address(this), block.timestamp);
        assertGt(paid, 0);
        claim = vault.accountedAssets(PAIR, second);
        (paid,) = vault.withdrawForSide(PAIR, second, claim, address(this), block.timestamp);
        assertGt(paid, 0);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bool swapped;
        for (uint256 i; i < logs.length; ++i) {
            if (
                logs[i].emitter == address(vault)
                    && logs[i].topics[0]
                        == keccak256("SettlementSwap(bytes32,address,uint256,uint256)")
            ) swapped = true;
        }
        assertTrue(swapped, "redemption must exercise actual settlement pool");
        _assertApprovals();
    }

    function testForkVaultRedeemStockThroughPool() public {
        _redeem(true);
    }

    function testForkVaultRedeemUsdgThroughPool() public {
        _redeem(false);
    }

    function testForkVaultEmergencyExit() public {
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, 0);
        assertTrue(vault.pairConfig(PAIR).emergencyMode);
        assertApproxEqAbs(stock.balanceOf(address(vault)), initialStock, 3);
        assertApproxEqAbs(usdg.balanceOf(address(vault)), initialUsdg, 3);
        vm.prank(keeper);
        vault.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
        _assertApprovals();
    }

    function testForkVaultWeekendFailsClosed() public {
        (, int24 tick,,,,,) = pool.slot0();
        trader.move(false, TickMath.getSqrtPriceAtTick(tick + 513));
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vm.expectRevert();
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        vm.expectRevert();
        vault.withdrawForSide(PAIR, address(stock), initialStock, address(this), block.timestamp);
        vm.prank(keeper);
        vm.expectRevert();
        vault.recenter(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).liquidity, l);
    }

    function testForkVaultConfigForfeitureWithRealLiquidity() public {
        vm.mockCallRevert(
            address(pm),
            abi.encodeWithSelector(IRamsesNfp.getReward.selector),
            abi.encodeWithSignature("RewardUnavailable()")
        );
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vm.expectRevert();
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        vault.setAllowRewardForfeiture(PAIR, true);
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        vm.prank(keeper);
        vault.burnEmptyPosition(PAIR, block.timestamp);
        assertEq(adapter.positionState(PAIR).tokenId, 0);
        _assertApprovals();
    }
}
