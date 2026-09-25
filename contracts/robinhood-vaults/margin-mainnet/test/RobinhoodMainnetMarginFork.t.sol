// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { ProxyAdmin } from "@openzeppelin/contracts/proxy/transparent/ProxyAdmin.sol";
import { PErc20 } from "peridot/PErc20.sol";
import { StockSimplePriceOracle } from "peridot/StockSimplePriceOracle.sol";
import { RobinhoodMarginPriceOracle } from "peridot/margin/RobinhoodMarginPriceOracle.sol";
import {
    IsolatedMarginExecutorUpgradeable
} from "peridot/margin/IsolatedMarginExecutorUpgradeable.sol";
import {
    IsolatedMarginLiquidatorUpgradeable
} from "peridot/margin/IsolatedMarginLiquidatorUpgradeable.sol";
import { IsolatedMarginTypes } from "peridot/margin/IsolatedMarginTypes.sol";
import { RobinhoodBoostedVault } from "../../src/RobinhoodBoostedVault.sol";
import { UniswapV4PairedAdapter } from "../../src/UniswapV4PairedAdapter.sol";
import { StockOracleGuard } from "../../src/StockOracleGuard.sol";
import { IAggregatorV3 } from "../../src/interfaces/IAggregatorV3.sol";
import { IStockToken } from "../../src/interfaces/IStockToken.sol";
import { IAllowanceTransfer } from "permit2/src/interfaces/IAllowanceTransfer.sol";
import { RobinhoodMainnetMarginBase } from "../RobinhoodMainnetMarginBase.sol";
import { IPoolManager } from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import { IUnlockCallback } from "@uniswap/v4-core/src/interfaces/callback/IUnlockCallback.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";
import { Currency } from "@uniswap/v4-core/src/types/Currency.sol";
import { BalanceDelta } from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import { StateLibrary } from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import { PoolIdLibrary } from "@uniswap/v4-core/src/types/PoolId.sol";
import { VaultMath } from "../../src/libraries/VaultMath.sol";

/// @dev Scenario driver only: moves the real forked pool to a price limit across actual ticks.
contract MainnetPoolShock is IUnlockCallback {
    IPoolManager immutable manager;

    constructor(IPoolManager manager_) {
        manager = manager_;
    }

    function move(PoolKey memory key, uint160 target, bool zeroForOne, uint256 maximum) external {
        manager.unlock(abi.encode(key, target, zeroForOne, maximum));
    }

    function unlockCallback(bytes calldata data) external returns (bytes memory) {
        require(msg.sender == address(manager), "MANAGER_ONLY");
        (PoolKey memory key, uint160 target, bool zeroForOne, uint256 maximum) =
            abi.decode(data, (PoolKey, uint160, bool, uint256));
        BalanceDelta delta =
            manager.swap(key, IPoolManager.SwapParams(zeroForOne, -int256(maximum), target), "");
        _settle(key.currency0, delta.amount0());
        _settle(key.currency1, delta.amount1());
        return "";
    }

    function _settle(Currency currency, int128 delta) internal {
        if (delta < 0) {
            manager.sync(currency);
            IERC20(Currency.unwrap(currency)).transfer(address(manager), uint256(-int256(delta)));
            manager.settle();
        } else if (delta > 0) {
            manager.take(currency, address(this), uint128(delta));
        }
    }
}

/// @notice Existing mainnet markets/pool/feed; migration and new margin stack exist only on a fork.
/// @dev Only actor wallet/flash funding is injected. Baseline tests do not mock price or seed market liquidity.
contract RobinhoodMainnetMarginForkTest is RobinhoodMainnetMarginBase, Test {
    uint256 internal nativeBlock;
    uint256 internal pinTimestamp;
    address internal constant KEEPER = address(0xB07);

    function setUp() external {
        string memory url = vm.envOr("ROBINHOOD_RPC_URL", string(""));
        if (bytes(url).length == 0) {
            vm.skip(true);
            return;
        }
        string memory pin = vm.readFile("deployments/robinhood-mainnet.margin-pin.json");
        vm.createSelectFork(url, vm.parseJsonUint(pin, ".stateBlock"));
        nativeBlock = vm.parseJsonUint(pin, ".nativeEvmBlockNumber");
        pinTimestamp = vm.parseJsonUint(pin, ".timestamp");
        vm.roll(nativeBlock);
        assertEq(block.timestamp, pinTimestamp);
        assertEq(block.chainid, 4663);
        // Funding assumption, confined to the local actor wallet. Live market balances stay intact.
        deal(USD, ACTOR, 20e6);
        deal(STOCK, ACTOR, 1e18);
        vm.startPrank(ACTOR);
        _pauseBorrowing();
        _migrateMarkets();
        _deployPaused();
        _fundCanary();
        vm.stopPrank();
    }

    function _enable() internal {
        vm.warp(pinTimestamp + 1 hours + 1);
        vm.roll(nativeBlock + 300);
        vm.startPrank(ACTOR);
        _applyRisk();
        config.queueUnpauseOpens();
        vm.stopPrank();
        vm.warp(pinTimestamp + 2 hours + 2);
        vm.roll(nativeBlock + 600);
        vm.startPrank(ACTOR);
        _activate();
        vm.stopPrank();
    }

    function testMigrationAndPausedDeploymentPreserveLiveMarkets() external view {
        assertTrue(pUsd.borrowAccountingEnabled() && pStock.borrowAccountingEnabled());
        assertEq(pUsd.totalBorrows(), 0);
        assertEq(pStock.totalBorrows(), 0);
        assertEq(pUsd.totalBorrowShares(), 0);
        assertEq(pStock.totalBorrowShares(), 0);
        assertTrue(config.opensPaused() && flashVault.paused());
        assertEq(config.queuedActions(keccak256("unpauseOpens")), 0);
        assertEq(address(oracle.assetSource()), address(guardedSource));
        assertEq(controller.isolatedMarginRiskHook(), address(riskEngine));
        assertEq(controller.isolatedMarginRegistrar(), address(riskEngine));
        assertLe(address(replacement).code.length, 24576);
        assertGt(pUsd.balanceOf(ACTOR), 0);
    }

    function testResumeOrdinaryBorrowingKeepsMarginPaused() external {
        vm.startPrank(ACTOR);
        _resumeBorrowingAfterMigration();
        vm.stopPrank();
        assertFalse(controller.borrowGuardianPaused(P_USD));
        assertFalse(controller.borrowGuardianPaused(P_STOCK));
        assertTrue(config.opensPaused());
        assertTrue(flashVault.paused());
        _enable();
        assertFalse(config.opensPaused());
        assertFalse(flashVault.paused());
    }

    function testProxyUpgradeOwnersAreExistingTimelock() external view {
        bytes32 slot = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;
        address[7] memory proxies = [
            address(config),
            address(marginVault),
            address(riskEngine),
            address(executor),
            address(liquidator),
            address(insuranceFund),
            address(feeDistributor)
        ];
        for (uint256 i; i < proxies.length; ++i) {
            address admin = address(uint160(uint256(vm.load(proxies[i], slot))));
            assertEq(ProxyAdmin(admin).owner(), TIMELOCK);
        }
    }

    function activateForTest() external {
        vm.startPrank(ACTOR);
        _activate();
        vm.stopPrank();
    }

    function testActivationRejectsChangedShortRisk() external {
        vm.warp(pinTimestamp + 1 hours + 1);
        vm.startPrank(ACTOR);
        _applyRisk();
        config.queueUnpauseOpens();
        vm.stopPrank();
        vm.warp(pinTimestamp + 2 hours + 2);
        IsolatedMarginTypes.PairRiskConfig memory changed = canaryRisk();
        changed.maintenanceMarginBps = 2000;
        vm.mockCall(
            address(config),
            abi.encodeWithSelector(config.getPairRisk.selector, P_USD, P_USD, P_STOCK),
            abi.encode(changed)
        );
        vm.expectRevert("RISK_NOT_APPLIED");
        this.activateForTest();
        assertTrue(config.opensPaused() && flashVault.paused());
        assertTrue(
            controller.borrowGuardianPaused(P_USD) && controller.borrowGuardianPaused(P_STOCK)
        );
    }

    function testGovernanceCannotActivateBeforeDelay() external {
        vm.startPrank(ACTOR);
        vm.expectRevert();
        config.setPairRisk(P_USD, P_STOCK, P_USD, canaryRisk());
        vm.expectRevert();
        config.unpauseOpens();
        vm.stopPrank();
        assertTrue(config.opensPaused() && flashVault.paused());
    }

    function testMainnetLongRoundTripQuarterDollar() external {
        _roundTrip(false, 0.25e6);
    }

    function testMainnetShortRoundTripQuarterDollar() external {
        _roundTrip(true, 0.25e6);
    }

    function testMainnetLongRoundTripHalfDollar() external {
        _roundTrip(false, 0.5e6);
    }

    function testMainnetShortRoundTripHalfDollar() external {
        _roundTrip(true, 0.5e6);
    }

    function testMainnetLongRoundTripOneDollar() external {
        _roundTrip(false, 1e6);
    }

    function testMainnetShortRoundTripOneDollar() external {
        _roundTrip(true, 1e6);
    }

    function testMainnetPartialThenFullClose() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        uint256 beforeDebt = pUsd.borrowBalanceStored(_account(id));
        vm.prank(ACTOR);
        executor.closePosition(_closeParams(id, 5000));
        assertLt(pUsd.borrowBalanceStored(_account(id)), beforeDebt);
        assertGt(pUsd.borrowBalanceStored(_account(id)), 0);
        _closeAndWithdraw(id);
    }

    function testMainnetUnderlyingAndPTokenRepayment() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        vm.startPrank(ACTOR);
        usd.approve(address(executor), type(uint256).max);
        assertEq(executor.repayWithUnderlying(id, 0.05e6), 0.05e6);
        uint256 shares = Math.mulDiv(0.02e6, 1e18, pUsd.exchangeRateStored());
        pUsd.approve(address(executor), shares);
        assertGt(executor.repayWithPToken(id, shares), 0);
        pUsd.approve(address(executor), 0);
        usd.approve(address(executor), 0);
        vm.stopPrank();
        _closeAndWithdraw(id);
    }

    function testCachedLendingPriceStaysNonzeroButMarginFailsClosed() external {
        address[] memory assets = new address[](1);
        assets[0] = STOCK;
        StockSimplePriceOracle(ASSET_ORACLE).updateChainlinkPrices(assets);
        RobinhoodMarginPriceOracle ungated = new RobinhoodMarginPriceOracle(ACTOR, ASSET_ORACLE);
        uint256 fresh = ungated.getPrice(STOCK);
        assertGt(fresh, 0);
        vm.warp(block.timestamp + 73 hours);
        assertTrue(StockSimplePriceOracle(ASSET_ORACLE).isPriceStale(STOCK));
        assertEq(ungated.getPrice(STOCK), fresh, "legacy wrapper accepts cached stale price");
        assertEq(oracle.getPrice(STOCK), 0);
        assertFalse(oracle.marketPriceable(P_STOCK));
    }

    function testTwelveHourGuardBlocksBeforeSeventyTwoHourLendingThreshold() external {
        vm.warp(block.timestamp + 13 hours);
        assertFalse(StockSimplePriceOracle(ASSET_ORACLE).isPriceStale(STOCK));
        assertGt(StockSimplePriceOracle(ASSET_ORACLE).assetPrices(STOCK), 0);
        assertEq(oracle.getPrice(STOCK), 0);
    }

    function testStaleFeedBlocksOpenCloseAndLiquidationButAllowsDebtRepayAndExit() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        vm.warp(block.timestamp + 13 hours);
        vm.roll(block.number + 3900);
        vm.expectRevert();
        quoter.quoteOpen(P_USD, P_STOCK, P_USD, 0.25e6, 200);
        vm.prank(ACTOR);
        vm.expectRevert();
        executor.closePosition(_closeParams(id, 10000));
        vm.prank(KEEPER);
        vm.expectRevert();
        liquidator.liquidate(_liquidationParams(id));
        vm.startPrank(ACTOR);
        usd.approve(address(executor), type(uint256).max);
        executor.repayWithUnderlying(id, type(uint256).max);
        usd.approve(address(executor), 0);
        executor.exitDebtFreeToPTokens(id, 0);
        vm.stopPrank();
        _assertDebtFree(id);
    }

    function testStockOraclePauseBlocksNewRiskEvenWithFreshPrice() external {
        _enable();
        vm.mockCall(STOCK, abi.encodeCall(IStockToken.oraclePaused, ()), abi.encode(true));
        assertEq(oracle.getPrice(STOCK), 0);
        vm.expectRevert();
        quoter.quoteOpen(P_USD, P_STOCK, P_USD, 0.25e6, 200);
    }

    function testSourceDisagreementFailsClosed() external {
        vm.mockCall(
            ASSET_ORACLE, abi.encodeWithSignature("assetPrices(address)", STOCK), abi.encode(1e18)
        );
        assertEq(oracle.getPrice(STOCK), 0);
    }

    function testImpossibleCloseOutputRollsBackDebtAndShares() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        address account = _account(id);
        uint256 debt = pUsd.borrowBalanceStored(account);
        uint256 shares = pStock.balanceOf(account);
        IsolatedMarginExecutorUpgradeable.CloseParams memory params = _closeParams(id, 10000);
        params.minDebtUnderlying = type(uint128).max;
        vm.prank(ACTOR);
        vm.expectRevert();
        executor.closePosition(params);
        assertEq(pUsd.borrowBalanceStored(account), debt);
        assertEq(pStock.balanceOf(account), shares);
        _closeAndWithdraw(id);
    }

    function testHealthyLiquidationRejectedAndTwoXCapEnforced() external {
        _enable();
        uint256 id = _open(false, 0.25e6);
        vm.prank(KEEPER);
        vm.expectRevert();
        liquidator.liquidate(_liquidationParams(id));
        vm.expectRevert();
        quoter.quoteOpen(P_USD, P_STOCK, P_USD, 0.25e6, 201);
        _closeAndWithdraw(id);
    }

    function testRealBoostedLpCanOpenAndUnwind() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        _makeLp();
        _closeAndWithdraw(id);
    }

    function testLongFortyPercentPoolShockLiquidatesCanary() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        _makeLp();
        _shock(6000);
        _liquidateCanary(id);
    }

    function testShortSeventyPercentPoolShockLiquidatesCanary() external {
        _enable();
        uint256 id = _open(true, 0.5e6);
        // A short adds USDG, so provide a small additional stock supply only in this
        // explicitly funded LP stress scenario. Baseline liquidity tests do not do this.
        vm.startPrank(ACTOR);
        stock.approve(P_STOCK, 0.005e18);
        assertEq(pStock.mint(0.005e18), 0);
        stock.approve(P_STOCK, 0);
        vm.stopPrank();
        _makeLp();
        _shock(17000);
        _liquidateCanary(id);
    }

    function testLiquidationFailurePreservesStateAndCanBeRequoted() external {
        _enable();
        uint256 id = _open(false, 0.5e6);
        _makeLp();
        _shock(6000);
        uint256 beforeDebt = pUsd.borrowBalanceStored(_account(id));
        uint256 beforeShares = pStock.balanceOf(_account(id));
        IsolatedMarginLiquidatorUpgradeable.LiquidationParams memory params = _liquidationParams(id);
        params.minDebtUnderlying = type(uint128).max;
        vm.prank(KEEPER);
        vm.expectRevert();
        liquidator.liquidate(params);
        assertEq(pUsd.borrowBalanceStored(_account(id)), beforeDebt);
        assertEq(pStock.balanceOf(_account(id)), beforeShares);
        _liquidateCanary(id);
    }

    function _makeLp() internal {
        vm.startPrank(ACTOR);
        RobinhoodBoostedVault(BOOSTED_VAULT).checkpoint(PAIR, block.timestamp + 120);
        RobinhoodBoostedVault(BOOSTED_VAULT).rebalance(PAIR, block.timestamp + 120);
        vm.stopPrank();
        assertGt(UniswapV4PairedAdapter(PAIRED_ADAPTER).positionState(PAIR).liquidity, 0);
    }

    function _shock(uint16 priceBps) internal {
        PoolKey memory key = UniswapV4PairedAdapter(PAIRED_ADAPTER).poolKey(PAIR);
        IPoolManager manager = IPoolManager(POOL_MANAGER);
        (uint160 current,,,) = StateLibrary.getSlot0(manager, PoolIdLibrary.toId(key));
        uint160 target = uint160(Math.mulDiv(current, 1e11, Math.sqrt(uint256(priceBps) * 1e18)));
        MainnetPoolShock mover = new MainnetPoolShock(manager);
        deal(USD, address(mover), 1_000_000e6);
        deal(STOCK, address(mover), 10_000e18);
        bool zeroForOne = target < current;
        mover.move(key, target, zeroForOne, zeroForOne ? 1_000_000e6 : 10_000e18);
        (uint160 afterPrice, int24 tick,,) = StateLibrary.getSlot0(manager, PoolIdLibrary.toId(key));
        assertEq(afterPrice, target, "scenario must actually reach target price");
        uint256 answer = VaultMath.quoteAtTick(tick, 1e18, STOCK, USD) * 100;
        // Explicit shock scenario: only the external feed answer is overridden to
        // track the genuine pool move; all lending/vault/router bytecode stays real.
        vm.mockCall(
            FEED,
            abi.encodeCall(IAggregatorV3.latestRoundData, ()),
            abi.encode(uint80(1), int256(answer), block.timestamp, block.timestamp, uint80(1))
        );
        vm.prank(ACTOR);
        RobinhoodBoostedVault(BOOSTED_VAULT).checkpoint(PAIR, block.timestamp + 120);
        emit log_named_uint("shockPriceUsd8", answer);
    }

    function _liquidateCanary(uint256 id) internal {
        address account = _account(id);
        assertTrue(riskEngine.isLiquidatable(account));
        emit log_named_uint(
            "preLiquidationHealthBps", riskEngine.getMetrics(account).healthFactorBps
        );
        // All canary debt is below the engine's $10 dust threshold: full liquidation.
        assertLe(riskEngine.getMetrics(account).debtValueUsd, riskEngine.DUST_DEBT_VALUE_USD());
        vm.prank(KEEPER);
        liquidator.liquidate(_liquidationParams(id));
        (,,,,,,,,,,, IsolatedMarginTypes.Status status) = executor.positions(id);
        assertEq(uint256(status), uint256(IsolatedMarginTypes.Status.LIQUIDATED));
        uint256 free = marginVault.freeBalance(ACTOR, P_USD);
        if (free > 0) {
            vm.prank(ACTOR);
            marginVault.withdraw(P_USD, free);
        }
        _assertDebtFree(id);
    }

    function _open(bool short, uint256 amount) internal returns (uint256 id) {
        vm.startPrank(ACTOR);
        require(pUsd.accrueInterest() == 0 && pStock.accrueInterest() == 0, "ACCRUE");
        uint256 beforeShares = pUsd.balanceOf(ACTOR);
        usd.approve(P_USD, amount);
        require(pUsd.mint(amount) == 0, "MINT");
        usd.approve(P_USD, 0);
        uint256 shares = pUsd.balanceOf(ACTOR) - beforeShares;
        pUsd.approve(address(marginVault), shares);
        marginVault.deposit(P_USD, shares);
        pUsd.approve(address(marginVault), 0);
        (, uint256 minimum) = quoter.quoteOpen(
            P_USD,
            short ? P_USD : P_STOCK,
            short ? P_STOCK : P_USD,
            Math.mulDiv(shares, pUsd.exchangeRateStored(), 1e18),
            200
        );
        id = executor.openPosition(
            IsolatedMarginExecutorUpgradeable.OpenParams(
                P_USD,
                short ? P_USD : P_STOCK,
                short ? P_STOCK : P_USD,
                shares,
                200,
                0,
                minimum,
                short ? IsolatedMarginTypes.Side.SHORT : IsolatedMarginTypes.Side.LONG,
                ""
            )
        );
        vm.stopPrank();
        IsolatedMarginTypes.AccountMetrics memory metrics = riskEngine.getMetrics(_account(id));
        assertLe(metrics.leverageX100, 200);
        assertGt(metrics.leverageX100, 190);
        assertFalse(riskEngine.isLiquidatable(_account(id)));
        emit log_named_uint("marginUsd6", amount);
        emit log_named_uint("entryGrossUsd18", metrics.grossAssetValueUsd);
        emit log_named_uint("entryDebtUsd18", metrics.debtValueUsd);
        emit log_named_uint("entryLeverageX100", metrics.leverageX100);
        _clean();
    }

    function _roundTrip(bool short, uint256 amount) internal {
        _enable();
        uint256 id = _open(short, amount);
        uint256 returned = _closeAndWithdraw(id);
        emit log_named_uint("returnedMarginUsd6", returned);
        assertGt(returned, amount * 95 / 100);
    }

    function _closeAndWithdraw(uint256 id) internal returns (uint256 returned) {
        vm.prank(ACTOR);
        executor.closePosition(_closeParams(id, 10000));
        uint256 shares = marginVault.freeBalance(ACTOR, P_USD);
        returned = Math.mulDiv(shares, pUsd.exchangeRateStored(), 1e18);
        if (shares > 0) {
            vm.prank(ACTOR);
            marginVault.withdraw(P_USD, shares);
        }
        _assertDebtFree(id);
        assertEq(marginVault.freeBalance(ACTOR, P_USD), 0);
    }

    function _account(uint256 id) internal view returns (address account) {
        (,, account,,,,,,,,,) = executor.positions(id);
    }

    function _closeParams(uint256 id, uint16 bps)
        internal
        pure
        returns (IsolatedMarginExecutorUpgradeable.CloseParams memory)
    {
        return IsolatedMarginExecutorUpgradeable.CloseParams(id, bps, 0, 0, 0, "", "");
    }

    function _liquidationParams(uint256 id)
        internal
        pure
        returns (IsolatedMarginLiquidatorUpgradeable.LiquidationParams memory)
    {
        return IsolatedMarginLiquidatorUpgradeable.LiquidationParams(id, KEEPER, 0, 0, "", "");
    }

    function _assertDebtFree(uint256 id) internal view {
        assertEq(pUsd.borrowBalanceStored(_account(id)), 0);
        assertEq(pStock.borrowBalanceStored(_account(id)), 0);
        assertEq(pUsd.totalBorrows(), 0);
        assertEq(pStock.totalBorrows(), 0);
        assertEq(pUsd.totalBorrowShares(), 0);
        assertEq(pStock.totalBorrowShares(), 0);
        assertEq(marginVault.lockedBalance(ACTOR, P_USD), 0);
        _clean();
    }

    function _clean() internal view {
        address[2] memory assets = [USD, STOCK];
        for (uint256 i; i < 2; ++i) {
            assertEq(IERC20(assets[i]).balanceOf(address(router)), 0);
            assertEq(IERC20(assets[i]).balanceOf(address(swapModule)), 0);
            assertEq(IERC20(assets[i]).allowance(address(router), PERMIT2), 0);
            assertEq(IERC20(assets[i]).allowance(address(swapModule), address(router)), 0);
            (uint160 allowance,,) =
                IAllowanceTransfer(PERMIT2).allowance(address(router), assets[i], UNIVERSAL_ROUTER);
            assertEq(allowance, 0);
        }
    }
}
