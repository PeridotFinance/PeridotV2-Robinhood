// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

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
import { GuardedMarginPriceSource } from "./GuardedMarginPriceSource.sol";

/// @dev Shared deployment logic for the mainnet script and fork tests.
abstract contract RobinhoodMainnetMarginBase {
    address public constant ACTOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address public constant USD = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address public constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address public constant P_USD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address public constant P_STOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address public constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address public constant ASSET_ORACLE = 0x266F014d1325774F1190f963Df4369E07dDA1d33;
    address public constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address public constant BOOSTED_VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address public constant PAIRED_ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address public constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address public constant UNIVERSAL_ROUTER = 0x8876789976dEcBfCbBbe364623C63652db8C0904;
    address public constant POOL_MANAGER = 0x8366a39CC670B4001A1121B8F6A443A643e40951;
    address public constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    address public constant FEED = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;

    bytes32 public constant PAIR = keccak256("NVDA/USDG");
    bytes32 public constant LEGACY_CODEHASH =
        0x9e40397488f537aae7be96a4cc23e409a18b5fa722bc2dd71d8473adf2185481;
    address public actor = ACTOR;
    IERC20 public usd = IERC20(USD);
    IERC20 public stock = IERC20(STOCK);
    PErc20 public pUsd = PErc20(P_USD);
    PErc20 public pStock = PErc20(P_STOCK);
    Peridottroller public controller = Peridottroller(CONTROLLER);
    GuardedMarginPriceSource public guardedSource;
    RobinhoodBoostedDelegate public replacement;
    RobinhoodV4RouterAdapter public router;
    RobinhoodMarginPriceOracle public oracle;
    SimpleFlashLoanVault public flashVault;
    IsolatedMarginConfigUpgradeable public config;
    MarginInsuranceFundUpgradeable public insuranceFund;
    MarginFeeDistributorUpgradeable public feeDistributor;
    IsolatedMarginVaultUpgradeable public marginVault;
    IsolatedMarginRiskEngineUpgradeable public riskEngine;
    IsolatedMarginQuoter public quoter;
    IsolatedMarginSwapModule public swapModule;
    IsolatedMarginExecutorUpgradeable public executor;
    IsolatedMarginAccountFactory public accountFactory;
    IsolatedMarginLiquidatorUpgradeable public liquidator;

    function canaryRisk() public pure returns (IsolatedMarginTypes.PairRiskConfig memory) {
        return IsolatedMarginTypes.PairRiskConfig(
            true, 200, 5000, 2500, 12500, 5000, 5000, 500, 100, 100, 2e18, 1e18
        );
    }

    function _identity() internal view {
        require(block.chainid == 4663, "MAINNET_CHAIN_ONLY");
        require(
            controller.admin() == ACTOR && pUsd.admin() == ACTOR && pStock.admin() == ACTOR,
            "ADMIN_CHANGED"
        );
        require(pUsd.underlying() == USD && pStock.underlying() == STOCK, "MARKET_IDENTITY");
        require(address(controller.oracle()) == ASSET_ORACLE, "LENDING_ORACLE_CHANGED");
    }

    function _pauseBorrowing() internal {
        _identity();
        require(
            pUsd.totalBorrows() == 0 && pStock.totalBorrows() == 0,
            "LIVE_DEBT_REQUIRES_SEPARATE_MIGRATION"
        );
        require(
            !controller.borrowGuardianPaused(P_USD) && !controller.borrowGuardianPaused(P_STOCK),
            "RECONCILE_PAUSE_FIRST"
        );
        require(controller._setBorrowPaused(PToken(P_USD), true), "USD_PAUSE");
        require(controller._setBorrowPaused(PToken(P_STOCK), true), "STOCK_PAUSE");
    }

    function _marketSnapshot(PErc20 market) internal view returns (bytes32) {
        RobinhoodBoostedDelegate d = RobinhoodBoostedDelegate(address(market));
        return keccak256(
            abi.encode(
                market.totalSupply(),
                market.totalReserves(),
                market.getCash(),
                market.exchangeRateStored(),
                market.balanceOf(ACTOR),
                d.vaultAccountedAssets(),
                address(d.robinhoodVault()),
                d.robinhoodPairId(),
                d.vaultBufferMantissa(),
                d.vaultOperator(),
                d.vaultPaused(),
                d.actionDelay(),
                address(market.peridottroller()),
                market.admin()
            )
        );
    }

    function _migrateMarkets() internal {
        _identity();
        require(
            controller.borrowGuardianPaused(P_USD) && controller.borrowGuardianPaused(P_STOCK),
            "PAUSE_BORROWING_FIRST"
        );
        require(pUsd.totalBorrows() == 0 && pStock.totalBorrows() == 0, "DEBT_CHANGED");
        address oldUsd = PErc20Delegator(payable(P_USD)).implementation();
        address oldStock = PErc20Delegator(payable(P_STOCK)).implementation();
        require(
            oldUsd == oldStock && oldUsd.codehash == LEGACY_CODEHASH,
            "LEGACY_IMPLEMENTATION_CHANGED"
        );
        bytes32 beforeUsd = _marketSnapshot(pUsd);
        bytes32 beforeStock = _marketSnapshot(pStock);
        replacement = new RobinhoodBoostedDelegate();
        require(address(replacement).code.length <= 24576, "DELEGATE_SIZE");
        // Boosted become-data configures the vault; it does not migrate borrower accounting.
        // Borrowing remains paused across these separate upgrade and activation transactions.
        PErc20Delegator(payable(P_USD))._setImplementation(address(replacement), false, "");
        pUsd.activateBorrowAccounting(new address[](0), 0, 0);
        PErc20Delegator(payable(P_STOCK))._setImplementation(address(replacement), false, "");
        pStock.activateBorrowAccounting(new address[](0), 0, 0);
        require(
            pUsd.borrowAccountingEnabled() && pStock.borrowAccountingEnabled(),
            "ACCOUNTING_NOT_ENABLED"
        );
        require(pUsd.totalBorrowShares() == 0 && pStock.totalBorrowShares() == 0, "BORROW_SHARES");
        require(
            _marketSnapshot(pUsd) == beforeUsd && _marketSnapshot(pStock) == beforeStock,
            "MIGRATION_CHANGED_ASSETS_OR_CONFIG"
        );
    }

    function _deployPaused() internal {
        _identity();
        require(pUsd.borrowAccountingEnabled() && pStock.borrowAccountingEnabled(), "MIGRATE_FIRST");
        require(
            controller.isolatedMarginRiskHook() == address(0)
                && controller.isolatedMarginRegistrar() == address(0),
            "MARGIN_ALREADY_WIRED"
        );
        router = new RobinhoodV4RouterAdapter(ACTOR, UNIVERSAL_ROUTER, PERMIT2);
        router.registerPool(USD, STOCK, 3000, 60);
        guardedSource = new GuardedMarginPriceSource(ASSET_ORACLE, GUARD, PAIR, STOCK, USD);
        oracle = new RobinhoodMarginPriceOracle(ACTOR, address(guardedSource));
        oracle.registerMarket(P_USD, USD, true);
        oracle.registerMarket(P_STOCK, STOCK, true);
        flashVault = new SimpleFlashLoanVault(ACTOR);
        flashVault.setPaused(true);
        flashVault.setTokenAllowed(USD, true);
        flashVault.setTokenAllowed(STOCK, true);
        _deployMarginStack();
        _configureMarginStack();
        router.setManager(address(swapModule));
        require(config.opensPaused() && flashVault.paused(), "DEPLOYMENT_MUST_REMAIN_PAUSED");
        require(config.queuedActions(keccak256("unpauseOpens")) == 0, "ACTIVATION_MUST_BE_SEPARATE");
    }

    function _resumeBorrowingAfterMigration() internal {
        _identity();
        require(pUsd.borrowAccountingEnabled() && pStock.borrowAccountingEnabled(), "MIGRATE_FIRST");
        require(
            controller.borrowGuardianPaused(P_USD) && controller.borrowGuardianPaused(P_STOCK),
            "RECONCILE_PAUSE_FIRST"
        );
        require(!controller._setBorrowPaused(PToken(P_USD), false), "USD_UNPAUSE");
        require(!controller._setBorrowPaused(PToken(P_STOCK), false), "STOCK_UNPAUSE");
    }

    function _applyRisk() internal {
        config.setPairRisk(P_USD, P_STOCK, P_USD, canaryRisk());
        config.setPairRisk(P_USD, P_USD, P_STOCK, canaryRisk());
        require(config.opensPaused() && flashVault.paused(), "KEEP_PAUSED");
    }

    function _fundCanary() internal {
        require(config.opensPaused() && flashVault.paused(), "FUND_WHILE_PAUSED");
        require(
            usd.balanceOf(address(flashVault)) == 0 && stock.balanceOf(address(flashVault)) == 0,
            "ALREADY_FUNDED"
        );
        require(pUsd.balanceOf(address(insuranceFund)) == 0, "INSURANCE_ALREADY_FUNDED");
        usd.approve(address(flashVault), 2e6);
        flashVault.depositLiquidity(USD, 2e6);
        usd.approve(address(flashVault), 0);
        stock.approve(address(flashVault), 0.01e18);
        flashVault.depositLiquidity(STOCK, 0.01e18);
        stock.approve(address(flashVault), 0);
        uint256 shares = Math.mulDiv(1e6, 1e18, pUsd.exchangeRateCurrent(), Math.Rounding.Ceil);
        require(pUsd.balanceOf(ACTOR) > shares, "PRESERVE_MARKET_SEED");
        require(pUsd.transfer(address(insuranceFund), shares), "INSURANCE_TRANSFER");
    }

    function _activate() internal {
        _identity();
        require(config.opensPaused() && flashVault.paused(), "KEEP_PAUSED_UNTIL_ACTIVATION");
        require(
            pUsd.borrowAccountingEnabled() && pStock.borrowAccountingEnabled(),
            "ACCOUNTING_REQUIRED"
        );
        require(
            controller.isolatedMarginRiskHook() == address(riskEngine)
                && controller.isolatedMarginRegistrar() == address(riskEngine),
            "MARGIN_WIRING_CHANGED"
        );
        require(
            config.routerAdapter() == address(router)
                && config.flashLoanProvider() == address(flashVault)
                && address(oracle.assetSource()) == address(guardedSource),
            "EXECUTION_ENDPOINT_CHANGED"
        );
        require(
            oracle.marketPriceable(P_USD) && oracle.marketPriceable(P_STOCK),
            "FRESH_PRICES_REQUIRED"
        );
        bytes32 expectedRisk = keccak256(abi.encode(canaryRisk()));
        require(
            keccak256(abi.encode(config.getPairRisk(P_USD, P_STOCK, P_USD))) == expectedRisk
                && keccak256(abi.encode(config.getPairRisk(P_USD, P_USD, P_STOCK))) == expectedRisk,
            "RISK_NOT_APPLIED"
        );
        require(
            usd.balanceOf(address(flashVault)) >= 2e6
                && stock.balanceOf(address(flashVault)) >= 0.01e18,
            "FLASH_FUNDING"
        );
        require(pUsd.balanceOf(address(insuranceFund)) > 0, "INSURANCE_FUNDING");
        require(!controller._setBorrowPaused(PToken(P_USD), false), "USD_UNPAUSE");
        require(!controller._setBorrowPaused(PToken(P_STOCK), false), "STOCK_UNPAUSE");
        flashVault.setPaused(false);
        config.unpauseOpens();
    }

    function _proxy(address implementation, bytes memory init) internal returns (address) {
        return address(new PeridotTransparentProxy(implementation, TIMELOCK, init));
    }

    function _deployMarginStack() internal {
        insuranceFund = MarginInsuranceFundUpgradeable(
            _proxy(
                address(new MarginInsuranceFundUpgradeable()),
                abi.encodeWithSelector(MarginInsuranceFundUpgradeable.initialize.selector, actor)
            )
        );
        config = IsolatedMarginConfigUpgradeable(
            _proxy(
                address(new IsolatedMarginConfigUpgradeable()),
                abi.encodeWithSelector(
                    IsolatedMarginConfigUpgradeable.initialize.selector,
                    actor,
                    1 hours,
                    address(router),
                    address(flashVault),
                    address(insuranceFund),
                    actor
                )
            )
        );
        feeDistributor = MarginFeeDistributorUpgradeable(
            _proxy(
                address(new MarginFeeDistributorUpgradeable()),
                abi.encodeWithSelector(
                    MarginFeeDistributorUpgradeable.initialize.selector, actor, address(config)
                )
            )
        );
        marginVault = IsolatedMarginVaultUpgradeable(
            _proxy(
                address(new IsolatedMarginVaultUpgradeable()),
                abi.encodeWithSelector(
                    IsolatedMarginVaultUpgradeable.initialize.selector,
                    actor,
                    address(feeDistributor)
                )
            )
        );
        riskEngine = IsolatedMarginRiskEngineUpgradeable(
            _proxy(
                address(new IsolatedMarginRiskEngineUpgradeable()),
                abi.encodeWithSelector(
                    IsolatedMarginRiskEngineUpgradeable.initialize.selector,
                    actor,
                    address(config),
                    address(oracle),
                    address(controller)
                )
            )
        );
        quoter = new IsolatedMarginQuoter(address(config), address(oracle));
        swapModule = new IsolatedMarginSwapModule(address(config), address(quoter));
        accountFactory = new IsolatedMarginAccountFactory(actor);
        executor = IsolatedMarginExecutorUpgradeable(
            _proxy(
                address(new IsolatedMarginExecutorUpgradeable()),
                abi.encodeWithSelector(
                    IsolatedMarginExecutorUpgradeable.initialize.selector,
                    address(config),
                    address(riskEngine),
                    address(marginVault),
                    address(feeDistributor),
                    address(quoter),
                    address(swapModule),
                    address(accountFactory)
                )
            )
        );
        accountFactory.setExecutor(address(executor));
        liquidator = IsolatedMarginLiquidatorUpgradeable(
            _proxy(
                address(new IsolatedMarginLiquidatorUpgradeable()),
                abi.encodeWithSelector(
                    IsolatedMarginLiquidatorUpgradeable.initialize.selector,
                    address(executor),
                    address(config),
                    address(riskEngine),
                    address(marginVault),
                    address(insuranceFund),
                    address(quoter),
                    address(swapModule)
                )
            )
        );
    }

    function _configureMarginStack() internal {
        riskEngine.setOperators(address(executor), address(liquidator));
        require(controller._setIsolatedMarginRiskHook(address(riskEngine)) == 0, "HOOK");
        require(controller._setIsolatedMarginRegistrar(address(riskEngine)) == 0, "REGISTRAR");
        marginVault.setExecutor(address(executor));
        marginVault.setLiquidator(address(liquidator));
        marginVault.setPTokenAllowed(address(pUsd), true);
        marginVault.setPTokenAllowed(address(pStock), true);
        feeDistributor.setVault(address(marginVault));
        feeDistributor.setFeeCollector(address(marginVault), true);
        feeDistributor.setFeeCollector(address(executor), true);
        insuranceFund.setLiquidator(address(liquidator));

        IsolatedMarginTypes.PairRiskConfig memory pairRisk = IsolatedMarginTypes.PairRiskConfig({
            enabled: true,
            maxLeverageX100: 200,
            initialMarginBps: 5_000,
            maintenanceMarginBps: 2_500,
            liquidationTargetBps: 12_500,
            fullLiquidationHealthBps: 5_000,
            maxLiquidationBps: 5_000,
            liquidationBonusBps: 500,
            maxSlippageBps: 100,
            oracleDeviationBps: 100,
            maxPositionValueUsd: 2e18,
            maxDebtValueUsd: 1e18
        });
        config.queuePairRisk(address(pUsd), address(pStock), address(pUsd), pairRisk);
        config.queuePairRisk(address(pUsd), address(pUsd), address(pStock), pairRisk);
    }
}
