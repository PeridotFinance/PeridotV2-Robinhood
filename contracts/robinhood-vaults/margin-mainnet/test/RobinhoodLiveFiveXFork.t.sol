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

import { MainnetPoolShock } from "./RobinhoodMainnetMarginFork.t.sol";
import { FiveXRiskUpdate } from "../five-x/FiveXRiskUpdate.sol";
import {
    IsolatedMarginConfigUpgradeable
} from "peridot/margin/IsolatedMarginConfigUpgradeable.sol";
import { IsolatedMarginVaultUpgradeable } from "peridot/margin/IsolatedMarginVaultUpgradeable.sol";
import {
    IsolatedMarginRiskEngineUpgradeable
} from "peridot/margin/IsolatedMarginRiskEngineUpgradeable.sol";
import { IsolatedMarginQuoter } from "peridot/margin/IsolatedMarginQuoter.sol";
import { SimpleFlashLoanVault } from "peridot/margin/SimpleFlashLoanVault.sol";
import { RobinhoodV4RouterAdapter } from "peridot/margin/RobinhoodV4RouterAdapter.sol";
import { IsolatedMarginSwapModule } from "peridot/margin/IsolatedMarginSwapModule.sol";

/// @notice Uses the actual deployed mainnet margin stack. All governance and shocks are fork-only.
contract RobinhoodLiveFiveXForkTest is RobinhoodMainnetMarginBase, FiveXRiskUpdate, Test {
    uint256 internal nativeBlock;
    uint256 internal pinTimestamp;
    address internal constant KEEPER = address(0xB07);

    function setUp() external {
        string memory url = vm.envString("ROBINHOOD_RPC_URL");
        string memory pin = vm.readFile("deployments/robinhood-mainnet.margin-5x-pin.json");
        vm.createSelectFork(url, vm.parseJsonUint(pin, ".stateBlock"));
        nativeBlock = vm.parseJsonUint(pin, ".nativeEvmBlockNumber");
        pinTimestamp = vm.parseJsonUint(pin, ".timestamp");
        vm.roll(nativeBlock);
        assertEq(block.timestamp, pinTimestamp);
        _bindLive();
        _checkFiveXIdentity();
        assertFalse(config.opensPaused());
        assertFalse(flashVault.paused());
        // Real funded actor and existing market/flash balances; no baseline wallet injection.
        assertGe(usd.balanceOf(ACTOR), 1e6);
    }

    function _bindLive() internal {
        string memory a = vm.readFile("deployments/margin-mainnet-live/addresses.json");
        config = IsolatedMarginConfigUpgradeable(vm.parseJsonAddress(a, ".config"));
        executor = IsolatedMarginExecutorUpgradeable(vm.parseJsonAddress(a, ".executor"));
        liquidator = IsolatedMarginLiquidatorUpgradeable(vm.parseJsonAddress(a, ".liquidator"));
        marginVault = IsolatedMarginVaultUpgradeable(vm.parseJsonAddress(a, ".marginVault"));
        riskEngine = IsolatedMarginRiskEngineUpgradeable(vm.parseJsonAddress(a, ".riskEngine"));
        quoter = IsolatedMarginQuoter(vm.parseJsonAddress(a, ".quoter"));
        oracle = RobinhoodMarginPriceOracle(vm.parseJsonAddress(a, ".oracle"));
        flashVault = SimpleFlashLoanVault(vm.parseJsonAddress(a, ".flashVault"));
        router = RobinhoodV4RouterAdapter(vm.parseJsonAddress(a, ".router"));
        swapModule = IsolatedMarginSwapModule(vm.parseJsonAddress(a, ".swapModule"));
    }

    function _enable() internal {
        vm.startPrank(ACTOR);
        _queueFiveX();
        vm.stopPrank();
        vm.warp(block.timestamp + config.actionDelay() + 1);
        vm.roll(nativeBlock + 301);
        vm.startPrank(ACTOR);
        _applyFiveX();
        vm.stopPrank();
    }

    function testFiveXGovernanceDelayAndIdempotency() external {
        vm.startPrank(ACTOR);
        _queueFiveX();
        uint256 ready = fiveXReadyAt(false);
        _queueFiveX();
        assertEq(fiveXReadyAt(false), ready);
        vm.expectRevert("FIVE_X_DELAY");
        this.applyForTest();
        vm.stopPrank();
        vm.warp(ready);
        vm.startPrank(ACTOR);
        _applyFiveX();
        _applyFiveX();
        _queueFiveX();
        vm.stopPrank();
        assertEq(config.getPairRisk(P_USD, P_STOCK, P_USD).maxLeverageX100, 500);
        assertFalse(config.opensPaused());
        assertFalse(flashVault.paused());
    }

    function testFiveXPartialQueueAndApplyRecovery() external {
        vm.startPrank(ACTOR);
        config.queuePairRisk(P_USD, P_STOCK, P_USD, fiveXRisk());
        _queueFiveX();
        vm.warp(block.timestamp + config.actionDelay() + 1);
        config.setPairRisk(P_USD, P_STOCK, P_USD, fiveXRisk());
        _applyFiveX();
        vm.stopPrank();
        assertEq(config.getPairRisk(P_USD, P_USD, P_STOCK).maxLeverageX100, 500);
        assertEq(config.queuedActions(fiveXAction(false)), 0);
        assertEq(config.queuedActions(fiveXAction(true)), 0);
    }

    function testFiveXRefusesUnexpectedExistingRisk() external {
        IsolatedMarginTypes.PairRiskConfig memory unexpected = canaryRisk();
        unexpected.maxDebtValueUsd = 2e18;
        vm.startPrank(ACTOR);
        config.queuePairRisk(P_USD, P_STOCK, P_USD, unexpected);
        vm.warp(block.timestamp + config.actionDelay() + 1);
        config.setPairRisk(P_USD, P_STOCK, P_USD, unexpected);
        vm.stopPrank();
        vm.expectRevert("UNEXPECTED_PAIR_RISK");
        this.queueForTest();
    }

    function queueForTest() external {
        _queueFiveX();
    }

    function applyForTest() external {
        _applyFiveX();
    }

    function testFiveXLongRoundTrip() external {
        _roundTrip(false, 0.2e6);
    }

    function testFiveXShortRoundTrip() external {
        _roundTrip(true, 0.2e6);
    }

    function testFiveXLongDebtCap() external {
        _enable();
        vm.expectRevert();
        this.openForTest(false, 0.3e6);
    }

    function testFiveXShortDebtCap() external {
        _enable();
        vm.expectRevert();
        this.openForTest(true, 0.3e6);
    }

    function openForTest(bool short, uint256 amount) external returns (uint256) {
        return _open(short, amount);
    }

    function testFiveXCeiling() external {
        _enable();
        vm.expectRevert();
        quoter.quoteOpen(P_USD, P_STOCK, P_USD, 0.2e6, 501);
    }

    function testFiveXPartialThenFullClose() external {
        _enable();
        uint256 id = _open(false, 0.2e6);
        uint256 debt = pUsd.borrowBalanceStored(_account(id));
        vm.prank(ACTOR);
        executor.closePosition(_closeParams(id, 5000));
        assertLt(pUsd.borrowBalanceStored(_account(id)), debt);
        assertGt(pUsd.borrowBalanceStored(_account(id)), 0);
        _closeAndWithdraw(id);
    }

    function testFiveXStalePriceRepayExit() external {
        _enable();
        uint256 id = _open(false, 0.2e6);
        vm.warp(block.timestamp + 13 hours);
        vm.roll(block.number + 3900);
        vm.expectRevert();
        quoter.quoteOpen(P_USD, P_STOCK, P_USD, 0.2e6, 500);
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

    function testFiveXLongBoundary() external {
        _boundary(false, false);
    }

    function testFiveXShortBoundary() external {
        _boundary(true, false);
    }

    function testFiveXLongBoostedBoundary() external {
        _boundary(false, true);
    }

    function testFiveXShortBoostedBoundary() external {
        _boundary(true, true);
    }

    function testFiveXLongSevereShock() external {
        _enable();
        uint256 id = _open(false, 0.2e6);
        _makeLp();
        _shock(6000);
        _liquidateCanary(id);
    }

    function testFiveXShortSevereShock() external {
        _enable();
        uint256 id = _open(true, 0.2e6);
        _makeLp();
        _shock(17000);
        _liquidateCanary(id);
    }

    function _boundary(bool short, bool boosted) internal {
        _enable();
        uint256 id = _open(short, 0.2e6);
        uint256 entry = oracle.getPrice(STOCK);
        if (boosted) _makeLp();
        vm.prank(KEEPER);
        vm.expectRevert();
        liquidator.liquidate(_liquidationParams(id));
        uint256 snap = vm.snapshotState();
        uint16 low = 0;
        uint16 high = 3000;
        while (high - low > 1) {
            uint16 mid = (high + low) / 2;
            _shock(short ? 10000 + mid : 10000 - mid);
            if (riskEngine.isLiquidatable(_account(id))) high = mid;
            else low = mid;
            assertTrue(vm.revertToState(snap));
            snap = vm.snapshotState();
        }
        _shock(short ? 10000 + low : 10000 - low);
        assertFalse(riskEngine.isLiquidatable(_account(id)));
        emit log_named_uint("lastHealthyPriceUsd18", oracle.getPrice(STOCK));
        vm.prank(KEEPER);
        vm.expectRevert();
        liquidator.liquidate(_liquidationParams(id));
        assertTrue(vm.revertToState(snap));
        _shock(short ? 10000 + high : 10000 - high);
        uint256 trigger = oracle.getPrice(STOCK);
        emit log_named_uint("entryOraclePriceUsd18", entry);
        emit log_named_uint("firstLiquidatablePriceUsd18", trigger);
        emit log_named_uint(
            "adverseMoveFromEntryBps",
            Math.mulDiv(short ? trigger - entry : entry - trigger, 10000, entry)
        );
        emit log_named_uint("poolMoveBps", high);
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
            500
        );
        id = executor.openPosition(
            IsolatedMarginExecutorUpgradeable.OpenParams(
                P_USD,
                short ? P_USD : P_STOCK,
                short ? P_STOCK : P_USD,
                shares,
                500,
                0,
                minimum,
                short ? IsolatedMarginTypes.Side.SHORT : IsolatedMarginTypes.Side.LONG,
                ""
            )
        );
        vm.stopPrank();
        IsolatedMarginTypes.AccountMetrics memory metrics = riskEngine.getMetrics(_account(id));
        assertLe(metrics.leverageX100, 500);
        assertGt(metrics.leverageX100, 450);
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
        assertGt(returned, amount * 90 / 100);
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
