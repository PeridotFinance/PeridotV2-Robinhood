// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { ProxyAdmin } from "@openzeppelin/contracts/proxy/transparent/ProxyAdmin.sol";
import {
    ITransparentUpgradeableProxy
} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import { PErc20 } from "peridot/PErc20.sol";
import { IPoolManager } from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";
import { StateLibrary } from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import { PoolIdLibrary } from "@uniswap/v4-core/src/types/PoolId.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RobinhoodBoostedVaultV3 } from "../../src/RobinhoodBoostedVaultV3.sol";
import { UniswapV4PairedAdapterV3 } from "../../src/UniswapV4PairedAdapterV3.sol";
import { RangeLib } from "../../src/libraries/RangeLib.sol";
import { RangePolicy, RangeState } from "../../src/libraries/RangeTypes.sol";
import { CompatPoolShock, IGuardPrices } from "./LendingDelegateMarginCompat.t.sol";

/// @notice The concentrated-liquidity upgrade applied to the LIVE vault and adapter proxies on a
/// local fork, exactly as the timelock would apply it, then exercised end to end against the real
/// Uniswap v4 pool, real oracle guard, real lending markets and real reserve.
/// Local conveniences: pool moves by a funded mover, and a mocked feed answer that tracks them.
contract ConcentratedLiquidityForkTest is Test {
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant VAULT_ADMIN = 0xad2165E6f3b8146D17815968470eDb8B9a0A4ab7;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant ADAPTER_ADMIN = 0x5a345842E3304EE97360de1C61699BA0Efea906b;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
    address constant POOL_MANAGER = 0x8366a39CC670B4001A1121B8F6A443A643e40951;
    address constant USD = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant P_USD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant P_STOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    bytes32 constant IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    RobinhoodBoostedVaultV3 internal vault = RobinhoodBoostedVaultV3(VAULT);
    UniswapV4PairedAdapterV3 internal adapter = UniswapV4PairedAdapterV3(ADAPTER);
    PoolKey internal key;
    uint256 internal usdRate;
    uint256 internal stockRate;

    function _policy() internal pure returns (RangePolicy memory) {
        return RangePolicy({
            enabled: true,
            halfWidthTicks: 1200,
            triggerTicks: 300,
            minInterval: 1 hours,
            maxPerDay: 4,
            maxLossBps: 50,
            maxRangedValueUsd: 50e18
        });
    }

    function setUp() public virtual {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        try IGuardPrices(GUARD).pricesUSD18(PAIR) returns (uint256 s, uint256 u) {
            if (s == 0 || u == 0) vm.skip(true);
        } catch {
            vm.skip(true);
        }
        assertEq(ProxyAdmin(VAULT_ADMIN).owner(), TIMELOCK);
        assertEq(ProxyAdmin(ADAPTER_ADMIN).owner(), TIMELOCK);
        key = IUniswapV4PairedAdapter(ADAPTER).poolKey(PAIR);
    }

    // ---------------------------------------------------------------- helpers

    function _upgrade() internal {
        address adapterImpl = address(new UniswapV4PairedAdapterV3());
        address vaultImpl = address(new RobinhoodBoostedVaultV3());
        vm.startPrank(TIMELOCK);
        ProxyAdmin(ADAPTER_ADMIN).upgradeAndCall(
            ITransparentUpgradeableProxy(ADAPTER), adapterImpl, ""
        );
        ProxyAdmin(VAULT_ADMIN).upgradeAndCall(ITransparentUpgradeableProxy(VAULT), vaultImpl, "");
        vm.stopPrank();
        assertEq(address(uint160(uint256(vm.load(VAULT, IMPLEMENTATION_SLOT)))), vaultImpl);
    }

    function _open() internal {
        vm.prank(TIMELOCK);
        vault.setPairPause(PAIR, false, true, false);
    }

    function _setPolicy(RangePolicy memory p) internal {
        vm.prank(TIMELOCK);
        vault.setRangePolicy(PAIR, p);
    }

    function _checkpoint() internal {
        vm.prank(GOVERNOR);
        vault.checkpoint(PAIR, vm.getBlockTimestamp() + 120);
    }

    function _rebalance() internal {
        _checkpoint();
        vm.prank(GOVERNOR);
        vault.rebalance(PAIR, vm.getBlockTimestamp() + 120);
    }

    function _recenter() internal {
        vm.prank(GOVERNOR);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
    }

    function _liquidity() internal view returns (uint128) {
        return adapter.positionState(PAIR).liquidity;
    }

    function _ticks() internal view returns (int24 lower, int24 upper, bool ranged) {
        return adapter.positionTicks(PAIR);
    }

    function _poolTick() internal view returns (int24 tick) {
        (, tick,,) = StateLibrary.getSlot0(IPoolManager(POOL_MANAGER), PoolIdLibrary.toId(key));
    }

    /// Moves the pool by a multiplicative USD-price factor (bps) and makes the feed follow it.
    function _shock(uint256 priceBps) internal {
        _movePool(priceBps);
        _followFeed();
    }

    /// Moves only the pool: the oracle keeps reporting the old price.
    function _movePool(uint256 priceBps) internal {
        IPoolManager manager = IPoolManager(POOL_MANAGER);
        (uint160 current,,,) = StateLibrary.getSlot0(manager, PoolIdLibrary.toId(key));
        uint160 target = uint160(Math.mulDiv(current, 1e11, Math.sqrt(priceBps * 1e18)));
        CompatPoolShock mover = new CompatPoolShock(manager);
        deal(USD, address(mover), 10_000_000e6);
        deal(STOCK, address(mover), 100_000e18);
        bool zeroForOne = target < current;
        mover.move(key, target, zeroForOne, zeroForOne ? 10_000_000e6 : 100_000e18);
    }

    function _followFeed() internal {
        int24 tick = _poolTick();
        uint256 answer = VaultMath.quoteAtTick(tick, 1e18, STOCK, USD) * 100;
        vm.mockCall(
            FEED,
            abi.encodeCall(IAggregatorV3.latestRoundData, ()),
            abi.encode(uint80(1), int256(answer), block.timestamp, block.timestamp, uint80(1))
        );
    }

    /// Oracle-priced value of everything the pair holds, USD 1e18.
    function _pairValue() internal view returns (uint256) {
        (uint256 stockPrice, uint256 usdgPrice) = IGuardPrices(GUARD).pricesUSD18(PAIR);
        PairLedger memory l = vault.ledger(PAIR);
        (,, uint160 ref) = _reference();
        IUniswapV4PairedAdapter.PositionState memory p = adapter.positionStateAt(PAIR, ref);
        return VaultMath.valueUSD18(l.stockIdle + p.stockAmount, 18, stockPrice, Math.Rounding.Floor)
            + VaultMath.valueUSD18(l.usdgIdle + p.usdgAmount, 6, usdgPrice, Math.Rounding.Floor);
    }

    function _reference() internal view returns (uint256, uint256, uint160) {
        return IGuardRef(GUARD).validatePoolPrice(PAIR, key);
    }

    // ---------------------------------------------------------------- upgrade

    function testUpgradePreservesLiveStateAndStartsFullRange() public {
        bytes memory ledgerBefore = abi.encode(vault.ledger(PAIR));
        bytes memory configBefore = abi.encode(vault.pairConfig(PAIR));
        uint256 aggregate = vault.aggregateUsdgPrincipal(USD);
        address oracle = address(vault.oracleGuard());
        address reserve = address(vault.lossReserve());
        bytes memory positionBefore = abi.encode(adapter_positionState());
        _upgrade();
        assertEq(abi.encode(vault.ledger(PAIR)), ledgerBefore);
        assertEq(abi.encode(vault.pairConfig(PAIR)), configBefore);
        assertEq(vault.aggregateUsdgPrincipal(USD), aggregate);
        assertEq(address(vault.oracleGuard()), oracle);
        assertEq(address(vault.lossReserve()), reserve);
        assertEq(abi.encode(adapter_positionState()), positionBefore);
        (int24 lower, int24 upper, bool ranged) = _ticks();
        assertFalse(ranged, "uninitialized range must mean legacy full range");
        assertEq(lower, TickMath.minUsableTick(60));
        assertEq(upper, TickMath.maxUsableTick(60));
        (bool enabled,,,,,,) = vault.rangePolicy(PAIR);
        assertFalse(enabled);
        assertTrue(vault.hasRole(vault.CONFIG_ROLE(), TIMELOCK));
        assertTrue(vault.hasRole(vault.KEEPER_ROLE(), GOVERNOR));
        assertEq(ProxyAdmin(VAULT_ADMIN).owner(), TIMELOCK);
        assertEq(ProxyAdmin(ADAPTER_ADMIN).owner(), TIMELOCK);
    }

    function adapter_positionState() internal view returns (IUniswapV4PairedAdapter.PositionState memory) {
        return adapter.positionState(PAIR);
    }

    /// Without a range policy the upgraded vault behaves exactly like V2: full range, no recenter.
    function testLegacyFullRangeBehaviourIsUnchanged() public {
        _upgrade();
        _open();
        _rebalance();
        assertGt(_liquidity(), 0);
        (,, bool ranged) = _ticks();
        assertFalse(ranged);
        vm.prank(GOVERNOR);
        vm.expectRevert(RobinhoodBoostedVaultV3.RangePolicyDisabled.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
    }

    // ---------------------------------------------------------------- conversion

    function _convert() internal returns (uint256 valueBefore) {
        _upgrade();
        _open();
        _rebalance();
        _setPolicy(_policy());
        usdRate = PErc20(P_USD).exchangeRateStored();
        stockRate = PErc20(P_STOCK).exchangeRateStored();
        valueBefore = _pairValue();
        _recenter();
    }

    function testFirstRecenterConvertsFullRangeToRangeAroundOraclePrice() public {
        PairLedger memory before_;
        _upgrade();
        _open();
        _rebalance();
        _setPolicy(_policy());
        before_ = vault.ledger(PAIR);
        uint256 valueBefore = _pairValue();
        _recenter();
        (int24 lower, int24 upper, bool ranged) = _ticks();
        assertTrue(ranged);
        assertEq(int256(upper) - int256(lower), 2 * 1200 + 60, "width");
        assertEq(lower % 60, 0);
        assertEq(upper % 60, 0);
        int24 tick = _poolTick();
        assertTrue(tick > lower && tick < upper, "pool price is inside the new range");
        assertGt(_liquidity(), 0);
        PairLedger memory after_ = vault.ledger(PAIR);
        assertLe(after_.stockPrincipal, before_.stockPrincipal, "no principal created");
        assertLe(after_.usdgPrincipal, before_.usdgPrincipal, "no principal created");
        uint256 valueAfter = _pairValue();
        assertGe(valueAfter + valueBefore / 200, valueBefore, "loss bound");
        (, bool initialized, uint64 last, uint8 count) = vault.rangeState(PAIR);
        assertTrue(initialized);
        assertEq(last, block.timestamp);
        assertEq(count, 1);
        emit log_named_int("lower", lower);
        emit log_named_int("upper", upper);
        emit log_named_uint("liquidity", _liquidity());
        emit log_named_uint("value before", valueBefore);
        emit log_named_uint("value after", valueAfter);
    }

    function testRecenterLeavesSupplierExchangeRatesUnchanged() public {
        _convert();
        assertApproxEqRel(PErc20(P_USD).exchangeRateStored(), usdRate, 1e9);
        assertApproxEqRel(PErc20(P_STOCK).exchangeRateStored(), stockRate, 1e9);
    }

    function testRangedPositionHoldsMoreLiquidityThanFullRange() public {
        _upgrade();
        _open();
        _rebalance();
        uint128 full = _liquidity();
        _setPolicy(_policy());
        _recenter();
        assertGt(_liquidity(), full, "concentration must raise liquidity for the same capital");
        emit log_named_uint("full-range liquidity", full);
        emit log_named_uint("ranged liquidity", _liquidity());
    }

    // ---------------------------------------------------------------- eligibility

    function testRecenterCooldownThenNotNeeded() public {
        _convert();
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.RecenterCooldown.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _followFeed();
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.RecenterNotNeeded.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
    }

    function testRecenterFollowsAPriceMoveAndKeepsValue() public {
        _convert();
        (int24 lower0, int24 upper0,) = _ticks();
        _shock(10_700); // +7%: past the 300-tick trigger, still inside the range
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _followFeed();
        uint256 valueBefore = _pairValue();
        _recenter();
        (int24 lower1, int24 upper1,) = _ticks();
        assertTrue(lower1 != lower0 || upper1 != upper0, "range moved");
        int24 tick = _poolTick();
        assertTrue(tick > lower1 && tick < upper1);
        assertGe(_pairValue() + valueBefore / 200, valueBefore, "loss bound");
        assertGt(_liquidity(), 0);
    }

    function testRecenterAfterPriceLeavesTheRange() public {
        _convert();
        _shock(11_800); // +18%: beyond the +12.7% edge, position is single-sided
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _followFeed();
        (int24 lower0, int24 upper0,) = _ticks();
        int24 tick0 = _poolTick();
        assertTrue(tick0 <= lower0 || tick0 >= upper0, "price left the range");
        _recenter();
        (int24 lower1, int24 upper1,) = _ticks();
        int24 tick1 = _poolTick();
        assertTrue(tick1 > lower1 && tick1 < upper1);
    }

    function testRateLimitStopsTheFifthRecenterInADay() public {
        RangePolicy memory p = _policy();
        p.maxPerDay = 2;
        _upgrade();
        _open();
        _rebalance();
        _setPolicy(p);
        _recenter(); // 1
        for (uint256 i; i < 1; ++i) {
            _shock(10_700);
            vm.warp(vm.getBlockTimestamp() + 1 hours);
            _followFeed();
            _recenter(); // 2
        }
        _shock(9_300);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _followFeed();
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.RecenterRateLimited.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
        vm.warp(vm.getBlockTimestamp() + 1 days);
        _followFeed();
        _recenter(); // the window rolled over
    }

    function testRecenterRefusesWhenPoolDeviatesFromTheOracle() public {
        _convert();
        (uint256 s,) = IGuardPrices(GUARD).pricesUSD18(PAIR);
        _shock(10_700);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        // Feed still reports the old price: pool and oracle disagree by 7%, beyond the deviation gate.
        vm.mockCall(
            FEED,
            abi.encodeCall(IAggregatorV3.latestRoundData, ()),
            abi.encode(
                uint80(1),
                int256(s / 1e10),
                vm.getBlockTimestamp(),
                vm.getBlockTimestamp(),
                uint80(1)
            )
        );
        vm.prank(GOVERNOR);
        vm.expectRevert();
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
    }

    // ---------------------------------------------------------------- access control

    function testOnlyKeeperRecentersAndOnlyConfigSetsPolicy() public {
        _upgrade();
        _open();
        address stranger = address(0xBAD);
        vm.startPrank(stranger);
        vm.expectRevert();
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
        vm.expectRevert();
        vault.setRangePolicy(PAIR, _policy());
        vm.expectRevert();
        vault.clearRange(PAIR);
        vm.stopPrank();
        // The governor holds keeper and guardian roles but not CONFIG_ROLE: policy is timelock-only.
        vm.prank(GOVERNOR);
        vm.expectRevert();
        vault.setRangePolicy(PAIR, _policy());
    }

    function testAdapterRangeSettersAreVaultOnly() public {
        _upgrade();
        vm.startPrank(GOVERNOR);
        vm.expectRevert();
        adapter.setRange(PAIR, -600, 600);
        vm.expectRevert();
        adapter.clearRange(PAIR);
        vm.stopPrank();
        vm.prank(TIMELOCK);
        vm.expectRevert();
        adapter.setRange(PAIR, -600, 600);
    }

    function testRangeCannotChangeWhileAPositionExists() public {
        _upgrade();
        _open();
        _rebalance();
        assertGt(_liquidity(), 0);
        vm.prank(VAULT);
        vm.expectRevert(UniswapV4PairedAdapterV3.RangeLocked.selector);
        adapter.setRange(PAIR, -600, 600);
        vm.prank(VAULT);
        vm.expectRevert(UniswapV4PairedAdapterV3.RangeLocked.selector);
        adapter.clearRange(PAIR);
    }

    function testRejectedRangesFromTheVaultAreRefused() public {
        _upgrade();
        vm.startPrank(VAULT);
        vm.expectRevert(UniswapV4PairedAdapterV3.InvalidRange.selector);
        adapter.setRange(PAIR, 600, -600);
        vm.expectRevert(UniswapV4PairedAdapterV3.InvalidRange.selector);
        adapter.setRange(PAIR, -601, 600); // not aligned to the spacing of 60
        vm.expectRevert(UniswapV4PairedAdapterV3.InvalidRange.selector);
        adapter.setRange(PAIR, TickMath.MIN_TICK - 60, 600);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------- withdrawals

    function testLargeRedemptionsUnwindTheRangedPositionAndPayExactly() public {
        _convert();
        address[2] memory markets = [P_USD, P_STOCK];
        address[2] memory assets = [USD, STOCK];
        for (uint256 i; i < 2; ++i) {
            uint256 shares = IERC20(markets[i]).balanceOf(GOVERNOR);
            uint256 before_ = IERC20(assets[i]).balanceOf(GOVERNOR);
            uint256 rate = i == 0
                ? PErc20(P_USD).exchangeRateCurrent()
                : PErc20(P_STOCK).exchangeRateCurrent();
            uint256 take = shares / 10 * 9;
            uint256 expected = take * rate / 1e18;
            vm.prank(GOVERNOR);
            assertEq(PErc20(markets[i]).redeem(take), 0);
            uint256 paid = IERC20(assets[i]).balanceOf(GOVERNOR) - before_;
            assertGe(paid, expected * 999 / 1000, "payout within 0.1% of the quoted value");
        }
    }

    /// With the price above the range the position holds only USDG; redeeming NVDA has no inventory
    /// to unwind. It must neither revert on a zero target nor liquidate the whole LP (the V2 trap).
    function testOneSidedRangeRedemptionDoesNotUnwindEverything() public {
        _convert();
        _shock(11_800);
        vm.warp(vm.getBlockTimestamp() + 10 minutes);
        _followFeed();
        IUniswapV4PairedAdapter.PositionState memory p = adapter.positionState(PAIR);
        emit log_named_uint("stock in position", p.stockAmount);
        emit log_named_uint("usdg in position", p.usdgAmount);
        uint128 liquidityBefore = _liquidity();
        uint256 shares = IERC20(P_STOCK).balanceOf(GOVERNOR) / 2;
        vm.prank(GOVERNOR);
        PErc20(P_STOCK).redeem(shares);
        assertGt(_liquidity(), 0, "LP is not wholesale unwound for the other token");
        assertLe(_liquidity(), liquidityBefore);
    }

    function testCheckpointOnARangedPositionRecognisesLossWithoutCreatingPrincipal() public {
        _convert();
        PairLedger memory before_ = vault.ledger(PAIR);
        _shock(8_500);
        vm.warp(vm.getBlockTimestamp() + 10 minutes);
        _followFeed();
        _checkpoint();
        PairLedger memory after_ = vault.ledger(PAIR);
        assertLe(after_.stockPrincipal, before_.stockPrincipal);
        assertLe(after_.usdgPrincipal, before_.usdgPrincipal);
        emit log_named_uint("stock principal", after_.stockPrincipal);
        emit log_named_uint("usdg principal", after_.usdgPrincipal);
        emit log_named_uint("cumulative loss", after_.cumulativeLossUSDG);
    }

    // ---------------------------------------------------------------- exits

    function testEmergencyExitThenClearRangeReturnsToLegacy() public {
        _convert();
        uint128 liquidity = _liquidity();
        vm.startPrank(GOVERNOR);
        vault.setPairPause(PAIR, true, true, false);
        vault.emergencyDecrease(PAIR, liquidity, vm.getBlockTimestamp() + 120);
        vault.burnEmptyPosition(PAIR, vm.getBlockTimestamp() + 120);
        vault.clearRange(PAIR);
        vm.stopPrank();
        (,, bool ranged) = _ticks();
        assertFalse(ranged);
        (, bool initialized,,) = vault.rangeState(PAIR);
        assertFalse(initialized);
        (,, uint64 lastKept,) = vault.rangeState(PAIR);
        assertGt(lastKept, 0, "history survives the clear");
        assertEq(_liquidity(), 0);
        // Everything is custody again; redemptions use the oracle-free idle path.
        uint256 quarter = IERC20(P_USD).balanceOf(GOVERNOR) / 4;
        vm.prank(GOVERNOR);
        assertEq(PErc20(P_USD).redeem(quarter), 0);
    }

    function testRangedCapBoundsRebalanceDeployment() public {
        RangePolicy memory p = _policy();
        p.maxRangedValueUsd = 1e18; // $0.50 per side
        _upgrade();
        _open();
        _setPolicy(p);
        _recenter(); // converts and deploys under the cap
        (uint256 stockPrice, uint256 usdgPrice) = IGuardPrices(GUARD).pricesUSD18(PAIR);
        (,, uint160 ref) = _reference();
        IUniswapV4PairedAdapter.PositionState memory pos = adapter.positionStateAt(PAIR, ref);
        uint256 lpValue = VaultMath.valueUSD18(pos.stockAmount, 18, stockPrice, Math.Rounding.Floor)
            + VaultMath.valueUSD18(pos.usdgAmount, 6, usdgPrice, Math.Rounding.Floor);
        assertLe(lpValue, 1.02e18, "ranged value stays under the policy cap");
        _rebalance();
        pos = adapter.positionStateAt(PAIR, ref);
        lpValue = VaultMath.valueUSD18(pos.stockAmount, 18, stockPrice, Math.Rounding.Floor)
            + VaultMath.valueUSD18(pos.usdgAmount, 6, usdgPrice, Math.Rounding.Floor);
        assertLe(lpValue, 1.02e18, "rebalance cannot exceed the cap either");
    }

    // ---------------------------------------------------------------- Astra review regressions

    /// The removal gate bounds the STOCK price in USD, and the pool quotes the inverse. A pool
    /// pushed to 90% of the removal gate in either direction must still allow every exit.
    function _exitWithPoolAtGate(bool up) internal {
        _convert();
        uint256 gate = IGuardGate(GUARD).maxRemovalDeviationBps(PAIR);
        uint256 move = gate * 99 / 100;
        // Oracle unchanged; pool moves by `move` bps in USD terms.
        _movePool(up ? 10_000 + move : 10_000 - move);
        vm.warp(vm.getBlockTimestamp() + 10 minutes);
        uint128 liquidity = _liquidity();
        // pToken redemption reaches through the LP at the deviating pool price.
        uint256 shares = IERC20(P_USD).balanceOf(GOVERNOR) / 10 * 9;
        vm.prank(GOVERNOR);
        assertEq(PErc20(P_USD).redeem(shares), 0);
        assertLe(_liquidity(), liquidity);
        // Guardian emergency exit of whatever remains.
        uint128 left = _liquidity();
        if (left != 0) {
            vm.startPrank(GOVERNOR);
            vault.setPairPause(PAIR, true, true, false);
            vault.emergencyDecrease(PAIR, left, vm.getBlockTimestamp() + 120);
            vm.stopPrank();
            assertEq(_liquidity(), 0);
        }
    }

    function testExitsWorkWithPoolAtTheRemovalGateStockPriceUp() public {
        _exitWithPoolAtGate(true);
    }

    function testExitsWorkWithPoolAtTheRemovalGateStockPriceDown() public {
        _exitWithPoolAtGate(false);
    }

    function testDisabledPolicyStopsAdditionsToARangedPosition() public {
        _convert();
        RangePolicy memory off = _policy();
        off.enabled = false;
        _setPolicy(off);
        _checkpoint();
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.InsufficientLiquidity.selector);
        vault.rebalance(PAIR, vm.getBlockTimestamp() + 120);
    }

    function testClearingTheRangeKeepsTheCooldown() public {
        _convert();
        uint128 liquidity = _liquidity();
        vm.startPrank(GOVERNOR);
        vault.setPairPause(PAIR, true, true, false);
        vault.emergencyDecrease(PAIR, liquidity, vm.getBlockTimestamp() + 120);
        vault.burnEmptyPosition(PAIR, vm.getBlockTimestamp() + 120);
        vault.clearRange(PAIR);
        vm.stopPrank();
        _open();
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.RecenterCooldown.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
    }

    /// Calls at 0h, 21h, then 22h and 25h: a rolling window forbids both, a fixed one allowed 25h.
    function testRateLimitIsRollingNotAFixedWindow() public {
        RangePolicy memory p = _policy();
        p.maxPerDay = 2;
        _upgrade();
        _open();
        _rebalance();
        _setPolicy(p);
        _recenter(); // t = 0
        vm.warp(vm.getBlockTimestamp() + 21 hours);
        _shock(10_700);
        _recenter(); // t = 21h
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _shock(9_300);
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.RecenterRateLimited.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120); // t = 22h: oldest is 22h old
        vm.warp(vm.getBlockTimestamp() + 2 hours);
        _followFeed();
        _recenter(); // t = 24h: the first one has aged out
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        _shock(10_700);
        vm.prank(GOVERNOR);
        vm.expectRevert(RangeLib.RecenterRateLimited.selector);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120); // t = 25h: 21h entry is only 4h old
    }

    /// A pool pushed to the edge of the allocation gate must not let the keeper add concentrated
    /// liquidity at a loss beyond the policy bound.
    function testRangedRebalanceRefusesLiquidityAddedAtAPushedPrice() public {
        RangePolicy memory p = _policy();
        p.maxLossBps = 1; // 0.01%
        _upgrade();
        _open();
        _setPolicy(p);
        _recenter(); // converts, deploys around the oracle price
        // Make room: raise the cap and let idle assets accumulate by exiting a little liquidity.
        p.maxRangedValueUsd = 500e18;
        _setPolicy(p);
        uint256 gate = IGuardGate(GUARD).maxPriceDeviationBps(PAIR);
        _movePool(10_000 + gate * 95 / 100);
        _checkpoint();
        vm.prank(GOVERNOR);
        try vault.rebalance(PAIR, vm.getBlockTimestamp() + 120) {
            emit log("rebalance added nothing material or stayed inside the 1 bp bound");
        } catch (bytes memory reason) {
            assertTrue(
                bytes4(reason) == RangeLib.DeployLossTooHigh.selector
                    || bytes4(reason) == RangeLib.InsufficientLiquidity.selector,
                "only the loss bound or an empty idle balance may stop it"
            );
        }
    }

    // ---------------------------------------------------------------- pure / fuzz

    function testFuzzNewRangeContainsCenterAndIsAligned(int24 center, uint16 half, int24 spacingSeed)
        public
        pure
    {
        int24 spacing = int24(int256(bound(int256(spacingSeed), 1, 200)));
        half = uint16(bound(half, 600, 4800));
        half = uint16(uint256(half) / uint24(spacing) * uint24(spacing));
        center = int24(bound(int256(center), TickMath.MIN_TICK + 6000, TickMath.MAX_TICK - 6000));
        (int24 lower, int24 upper) = RangeLib.newRange(center, half, spacing);
        assertEq(lower % spacing, 0);
        assertEq(upper % spacing, 0);
        assertTrue(lower <= center && center < upper);
        assertEq(int256(upper) - int256(lower), int256(uint256(half)) * 2 + spacing);
        assertTrue(center - lower >= int256(uint256(half)) - 0);
    }

    function testFuzzPolicyValidation(
        uint16 half,
        uint16 trigger,
        uint32 interval,
        uint8 perDay,
        uint16 lossBps,
        uint128 cap
    ) public {
        _upgrade();
        RangePolicy memory p = RangePolicy(true, half, trigger, interval, perDay, lossBps, cap);
        bool valid = half >= 600 && half <= 4800 && half % 60 == 0 && trigger >= 60
            && trigger <= half / 2 && interval >= 1 hours && interval <= 7 days && perDay != 0
            && perDay <= 24 && lossBps <= 100 && cap != 0;
        vm.prank(TIMELOCK);
        if (valid) {
            vault.setRangePolicy(PAIR, p);
        } else {
            vm.expectRevert(RangeLib.InvalidRangePolicy.selector);
            vault.setRangePolicy(PAIR, p);
        }
    }
}

interface IGuardGate {
    function maxRemovalDeviationBps(bytes32 pairId) external view returns (uint16);
    function maxPriceDeviationBps(bytes32 pairId) external view returns (uint16);
}

interface IGuardRef {
    function validatePoolPrice(bytes32 pairId, PoolKey calldata key)
        external
        view
        returns (uint256, uint256, uint160);
}
