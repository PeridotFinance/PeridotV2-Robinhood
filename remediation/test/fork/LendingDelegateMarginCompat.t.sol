// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PErc20 } from "peridot/PErc20.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import {
    IsolatedMarginExecutorUpgradeable
} from "peridot/margin/IsolatedMarginExecutorUpgradeable.sol";
import {
    IsolatedMarginLiquidatorUpgradeable
} from "peridot/margin/IsolatedMarginLiquidatorUpgradeable.sol";
import { IsolatedMarginTypes } from "peridot/margin/IsolatedMarginTypes.sol";
import { IsolatedMarginVaultUpgradeable } from "peridot/margin/IsolatedMarginVaultUpgradeable.sol";
import {
    IsolatedMarginRiskEngineUpgradeable
} from "peridot/margin/IsolatedMarginRiskEngineUpgradeable.sol";
import { IsolatedMarginQuoter } from "peridot/margin/IsolatedMarginQuoter.sol";
import { UniswapV4PairedAdapter } from "baseline/src/UniswapV4PairedAdapter.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { IPoolManager } from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import { IUnlockCallback } from "@uniswap/v4-core/src/interfaces/callback/IUnlockCallback.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";
import { Currency } from "@uniswap/v4-core/src/types/Currency.sol";
import { BalanceDelta } from "@uniswap/v4-core/src/types/BalanceDelta.sol";
import { StateLibrary } from "@uniswap/v4-core/src/libraries/StateLibrary.sol";
import { PoolIdLibrary } from "@uniswap/v4-core/src/types/PoolId.sol";
import { RobinhoodBoostedDelegateV2 } from "../../src/RobinhoodBoostedDelegateV2.sol";

interface IGuardPrices {
    function pricesUSD18(bytes32 pairId) external view returns (uint256, uint256);
}

/// @dev Scenario driver only: moves the forked pool to a price limit across real ticks.
contract CompatPoolShock is IUnlockCallback {
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

/// @notice The candidate delegate against the LIVE margin stack (executor, liquidator, margin vault,
/// risk engine, quoter, swap module, flash vault), at the latest block, on a local fork only.
/// @dev Margin positions are the only other contracts that call these markets' `mint`. Every flow
/// here runs with the candidate installed on BOTH markets. Needs a fresh stock feed (the margin
/// oracle fails closed otherwise) and skips, loudly, when the feed is stale.
/// Local fork conveniences: actor top-up via `deal`, and (liquidation only) a pool move plus a
/// mocked feed answer that tracks it. All lending, vault and router bytecode stays real.
contract LendingDelegateMarginCompatForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant USD = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant P_USD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant P_STOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
    address constant PAIRED_ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant POOL_MANAGER = 0x8366a39CC670B4001A1121B8F6A443A643e40951;
    address constant KEEPER = address(0xB07);
    bytes32 constant PAIR = keccak256("NVDA/USDG");

    // Live margin stack (deployments/margin-mainnet-live/addresses.json).
    IsolatedMarginExecutorUpgradeable constant executor =
        IsolatedMarginExecutorUpgradeable(0x6A45Ae86bD992d250580d08D340A06A04D478977);
    IsolatedMarginLiquidatorUpgradeable constant liquidator =
        IsolatedMarginLiquidatorUpgradeable(0x1434CDa56d0Aeac4d5abC16F91ca76a8A989083c);
    IsolatedMarginVaultUpgradeable constant marginVault =
        IsolatedMarginVaultUpgradeable(0x04D4A5555b7a37017A67B4D21A1Da5838de28B9e);
    IsolatedMarginRiskEngineUpgradeable constant riskEngine =
        IsolatedMarginRiskEngineUpgradeable(0xC8b178C3c74570472FF1eeE0DD559e61AF9f9678);
    IsolatedMarginQuoter constant quoter =
        IsolatedMarginQuoter(0xeD3c353Ab237329BD53CC7eB24E66B370155FE6e);

    PErc20 constant pUsd = PErc20(P_USD);
    PErc20 constant pStock = PErc20(P_STOCK);
    IERC20 constant usd = IERC20(USD);
    IERC20 constant stock = IERC20(STOCK);

    uint256 internal baseUsdBorrows;
    uint256 internal baseStockBorrows;
    uint256 internal baseUsdShares;
    uint256 internal baseStockShares;
    bool internal staleFeed;

    bytes32 constant ORIGINAL_CODEHASH =
        0xa6913bd52087e56926b3f17fd131b7f331af194aaa77321e452582e75fb8cc34;
    /// True once the corrected delegate is installed on chain (it is, since October 2, 2026).
    bool internal alreadyInstalled;

    function setUp() public virtual {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        alreadyInstalled =
            PErc20Delegator(payable(P_STOCK)).implementation().codehash != ORIGINAL_CODEHASH;
        try IGuardPrices(GUARD).pricesUSD18(PAIR) returns (uint256 s, uint256 u) {
            if (s == 0 || u == 0) staleFeed = true;
        } catch {
            staleFeed = true;
        }
        if (staleFeed) {
            emit log("SKIPPED: stock feed is stale, so the margin oracle fails closed. Rerun when fresh.");
            vm.skip(true);
        }
        require(!executor.config().opensPaused(), "OPENS_PAUSED");
        if (usd.balanceOf(GOVERNOR) < 6e6) deal(USD, GOVERNOR, 6e6);
        baseUsdBorrows = pUsd.totalBorrows();
        baseStockBorrows = pStock.totalBorrows();
        baseUsdShares = pUsd.totalBorrowShares();
        baseStockShares = pStock.totalBorrowShares();
    }

    function _install() internal returns (RobinhoodBoostedDelegateV2 candidate) {
        if (alreadyInstalled) {
            return RobinhoodBoostedDelegateV2(PErc20Delegator(payable(P_STOCK)).implementation());
        }
        candidate = new RobinhoodBoostedDelegateV2();
        vm.startPrank(GOVERNOR);
        PErc20Delegator(payable(P_STOCK))._setImplementation(address(candidate), false, "");
        PErc20Delegator(payable(P_USD))._setImplementation(address(candidate), false, "");
        vm.stopPrank();
        assertEq(PErc20Delegator(payable(P_STOCK)).implementation(), address(candidate));
        assertEq(PErc20Delegator(payable(P_USD)).implementation(), address(candidate));
    }

    // ------------------------------------------------------------------ round trips

    function testLongRoundTripWithCandidateInstalled() public {
        _install();
        uint256 id = _open(false, 0.2e6);
        uint256 returned = _closeAndWithdraw(id);
        assertGt(returned, 0.2e6 * 90 / 100);
    }

    function testShortRoundTripWithCandidateInstalled() public {
        _install();
        uint256 id = _open(true, 0.2e6);
        uint256 returned = _closeAndWithdraw(id);
        assertGt(returned, 0.2e6 * 90 / 100);
    }

    function testPartialThenFullCloseBothDirections() public {
        _install();
        for (uint256 d; d < 2; ++d) {
            bool short = d == 1;
            uint256 id = _open(short, 0.2e6);
            address debtMarket = short ? P_STOCK : P_USD;
            uint256 debt = PErc20(debtMarket).borrowBalanceStored(_account(id));
            vm.prank(GOVERNOR);
            executor.closePosition(_closeParams(id, 5000));
            uint256 remaining = PErc20(debtMarket).borrowBalanceStored(_account(id));
            assertLt(remaining, debt);
            assertGt(remaining, 0);
            _closeAndWithdraw(id);
        }
    }

    function testRepayWithUnderlyingThenDebtFreeExit() public {
        _install();
        uint256 id = _open(false, 0.2e6);
        vm.startPrank(GOVERNOR);
        usd.approve(address(executor), type(uint256).max);
        executor.repayWithUnderlying(id, type(uint256).max);
        usd.approve(address(executor), 0);
        executor.exitDebtFreeToPTokens(id, 0);
        vm.stopPrank();
        _assertDebtFree(id);
    }

    // ------------------------------------------------------------------ liquidation

    function testLongSevereShockLiquidationWithCandidateInstalled() public {
        _install();
        uint256 id = _open(false, 0.2e6);
        _shock(6000);
        _liquidate(id);
    }

    function testShortSevereShockLiquidationWithCandidateInstalled() public {
        _install();
        uint256 id = _open(true, 0.2e6);
        _shock(17000);
        _liquidate(id);
    }

    // ------------------------------------------------------------------ repayWithPToken remainder mint

    /// A USDG remainder re-mints through the candidate: one raw USDG unit is already 5,000 shares.
    function testRepayWithPTokenRemainderMintsOnUsdgDebt() public {
        _install();
        uint256 id = _open(false, 0.2e6);
        address account = _account(id);
        uint256 debt = pUsd.borrowBalanceCurrent(account);
        vm.startPrank(GOVERNOR);
        uint256 before = pUsd.balanceOf(GOVERNOR);
        usd.approve(P_USD, 1e6);
        require(pUsd.mint(1e6) == 0, "MINT");
        usd.approve(P_USD, 0);
        uint256 shares = pUsd.balanceOf(GOVERNOR) - before;
        pUsd.approve(address(executor), shares);
        uint256 sharesBeforeRepay = pUsd.balanceOf(GOVERNOR);
        executor.repayWithPToken(id, shares);
        vm.stopPrank();
        assertEq(pUsd.borrowBalanceStored(account), 0, "debt repaid");
        assertGt(
            pUsd.balanceOf(GOVERNOR), sharesBeforeRepay - shares, "remainder returned as shares"
        );
        assertGt(1e6, debt);
    }

    /// An NVDA-debt `repayWithPToken` whose remainder is below one share reverts on BOTH delegates:
    /// the executor already refuses a zero-share re-mint (the risk engine rejects a zero movement).
    /// The candidate changes only the revert reason, so this path gains no new failure.
    function testShortRepayWithPTokenNvdaDustRemainderRevertsBeforeAndAfter() public {
        uint256 id = _open(true, 0.2e6);
        address account = _account(id);
        uint256 debt = pStock.borrowBalanceCurrent(account);
        uint256 rate = pStock.exchangeRateCurrent();
        uint256 shares = Math.mulDiv(debt, 1e18, rate, Math.Rounding.Ceil);
        uint256 remainder = Math.mulDiv(shares, rate, 1e18) - debt;
        uint256 oneShare = rate / 1e18;
        if (remainder == 0 || remainder >= oneShare) {
            emit log("remainder is not dust for this state; scenario not applicable");
            return;
        }
        uint256 snap = vm.snapshotState();

        if (!alreadyInstalled) {
            _giveShares(P_STOCK, STOCK, shares);
            vm.startPrank(GOVERNOR);
            pStock.approve(address(executor), shares);
            vm.expectRevert(bytes("RiskEngine: zero movement"));
            executor.repayWithPToken(id, shares);
            vm.stopPrank();
            assertTrue(vm.revertToState(snap));
        }

        _install();
        _giveShares(P_STOCK, STOCK, shares);
        vm.startPrank(GOVERNOR);
        pStock.approve(address(executor), shares);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        executor.repayWithPToken(id, shares);
        vm.stopPrank();
        assertGt(pStock.borrowBalanceStored(account), 0, "nothing was repaid");
    }

    /// The legacy no-op mint(0) also changed from success to a revert.
    function testZeroAmountMintNowRevertsAfterInstall() public {
        if (!alreadyInstalled) {
            vm.prank(GOVERNOR);
            assertEq(pUsd.mint(0), 0, "original accepts mint(0)");
        }
        _install();
        vm.prank(GOVERNOR);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        pUsd.mint(0);
    }

    // ------------------------------------------------------------------ helpers

    function _giveShares(address market, address asset, uint256 shares) internal {
        uint256 rate = PErc20(market).exchangeRateCurrent();
        uint256 needed = Math.mulDiv(shares, rate, 1e18, Math.Rounding.Ceil) + 1e9;
        if (IERC20(asset).balanceOf(GOVERNOR) < needed) deal(asset, GOVERNOR, needed);
        uint256 before = PErc20(market).balanceOf(GOVERNOR);
        vm.startPrank(GOVERNOR);
        IERC20(asset).approve(market, needed);
        require(PErc20(market).mint(needed) == 0, "MINT");
        IERC20(asset).approve(market, 0);
        vm.stopPrank();
        require(PErc20(market).balanceOf(GOVERNOR) - before >= shares, "SHARES");
    }

    function _open(bool short, uint256 amount) internal returns (uint256 id) {
        vm.startPrank(GOVERNOR);
        require(pUsd.accrueInterest() == 0 && pStock.accrueInterest() == 0, "ACCRUE");
        uint256 beforeShares = pUsd.balanceOf(GOVERNOR);
        usd.approve(P_USD, amount);
        require(pUsd.mint(amount) == 0, "MINT");
        usd.approve(P_USD, 0);
        uint256 shares = pUsd.balanceOf(GOVERNOR) - beforeShares;
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
    }

    function _closeAndWithdraw(uint256 id) internal returns (uint256 returned) {
        vm.prank(GOVERNOR);
        executor.closePosition(_closeParams(id, 10000));
        uint256 shares = marginVault.freeBalance(GOVERNOR, P_USD);
        returned = Math.mulDiv(shares, pUsd.exchangeRateStored(), 1e18);
        if (shares > 0) {
            vm.prank(GOVERNOR);
            marginVault.withdraw(P_USD, shares);
        }
        _assertDebtFree(id);
        assertEq(marginVault.freeBalance(GOVERNOR, P_USD), 0);
    }

    function _liquidate(uint256 id) internal {
        address account = _account(id);
        assertTrue(riskEngine.isLiquidatable(account));
        vm.prank(KEEPER);
        liquidator.liquidate(
            IsolatedMarginLiquidatorUpgradeable.LiquidationParams(id, KEEPER, 0, 0, "", "")
        );
        (,,,,,,,,,,, IsolatedMarginTypes.Status status) = executor.positions(id);
        assertEq(uint256(status), uint256(IsolatedMarginTypes.Status.LIQUIDATED));
        uint256 free = marginVault.freeBalance(GOVERNOR, P_USD);
        if (free > 0) {
            vm.prank(GOVERNOR);
            marginVault.withdraw(P_USD, free);
        }
        _assertDebtFree(id);
    }

    function _shock(uint16 priceBps) internal {
        PoolKey memory key = UniswapV4PairedAdapter(PAIRED_ADAPTER).poolKey(PAIR);
        IPoolManager manager = IPoolManager(POOL_MANAGER);
        (uint160 current,,,) = StateLibrary.getSlot0(manager, PoolIdLibrary.toId(key));
        uint160 target = uint160(Math.mulDiv(current, 1e11, Math.sqrt(uint256(priceBps) * 1e18)));
        CompatPoolShock mover = new CompatPoolShock(manager);
        deal(USD, address(mover), 1_000_000e6);
        deal(STOCK, address(mover), 10_000e18);
        bool zeroForOne = target < current;
        mover.move(key, target, zeroForOne, zeroForOne ? 1_000_000e6 : 10_000e18);
        (uint160 afterPrice, int24 tick,,) = StateLibrary.getSlot0(manager, PoolIdLibrary.toId(key));
        assertEq(afterPrice, target, "scenario must actually reach target price");
        uint256 answer = VaultMath.quoteAtTick(tick, 1e18, STOCK, USD) * 100;
        // Only the external feed answer is overridden, to track the genuine pool move.
        vm.mockCall(
            FEED,
            abi.encodeCall(IAggregatorV3.latestRoundData, ()),
            abi.encode(uint80(1), int256(answer), block.timestamp, block.timestamp, uint80(1))
        );
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

    /// The position's own debt is cleared and market-wide borrows are back to where they started.
    function _assertDebtFree(uint256 id) internal view {
        address account = _account(id);
        assertEq(pUsd.borrowBalanceStored(account), 0);
        assertEq(pStock.borrowBalanceStored(account), 0);
        assertEq(pUsd.totalBorrows(), baseUsdBorrows);
        assertEq(pStock.totalBorrows(), baseStockBorrows);
        assertEq(pUsd.totalBorrowShares(), baseUsdShares);
        assertEq(pStock.totalBorrowShares(), baseStockShares);
        assertEq(marginVault.lockedBalance(GOVERNOR, P_USD), 0);
    }
}
