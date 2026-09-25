// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;
import { Script, console2 } from "forge-std/Script.sol";
import { RobinhoodMainnetMarginBase } from "../RobinhoodMainnetMarginBase.sol";
import { GuardedMarginPriceSource } from "../GuardedMarginPriceSource.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PeridotTransparentProxy } from "peridot/proxy/PeridotTransparentProxy.sol";
import { Peridottroller } from "peridot/Peridottroller.sol";
import { PErc20 } from "peridot/PErc20.sol";
import { PToken } from "peridot/PToken.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { RobinhoodBoostedDelegate } from "peridot/boosted/RobinhoodBoostedDelegate.sol";
import { StockSimplePriceOracle } from "peridot/StockSimplePriceOracle.sol";
import { SimpleFlashLoanVault } from "peridot/margin/SimpleFlashLoanVault.sol";
import { RobinhoodV4RouterAdapter } from "peridot/margin/RobinhoodV4RouterAdapter.sol";
import { RobinhoodMarginPriceOracle } from "peridot/margin/RobinhoodMarginPriceOracle.sol";
import { IsolatedMarginAccountFactory } from "peridot/margin/IsolatedMarginAccountFactory.sol";
import {
    IsolatedMarginConfigUpgradeable
} from "peridot/margin/IsolatedMarginConfigUpgradeable.sol";
import {
    IsolatedMarginExecutorUpgradeable
} from "peridot/margin/IsolatedMarginExecutorUpgradeable.sol";
import {
    IsolatedMarginLiquidatorUpgradeable
} from "peridot/margin/IsolatedMarginLiquidatorUpgradeable.sol";
import { IsolatedMarginQuoter } from "peridot/margin/IsolatedMarginQuoter.sol";
import {
    IsolatedMarginRiskEngineUpgradeable
} from "peridot/margin/IsolatedMarginRiskEngineUpgradeable.sol";
import { IsolatedMarginSwapModule } from "peridot/margin/IsolatedMarginSwapModule.sol";
import { IsolatedMarginTypes } from "peridot/margin/IsolatedMarginTypes.sol";
import { IsolatedMarginVaultUpgradeable } from "peridot/margin/IsolatedMarginVaultUpgradeable.sol";
import {
    MarginFeeDistributorUpgradeable
} from "peridot/margin/MarginFeeDistributorUpgradeable.sol";
import { MarginInsuranceFundUpgradeable } from "peridot/margin/MarginInsuranceFundUpgradeable.sol";

/// @notice Staged, initially paused mainnet package. Never replay a partly submitted phase.
contract PrepareRobinhoodMainnetMargin is RobinhoodMainnetMarginBase, Script {
    function _confirm() internal {
        _identity();
        uint256 nativeHeight = vm.envUint("MARGIN_EVM_BLOCK_NUMBER");
        require(nativeHeight != 0, "NATIVE_CLOCK_REQUIRED");
        vm.roll(nativeHeight);
    }

    function pauseBorrowing() external {
        _confirm();
        vm.startBroadcast(ACTOR);
        _pauseBorrowing();
        vm.stopBroadcast();
    }

    function migrateMarkets() external {
        _confirm();
        string memory history = vm.readFile(
            vm.envOr(
                "MARGIN_HISTORY",
                string("deployments/robinhood-mainnet.margin-borrower-history.json")
            )
        );
        string memory pin = vm.readFile(
            vm.envOr("MARGIN_PIN", string("deployments/robinhood-mainnet.margin-pin.json"))
        );
        require(vm.parseJsonUint(history, ".chainId") == 4663, "HISTORY_CHAIN");
        require(
            vm.parseJsonUint(history, ".throughBlock") == vm.parseJsonUint(pin, ".stateBlock"),
            "HISTORY_PIN"
        );
        require(vm.parseJsonBool(history, ".allHistoricalBorrowersDebtFree"), "HISTORICAL_DEBT");
        require(
            vm.parseJsonBool(history, ".postPauseSnapshot"), "REFRESH_HISTORY_AFTER_CONFIRMED_PAUSE"
        );
        require(
            vm.parseJsonUint(history, ".borrowEventCount") == 0,
            "BORROW_HISTORY_CHANGED_REVIEW_REQUIRED"
        );
        vm.startBroadcast(ACTOR);
        _migrateMarkets();
        vm.stopBroadcast();
        console2.log("Replacement boosted delegate", address(replacement));
    }

    function deployPaused() external {
        _confirm();
        vm.startBroadcast(ACTOR);
        _deployPaused();
        vm.stopBroadcast();
        _record();
    }

    function applyRisk() external {
        _load();
        vm.startBroadcast(ACTOR);
        _applyRisk();
        vm.stopBroadcast();
    }

    function fundCanary() external {
        _load();
        vm.startBroadcast(ACTOR);
        _fundCanary();
        vm.stopBroadcast();
    }

    function queueActivation() external {
        _load();
        require(config.opensPaused() && flashVault.paused(), "KEEP_PAUSED");
        require(config.queuedActions(keccak256("unpauseOpens")) == 0, "ALREADY_QUEUED");
        vm.startBroadcast(ACTOR);
        config.queueUnpauseOpens();
        vm.stopBroadcast();
    }

    function activate() external {
        _load();
        vm.startBroadcast(ACTOR);
        _activate();
        vm.stopBroadcast();
    }

    function openCanary(bool short) external {
        _load();
        require(
            marginVault.freeBalance(ACTOR, P_USD) == 0
                && marginVault.lockedBalance(ACTOR, P_USD) == 0,
            "MARGIN_REMAINS"
        );
        vm.startBroadcast(ACTOR);
        require(pUsd.accrueInterest() == 0 && pStock.accrueInterest() == 0, "ACCRUE");
        uint256 beforeShares = pUsd.balanceOf(ACTOR);
        usd.approve(P_USD, 0.25e6);
        require(pUsd.mint(0.25e6) == 0, "MINT");
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
        uint256 id = executor.openPosition(
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
        vm.stopBroadcast();
        (,, address account,,,,,,,,,) = executor.positions(id);
        require(!riskEngine.isLiquidatable(account), "UNHEALTHY_OPEN");
        console2.log("Canary position", id);
    }

    function closeCanary(uint256 id) external {
        _load();
        vm.startBroadcast(ACTOR);
        executor.closePosition(
            IsolatedMarginExecutorUpgradeable.CloseParams(id, 10000, 0, 0, 0, "", "")
        );
        vm.stopBroadcast();
    }

    function withdrawCanary() external {
        _load();
        uint256 shares = marginVault.freeBalance(ACTOR, P_USD);
        require(shares > 0 && marginVault.lockedBalance(ACTOR, P_USD) == 0, "CLOSE_FIRST");
        vm.startBroadcast(ACTOR);
        marginVault.withdraw(P_USD, shares);
        vm.stopBroadcast();
    }

    function finishCanary() external {
        _load();
        require(pUsd.totalBorrows() == 0 && pStock.totalBorrows() == 0, "DEBT_REMAINS");
        require(pUsd.totalBorrowShares() == 0 && pStock.totalBorrowShares() == 0, "SHARES_REMAIN");
        require(
            marginVault.freeBalance(ACTOR, P_USD) == 0
                && marginVault.lockedBalance(ACTOR, P_USD) == 0,
            "MARGIN_REMAINS"
        );
        vm.startBroadcast(ACTOR);
        config.pauseOpens();
        flashVault.setPaused(true);
        vm.stopBroadcast();
    }

    function _path() internal view returns (string memory) {
        return vm.envOr(
            "MARGIN_RECORD", string("deployments/robinhood-mainnet.margin-rehearsal-addresses.json")
        );
    }

    function _record() internal {
        string memory key = "margin-mainnet-package";
        vm.serializeUint(key, "chainId", block.chainid);
        vm.serializeString(key, "status", "SIMULATED_ADDRESSES_VERIFY_RECEIPTS_BEFORE_USE");
        vm.serializeAddress(key, "actor", ACTOR);
        vm.serializeAddress(key, "proxyAdminOwner", TIMELOCK);
        vm.serializeAddress(key, "operationalOwner", ACTOR);
        vm.serializeAddress(key, "usd", USD);
        vm.serializeAddress(key, "stock", STOCK);
        vm.serializeAddress(key, "pUsd", P_USD);
        vm.serializeAddress(key, "pStock", P_STOCK);
        vm.serializeAddress(key, "controller", CONTROLLER);
        vm.serializeAddress(key, "assetOracle", ASSET_ORACLE);
        vm.serializeAddress(key, "guard", GUARD);
        vm.serializeAddress(key, "guardedSource", address(guardedSource));
        vm.serializeAddress(key, "router", address(router));
        vm.serializeAddress(key, "oracle", address(oracle));
        vm.serializeAddress(key, "flashVault", address(flashVault));
        vm.serializeAddress(key, "config", address(config));
        vm.serializeAddress(key, "insuranceFund", address(insuranceFund));
        vm.serializeAddress(key, "feeDistributor", address(feeDistributor));
        vm.serializeAddress(key, "marginVault", address(marginVault));
        vm.serializeAddress(key, "riskEngine", address(riskEngine));
        vm.serializeAddress(key, "quoter", address(quoter));
        vm.serializeAddress(key, "swapModule", address(swapModule));
        vm.serializeAddress(key, "executor", address(executor));
        vm.serializeAddress(key, "accountFactory", address(accountFactory));
        vm.writeJson(vm.serializeAddress(key, "liquidator", address(liquidator)), _path());
    }

    function _load() internal {
        _confirm();
        string memory json = vm.readFile(_path());
        require(vm.parseJsonUint(json, ".chainId") == 4663, "RECORD_CHAIN");
        require(vm.parseJsonAddress(json, ".actor") == ACTOR, "RECORD_ACTOR");
        router = RobinhoodV4RouterAdapter(vm.parseJsonAddress(json, ".router"));
        oracle = RobinhoodMarginPriceOracle(vm.parseJsonAddress(json, ".oracle"));
        guardedSource = GuardedMarginPriceSource(vm.parseJsonAddress(json, ".guardedSource"));
        flashVault = SimpleFlashLoanVault(vm.parseJsonAddress(json, ".flashVault"));
        config = IsolatedMarginConfigUpgradeable(vm.parseJsonAddress(json, ".config"));
        insuranceFund = MarginInsuranceFundUpgradeable(vm.parseJsonAddress(json, ".insuranceFund"));
        marginVault = IsolatedMarginVaultUpgradeable(vm.parseJsonAddress(json, ".marginVault"));
        riskEngine = IsolatedMarginRiskEngineUpgradeable(vm.parseJsonAddress(json, ".riskEngine"));
        quoter = IsolatedMarginQuoter(vm.parseJsonAddress(json, ".quoter"));
        executor = IsolatedMarginExecutorUpgradeable(vm.parseJsonAddress(json, ".executor"));
        liquidator = IsolatedMarginLiquidatorUpgradeable(vm.parseJsonAddress(json, ".liquidator"));
        require(
            address(executor.riskEngine()) == address(riskEngine)
                && address(executor.config()) == address(config),
            "RECORD_WIRING"
        );
    }
}
