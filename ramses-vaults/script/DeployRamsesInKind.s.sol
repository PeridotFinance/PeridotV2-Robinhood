// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import {
    TransparentUpgradeableProxy
} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import { ERC1967Utils } from "@openzeppelin/contracts/proxy/ERC1967/ERC1967Utils.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../src/RamsesLossReserve.sol";
import { RamsesVaultAdapter } from "../src/RamsesVaultAdapter.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { IRamsesNfp, IRamsesPool, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RangePolicy } from "../src/libraries/RangeTypes.sol";
import { RamsesBoostedDelegate } from "../src/lending/RamsesBoostedDelegate.sol";
import { RamsesUnitroller, Peridottroller } from "../src/lending/RamsesUnitroller.sol";
import { RamsesPeridottroller } from "../src/lending/RamsesPeridottroller.sol";
import { RamsesInterestRateModel } from "../src/lending/RamsesInterestRateModel.sol";
import {
    RamsesLendingPriceSource,
    RobinhoodLendingPriceAdapter
} from "../src/lending/RamsesLendingPriceSource.sol";
import { RoutedVault, InKindExtension } from "../src/inkind/RoutedVault.sol";
import { InKindRedeemer } from "../src/inkind/InKindRedeemer.sol";
import { CoverageGuard } from "../src/inkind/CoverageGuard.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { PToken } from "peridot/PToken.sol";
import { PriceOracle } from "peridot/PriceOracle.sol";

interface IInKindTimelock {
    function getMinDelay() external view returns (uint256);
    function hasRole(bytes32 role, address account) external view returns (bool);
}

interface IProxyAdminOwner {
    function owner() external view returns (address);
}

/// @notice SPY/USDG supply-only range vault on Ramses with the in-kind exit (ramses-vaults/PRODUCTION_DECISION.md).
/// Simulation by default; the operator broadcasts with the Ledger. Deploys contracts only; every governance setting is one
/// timelock batch printed and journaled for the Ledger to schedule and execute.
///   vault:   TransparentUpgradeableProxy -> RoutedVault (router) -> RamsesBoostedVault code, plus InKindExtension. ProxyAdmin owned by the timelock.
///   reserve: TransparentUpgradeableProxy -> RamsesLossReserve. ProxyAdmin owned by the timelock.
/// Supply-only: both markets have borrowing paused and no collateral factor, so nothing can be borrowed against Ramses shares.
/// Daily in-kind limit is 0 (every in-kind exit refused) until a later timelock call.
///   verify(journal) re-reads the deployment after the batch executes and asserts roles, wiring and the vault codehash.
contract DeployRamsesInKind is Script {
    bytes32 constant PAIR = keccak256("SPY/USDG");
    IRamsesPool constant POOL = IRamsesPool(0x38453c115607463Ac284820Ce959831042f3Df4E);
    IRamsesGauge constant GAUGE = IRamsesGauge(0x739E31798EFE8Cff3ef4c613D8ae751e179C7337);
    IRamsesNfp constant MANAGER = IRamsesNfp(0x2eBd7B85a4E08D5B508b04BA147976C94afE6590);
    IAggregatorV3 constant FEED = IAggregatorV3(0x319724394D3A0e3669269846abE664Cd621f9f6A);
    address constant SPY = 0x117cc2133c37B721F49dE2A7a74833232B3B4C0C;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    uint128 constant PAIR_CAP_USD = 1000e18; // $1,000 per side principal and ranged value
    uint16 constant COVER_USDG_BPS = 1000; // CoverageGuard: 10% of exposure in USDG
    uint16 constant COVER_STOCK_BPS = 500; // and 5% in the stock
    bytes32 constant KEEPER_ROLE = keccak256("KEEPER_ROLE");
    bytes32 constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");
    bytes32 constant CONFIG_ROLE = keccak256("CONFIG_ROLE");
    bytes32 constant IN_KIND_OPERATOR_ROLE = keccak256("IN_KIND_OPERATOR_ROLE");

    address timelock;
    address keeper;
    address sender;
    RamsesBoostedVault vaultImplementation;
    InKindExtension extension;
    RoutedVault router;
    RamsesBoostedVault vault; // the proxy
    RamsesLossReserve reserveImplementation;
    RamsesLossReserve reserve; // the proxy
    RamsesOracleGuard guard;
    RamsesVaultAdapter adapter;
    Peridottroller controllerImplementation;
    RamsesUnitroller unitroller;
    Peridottroller controller;
    RamsesBoostedDelegate delegate;
    RamsesInterestRateModel rate;
    RamsesBoostedDelegate sMarket;
    RamsesBoostedDelegate uMarket;
    RamsesLendingPriceSource source;
    RobinhoodLendingPriceAdapter oracle;
    InKindRedeemer redeemer;
    CoverageGuard coverage;
    bytes32 expectedVaultCodehash;
    address[] targets;
    bytes[] payloads;
    uint256 sCap;
    uint256 uCap;
    uint256 sSeed;
    uint256 uSeed;

    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        sender = vm.envAddress("RAMSES_PUBLIC_SENDER");
        timelock = vm.envAddress("RAMSES_TIMELOCK");
        keeper = vm.envAddress("RAMSES_KEEPER");
        require(
            sender != address(0) && keeper != address(0) && keeper != sender && keeper != timelock,
            "DISTINCT_KEEPER_REQUIRED"
        );
        require(
            timelock.code.length != 0 && IInKindTimelock(timelock).getMinDelay() >= 1 hours,
            "TIMELOCK_REQUIRED"
        );
        bytes32[4] memory govRoles = [
            bytes32(0),
            keccak256("PROPOSER_ROLE"),
            keccak256("EXECUTOR_ROLE"),
            keccak256("CANCELLER_ROLE")
        ];
        for (uint256 i; i < govRoles.length; i++) {
            require(
                !IInKindTimelock(timelock).hasRole(govRoles[i], keeper),
                "KEEPER_HAS_GOVERNANCE_ROLE"
            );
        }
        string memory journal = vm.envString("RAMSES_JOURNAL");
        require(!vm.exists(journal), "JOURNAL_EXISTS_USE_VERIFIER_OR_FORGE_RESUME");
        sCap = vm.envUint("RAMSES_STOCK_SUPPLY_CAP");
        uCap = vm.envUint("RAMSES_USDG_SUPPLY_CAP");
        sSeed = vm.envUint("RAMSES_STOCK_SEED");
        uSeed = vm.envUint("RAMSES_USDG_SEED");
        // First mint must exceed DEAD_SHARES (1e8 shares): above 0.02 of either token at the initial rates.
        require(
            sSeed > 0.02e18 && uSeed > 0.02e6 && sCap >= sSeed && uCap >= uSeed,
            "SEED_AND_CAPS_REQUIRED"
        );
        require(POOL.token0() == SPY && POOL.token1() == USDG, "UNEXPECTED_UNDERLYING");
        require(GAUGE.pool() == address(POOL), "UNEXPECTED_GAUGE");

        vm.startBroadcast(sender);
        vaultImplementation = new RamsesBoostedVault();
        extension = new InKindExtension();
        router = new RoutedVault(address(vaultImplementation), address(extension));
        // The vault and reserve initializers accept only their implementation's deployer (`sender`), so an uninitialized proxy cannot be front-run.
        vault = RamsesBoostedVault(
            payable(address(new TransparentUpgradeableProxy(address(router), timelock, "")))
        );
        reserveImplementation = new RamsesLossReserve();
        reserve = RamsesLossReserve(
            address(new TransparentUpgradeableProxy(address(reserveImplementation), timelock, ""))
        );
        guard = new RamsesOracleGuard(timelock);
        adapter = new RamsesVaultAdapter(address(vault), MANAGER, guard);
        reserve.initialize(timelock, address(vault));
        vault.initialize(timelock, keeper, timelock, guard, reserve, adapter);
        controllerImplementation = new RamsesPeridottroller(timelock);
        unitroller = new RamsesUnitroller(timelock, address(controllerImplementation));
        controller = Peridottroller(address(unitroller));
        delegate = new RamsesBoostedDelegate();
        rate = new RamsesInterestRateModel();
        sMarket = _market(SPY, 2e26, "Ramses Peridot SPY", "rpSPY");
        uMarket = _market(USDG, 2e14, "Ramses Peridot USDG SPY", "rpUSDGs");
        source = new RamsesLendingPriceSource(guard, PAIR);
        oracle =
            new RobinhoodLendingPriceAdapter(address(source), address(sMarket), address(uMarket));
        redeemer = new InKindRedeemer(vault, PAIR, sMarket, uMarket);
        coverage = new CoverageGuard(
            vault, reserve, guard, adapter, PAIR, COVER_USDG_BPS, COVER_STOCK_BPS
        );
        vm.stopBroadcast();

        // Reference build: the vault code embeds its deployer, so the expected codehash is a local, unbroadcast deployment from the
        // same sender. Done after the broadcast so it cannot shift the broadcast's nonces or addresses.
        vm.startPrank(sender);
        expectedVaultCodehash = address(new RamsesBoostedVault()).codehash;
        vm.stopPrank();

        _assertDeployed();
        _configuration();
        _journal(journal);
    }

    function _assertDeployed() internal view {
        require(address(vaultImplementation).codehash == expectedVaultCodehash, "VAULT_CODEHASH");
        require(
            address(vaultImplementation).code.length <= 24576
                && address(delegate).code.length <= 24576,
            "EIP170"
        );
        require(
            router.implementation() == address(vaultImplementation)
                && router.extension() == address(extension),
            "ROUTER_WIRING"
        );
        require(_implementationOf(address(vault)) == address(router), "VAULT_PROXY_IMPLEMENTATION");
        require(
            _implementationOf(address(reserve)) == address(reserveImplementation),
            "RESERVE_PROXY_IMPLEMENTATION"
        );
        require(
            IProxyAdminOwner(_adminOf(address(vault))).owner() == timelock,
            "VAULT_PROXY_ADMIN_OWNER"
        );
        require(
            IProxyAdminOwner(_adminOf(address(reserve))).owner() == timelock,
            "RESERVE_PROXY_ADMIN_OWNER"
        );
        require(adapter.vault() == address(vault), "ADAPTER_VAULT");
        require(
            address(vault.liquidityAdapter()) == address(adapter)
                && address(vault.oracleGuard()) == address(guard),
            "VAULT_WIRING"
        );
        require(
            vault.hasRole(CONFIG_ROLE, timelock) && vault.hasRole(KEEPER_ROLE, keeper)
                && vault.hasRole(GUARDIAN_ROLE, timelock),
            "VAULT_ROLES"
        );
        require(
            !vault.hasRole(CONFIG_ROLE, sender) && !vault.hasRole(bytes32(0), sender),
            "SENDER_HAS_NO_ROLE"
        );
        // The gate lives in the vault (proxy) storage, so read it through the proxy, not the router address.
        require(
            address(RoutedVault(payable(address(vault))).gate()) == address(0),
            "GATE_SET_ONLY_BY_TIMELOCK"
        );
    }

    function _implementationOf(address proxy) internal view returns (address) {
        return address(uint160(uint256(vm.load(proxy, ERC1967Utils.IMPLEMENTATION_SLOT))));
    }

    function _adminOf(address proxy) internal view returns (address) {
        return address(uint160(uint256(vm.load(proxy, ERC1967Utils.ADMIN_SLOT))));
    }

    function _market(address token, uint256 initialRate, string memory name, string memory symbol)
        internal
        returns (RamsesBoostedDelegate)
    {
        return RamsesBoostedDelegate(
            address(
                new PErc20Delegator(
                    token,
                    PeridottrollerInterface(address(unitroller)),
                    rate,
                    initialRate,
                    name,
                    symbol,
                    8,
                    payable(timelock),
                    address(delegate),
                    ""
                )
            )
        );
    }

    function _action(address target, bytes memory data) internal {
        targets.push(target);
        payloads.push(data);
        console2.log("timelock target", target);
        console2.logBytes(data);
    }

    function _configuration() internal {
        _action(
            address(guard),
            abi.encodeCall(
                guard.configurePair,
                (
                    PAIR,
                    RamsesOracleGuard.FeedConfig(
                        SPY, USDG, FEED, POOL, GAUGE, 12 hours, 300, 600, 18, 6, 8, true
                    )
                )
            )
        );
        // No funded coverage promised; the operator funds the reserve separately.
        _action(
            address(reserve),
            abi.encodeCall(
                reserve.configurePair,
                (
                    PAIR,
                    RamsesLossReserve.ReserveConfig(
                        SPY, USDG, PAIR_CAP_USD, PAIR_CAP_USD, 10000, false, true
                    )
                )
            )
        );
        PairConfig memory c = PairConfig(
            SPY,
            USDG,
            address(sMarket),
            address(uMarket),
            PAIR_CAP_USD,
            PAIR_CAP_USD,
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
        _action(
            address(vault),
            abi.encodeCall(
                vault.registerPair,
                (
                    PAIR,
                    c,
                    RamsesV3PairedAdapter.RegisterPairParams(SPY, USDG, POOL, GAUGE, 400, 300)
                )
            )
        );
        _action(address(vault), abi.encodeCall(vault.setAggregateUsdgDepositCap, (USDG, 1000e6)));
        _action(address(vault), abi.encodeCall(vault.setDeployLossBps, (PAIR, 10)));
        // Mirrors the live NVDA/USDG Uniswap pilot: +/-2400 ticks, recentre at 600, 1 h apart, 4 a day, 10 bp loss bound; $1,000 ranged cap.
        _action(
            address(vault),
            abi.encodeCall(
                vault.setRangePolicy,
                (PAIR, RangePolicy(true, 2400, 600, 1 hours, 4, 10, PAIR_CAP_USD))
            )
        );
        // In-kind exit wiring. The vault holds KEEPER_ROLE on itself only for inKindCheckpoint (tested self-call invariant).
        _action(
            address(vault),
            abi.encodeCall(vault.grantRole, (IN_KIND_OPERATOR_ROLE, address(redeemer)))
        );
        _action(address(vault), abi.encodeCall(vault.grantRole, (KEEPER_ROLE, address(vault))));
        _action(address(vault), abi.encodeCall(vault.grantRole, (GUARDIAN_ROLE, address(coverage))));
        _action(address(vault), abi.encodeCall(RoutedVault.setGate, (coverage)));
        _action(address(vault), abi.encodeCall(InKindExtension.setInKindLimit, (PAIR, 0)));
        _action(
            address(controller),
            abi.encodeCall(controller._setPriceOracle, (PriceOracle(address(oracle))))
        );
        _action(address(controller), abi.encodeCall(controller._setCloseFactor, (0.5e18)));
        _action(address(controller), abi.encodeCall(controller._setLiquidationIncentive, (1.08e18)));
        for (uint256 i; i < 2; i++) {
            RamsesBoostedDelegate m = i == 0 ? sMarket : uMarket;
            _action(
                address(controller), abi.encodeCall(controller._supportMarket, (PToken(address(m))))
            );
            // Supply-only: borrowing paused and no collateral factor is ever set.
            _action(
                address(controller),
                abi.encodeCall(controller._setBorrowPaused, (PToken(address(m)), true))
            );
            _action(address(m), abi.encodeCall(m._setReserveFactor, (0.2e18)));
            _action(address(m), abi.encodeCall(m._setFlashLoansPaused, (true)));
            _action(address(vault), abi.encodeCall(vault.grantRole, (KEEPER_ROLE, address(m))));
            _action(
                address(m), abi.encodeCall(m.setVaultConfig, (address(vault), PAIR, 0.5e18, keeper))
            );
            _action(address(m), abi.encodeCall(m.setSupplyCap, (i == 0 ? sCap : uCap)));
            _action(address(m), abi.encodeCall(m.setVaultPaused, (false)));
            _action(
                i == 0 ? SPY : USDG,
                abi.encodeWithSignature(
                    "approve(address,uint256)", address(m), i == 0 ? sSeed : uSeed
                )
            );
            _action(address(m), abi.encodeCall(m.mint, (i == 0 ? sSeed : uSeed)));
            _action(
                i == 0 ? SPY : USDG,
                abi.encodeWithSignature("approve(address,uint256)", address(m), 0)
            );
        }
        // A zero borrow cap means unlimited in this controller, so set a one-unit cap on top of the pause.
        PToken[] memory ms = new PToken[](2);
        ms[0] = PToken(address(sMarket));
        ms[1] = PToken(address(uMarket));
        uint256[] memory caps = new uint256[](2);
        caps[0] = 1;
        caps[1] = 1;
        _action(address(controller), abi.encodeCall(controller._setMarketBorrowCaps, (ms, caps)));
    }

    function _journal(string memory path) internal {
        string memory key = "ramsesInKind";
        vm.serializeAddress(key, "sender", sender);
        vm.serializeAddress(key, "timelock", timelock);
        vm.serializeAddress(key, "keeper", keeper);
        vm.serializeUint(key, "chainId", block.chainid);
        vm.serializeBytes32(key, "pair", PAIR);
        vm.serializeAddress(key, "vault", address(vault));
        vm.serializeAddress(key, "vaultImplementation", address(vaultImplementation));
        vm.serializeBytes32(
            key, "vaultImplementationCodehash", address(vaultImplementation).codehash
        );
        vm.serializeAddress(key, "router", address(router));
        vm.serializeAddress(key, "extension", address(extension));
        vm.serializeAddress(key, "reserve", address(reserve));
        vm.serializeAddress(key, "reserveImplementation", address(reserveImplementation));
        vm.serializeAddress(key, "guard", address(guard));
        vm.serializeAddress(key, "adapter", address(adapter));
        vm.serializeAddress(key, "controller", address(unitroller));
        vm.serializeAddress(key, "controllerImplementation", address(controllerImplementation));
        vm.serializeAddress(key, "delegate", address(delegate));
        vm.serializeAddress(key, "rate", address(rate));
        vm.serializeAddress(key, "stockMarket", address(sMarket));
        vm.serializeAddress(key, "dollarMarket", address(uMarket));
        vm.serializeAddress(key, "source", address(source));
        vm.serializeAddress(key, "oracle", address(oracle));
        vm.serializeAddress(key, "redeemer", address(redeemer));
        vm.serializeAddress(key, "coverageGuard", address(coverage));
        vm.serializeUint(key, "stockSupplyCap", sCap);
        vm.serializeUint(key, "dollarSupplyCap", uCap);
        vm.serializeUint(key, "stockSeed", sSeed);
        vm.serializeUint(key, "dollarSeed", uSeed);
        vm.serializeAddress(key, "targets", targets);
        vm.serializeBytes(key, "payloads", payloads);
        uint256[] memory values = new uint256[](targets.length);
        bytes32 salt = keccak256(abi.encode(address(vault), PAIR));
        bytes memory schedule = abi.encodeWithSignature(
            "scheduleBatch(address[],uint256[],bytes[],bytes32,bytes32,uint256)",
            targets,
            values,
            payloads,
            bytes32(0),
            salt,
            IInKindTimelock(timelock).getMinDelay()
        );
        bytes memory execute = abi.encodeWithSignature(
            "executeBatch(address[],uint256[],bytes[],bytes32,bytes32)",
            targets,
            values,
            payloads,
            bytes32(0),
            salt
        );
        vm.serializeBytes(key, "scheduleCalldata", schedule);
        string memory result = vm.serializeBytes(key, "executeCalldata", execute);
        vm.writeJson(result, path);
        console2.log("Deployment plan/journal", path);
        console2.log("Timelock scheduleBatch calldata");
        console2.logBytes(schedule);
        console2.log("Timelock executeBatch calldata");
        console2.logBytes(execute);
    }

    /// @notice Read-only check after the timelock batch has executed. Pass the journal path written by run().
    function verify(string memory journal) external view {
        string memory j = vm.readFile(journal);
        RamsesBoostedVault v = RamsesBoostedVault(payable(vm.parseJsonAddress(j, ".vault")));
        address tl = vm.parseJsonAddress(j, ".timelock");
        address k = vm.parseJsonAddress(j, ".keeper");
        RoutedVault r = RoutedVault(payable(vm.parseJsonAddress(j, ".router")));
        address redeemer_ = vm.parseJsonAddress(j, ".redeemer");
        address coverage_ = vm.parseJsonAddress(j, ".coverageGuard");
        RamsesBoostedDelegate sm = RamsesBoostedDelegate(vm.parseJsonAddress(j, ".stockMarket"));
        RamsesBoostedDelegate um = RamsesBoostedDelegate(vm.parseJsonAddress(j, ".dollarMarket"));
        Peridottroller ctl = Peridottroller(vm.parseJsonAddress(j, ".controller"));
        require(
            vm.parseJsonAddress(j, ".vaultImplementation").codehash
                == vm.parseJsonBytes32(j, ".vaultImplementationCodehash"),
            "VAULT_CODEHASH"
        );
        require(_implementationOf(address(v)) == address(r), "VAULT_PROXY_IMPLEMENTATION");
        require(IProxyAdminOwner(_adminOf(address(v))).owner() == tl, "VAULT_PROXY_ADMIN_OWNER");
        require(v.hasRole(CONFIG_ROLE, tl) && v.hasRole(KEEPER_ROLE, k), "VAULT_ROLES");
        require(v.hasRole(IN_KIND_OPERATOR_ROLE, redeemer_), "REDEEMER_ROLE");
        require(v.hasRole(KEEPER_ROLE, address(v)), "SELF_KEEPER_ROLE");
        require(v.hasRole(GUARDIAN_ROLE, coverage_), "COVERAGE_GUARDIAN_ROLE");
        require(address(RoutedVault(payable(address(v))).gate()) == coverage_, "GATE");
        require(
            r.implementation() == vm.parseJsonAddress(j, ".vaultImplementation"),
            "ROUTER_IMPLEMENTATION"
        );
        require(
            InKindExtension(address(v)).inKindStats(PAIR).limitUsdPerDay == 0,
            "IN_KIND_LIMIT_NOT_ZERO"
        );
        require(v.pairConfig(PAIR).exists, "PAIR_REGISTERED");
        (bool enabled, uint16 halfWidth, uint16 trigger,,,, uint128 maxRanged) = v.rangePolicy(PAIR);
        require(
            enabled && halfWidth == 2400 && trigger == 600 && maxRanged == PAIR_CAP_USD,
            "RANGE_POLICY"
        );
        for (uint256 i; i < 2; i++) {
            RamsesBoostedDelegate m = i == 0 ? sm : um;
            require(ctl.borrowGuardianPaused(address(m)), "BORROW_NOT_PAUSED");
            (, uint256 cf,) = ctl.markets(address(m));
            require(cf == 0, "COLLATERAL_FACTOR_NOT_ZERO");
        }
        console2.log(
            "verified: proxy, router, roles, gate, limit 0, policy 2400/600, $1,000 cap, supply-only markets"
        );
    }
}
