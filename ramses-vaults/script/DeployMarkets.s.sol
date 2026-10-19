// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
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
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { PToken } from "peridot/PToken.sol";
import { PriceOracle } from "peridot/PriceOracle.sol";

interface IDelayedAdmin {
    function getMinDelay() external view returns (uint256);
    function hasRole(bytes32 role, address account) external view returns (bool);
}

/// @notice Simulation by default. No key access. Operator may separately broadcast with --account.
/// Creates ONLY separate contracts; governance wiring is emitted as a timelock batch.
contract DeployRamsesMarkets is Script {
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    IRamsesPool constant POOL = IRamsesPool(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b);
    IRamsesGauge constant GAUGE = IRamsesGauge(0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce);
    IRamsesNfp constant MANAGER = IRamsesNfp(0x2eBd7B85a4E08D5B508b04BA147976C94afE6590);
    IAggregatorV3 constant FEED = IAggregatorV3(0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15);
    address timelock;
    address keeper;
    address sender;
    address stock;
    address usdg;
    RamsesOracleGuard guard;
    RamsesBoostedVault vault;
    RamsesLossReserve reserve;
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
            timelock.code.length != 0 && IDelayedAdmin(timelock).getMinDelay() >= 1 hours,
            "TIMELOCK_REQUIRED"
        );
        string memory journal = vm.envString("RAMSES_JOURNAL");
        // Never guess how to resume a partially mined run. Foundry's receipt journal owns resume.
        require(!vm.exists(journal), "JOURNAL_EXISTS_USE_VERIFIER_OR_FORGE_RESUME");
        sCap = vm.envUint("RAMSES_STOCK_SUPPLY_CAP");
        uCap = vm.envUint("RAMSES_USDG_SUPPLY_CAP");
        sSeed = vm.envUint("RAMSES_STOCK_SEED");
        uSeed = vm.envUint("RAMSES_USDG_SEED");
        require(
            sSeed > 0.02e18 && uSeed > 0.02e6 && sCap >= sSeed && uCap >= uSeed,
            "SEED_AND_CAPS_REQUIRED"
        );
        stock = POOL.token1();
        usdg = POOL.token0();
        require(
            stock == 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC
                && usdg == 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168,
            "UNEXPECTED_UNDERLYING"
        );
        bytes32[4] memory roles = [
            bytes32(0),
            keccak256("PROPOSER_ROLE"),
            keccak256("EXECUTOR_ROLE"),
            keccak256("CANCELLER_ROLE")
        ];
        for (uint256 i; i < roles.length; i++) {
            require(
                !IDelayedAdmin(timelock).hasRole(roles[i], keeper), "KEEPER_HAS_GOVERNANCE_ROLE"
            );
        }
        vm.startBroadcast(sender);
        guard = new RamsesOracleGuard(timelock);
        vault = new RamsesBoostedVault();
        reserve = new RamsesLossReserve();
        adapter = new RamsesVaultAdapter(address(vault), MANAGER, guard);
        reserve.initialize(timelock, address(vault));
        vault.initialize(timelock, keeper, timelock, guard, reserve, adapter);
        controllerImplementation = new RamsesPeridottroller(timelock);
        unitroller = new RamsesUnitroller(timelock, address(controllerImplementation));
        controller = Peridottroller(address(unitroller));
        delegate = new RamsesBoostedDelegate();
        rate = new RamsesInterestRateModel();
        sMarket = _market(stock, 2e26, "Ramses Peridot NVDA", "rpNVDA");
        uMarket = _market(usdg, 2e14, "Ramses Peridot USDG", "rpUSDG");
        source = new RamsesLendingPriceSource(guard, PAIR);
        oracle =
            new RobinhoodLendingPriceAdapter(address(source), address(sMarket), address(uMarket));
        vm.stopBroadcast();
        require(
            address(delegate).code.length <= 24576 && address(vault).code.length <= 24576, "EIP170"
        );
        _configuration();
        _journal(journal);
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
                        stock, usdg, FEED, POOL, GAUGE, 12 hours, 300, 600, 18, 6, 8, true
                    )
                )
            )
        );
        // Proposed reserve policy: no funded coverage promised. Operator must separately fund it.
        _action(
            address(reserve),
            abi.encodeCall(
                reserve.configurePair,
                (
                    PAIR,
                    RamsesLossReserve.ReserveConfig(
                        stock, usdg, 1000e18, 1000e18, 10000, false, true
                    )
                )
            )
        );
        PairConfig memory c = PairConfig(
            stock,
            usdg,
            address(sMarket),
            address(uMarket),
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
        _action(
            address(vault),
            abi.encodeCall(
                vault.registerPair,
                (
                    PAIR,
                    c,
                    RamsesV3PairedAdapter.RegisterPairParams(stock, usdg, POOL, GAUGE, 400, 300)
                )
            )
        );
        _action(address(vault), abi.encodeCall(vault.setAggregateUsdgDepositCap, (usdg, 1000e6)));
        _action(address(vault), abi.encodeCall(vault.setDeployLossBps, (PAIR, 10)));
        _action(
            address(vault),
            abi.encodeCall(
                vault.setRangePolicy,
                (PAIR, RangePolicy(true, 2400, 600, 1 hours, 4, 10, type(uint128).max))
            )
        );
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
            _action(
                address(controller),
                abi.encodeCall(controller._setBorrowPaused, (PToken(address(m)), true))
            );
            // Zero collateral factors are the launch state. Borrowing requires a later risk decision.
            _action(address(m), abi.encodeCall(m._setReserveFactor, (0.2e18)));
            _action(address(m), abi.encodeCall(m._setFlashLoansPaused, (true)));
            _action(
                address(vault), abi.encodeCall(vault.grantRole, (vault.KEEPER_ROLE(), address(m)))
            );
            _action(
                address(m), abi.encodeCall(m.setVaultConfig, (address(vault), PAIR, 0.5e18, keeper))
            );
            _action(address(m), abi.encodeCall(m.setSupplyCap, (i == 0 ? sCap : uCap)));
            _action(address(m), abi.encodeCall(m.setVaultPaused, (false)));
            _action(
                i == 0 ? stock : usdg,
                abi.encodeWithSignature(
                    "approve(address,uint256)", address(m), i == 0 ? sSeed : uSeed
                )
            );
            _action(address(m), abi.encodeCall(m.mint, (i == 0 ? sSeed : uSeed)));
            _action(
                i == 0 ? stock : usdg,
                abi.encodeWithSignature("approve(address,uint256)", address(m), 0)
            );
        }
        PToken[] memory ms = new PToken[](2);
        ms[0] = PToken(address(sMarket));
        ms[1] = PToken(address(uMarket));
        uint256[] memory caps = new uint256[](2);
        caps[0] = vm.envUint("RAMSES_STOCK_BORROW_CAP");
        caps[1] = vm.envUint("RAMSES_USDG_BORROW_CAP");
        require(caps[0] != 0 && caps[1] != 0, "ZERO_BORROW_CAP_IS_UNLIMITED");
        _action(address(controller), abi.encodeCall(controller._setMarketBorrowCaps, (ms, caps)));
    }

    function _journal(string memory path) internal {
        string memory key = "ramses";
        vm.serializeAddress(key, "sender", sender);
        vm.serializeAddress(key, "timelock", timelock);
        vm.serializeAddress(key, "keeper", keeper);
        vm.serializeUint(key, "chainId", block.chainid);
        vm.serializeBytes32(key, "pair", PAIR);
        vm.serializeAddress(key, "guard", address(guard));
        vm.serializeAddress(key, "vault", address(vault));
        vm.serializeAddress(key, "reserve", address(reserve));
        vm.serializeAddress(key, "adapter", address(adapter));
        vm.serializeAddress(key, "controllerImplementation", address(controllerImplementation));
        vm.serializeAddress(key, "controller", address(unitroller));
        vm.serializeAddress(key, "delegate", address(delegate));
        vm.serializeAddress(key, "rate", address(rate));
        vm.serializeAddress(key, "stockMarket", address(sMarket));
        vm.serializeAddress(key, "dollarMarket", address(uMarket));
        vm.serializeAddress(key, "source", address(source));
        vm.serializeAddress(key, "oracle", address(oracle));
        vm.serializeAddress(key, "stock", stock);
        vm.serializeAddress(key, "usdg", usdg);
        vm.serializeUint(key, "stockSupplyCap", sCap);
        vm.serializeUint(key, "dollarSupplyCap", uCap);
        vm.serializeUint(key, "stockBorrowCap", vm.envUint("RAMSES_STOCK_BORROW_CAP"));
        vm.serializeUint(key, "dollarBorrowCap", vm.envUint("RAMSES_USDG_BORROW_CAP"));
        vm.serializeUint(key, "stockSeed", sSeed);
        vm.serializeUint(key, "dollarSeed", uSeed);
        vm.serializeAddress(key, "targets", targets);
        vm.serializeBytes(key, "payloads", payloads);
        // Delay and batch salt are explicit and stable; executing twice fails in the timelock.
        uint256[] memory values = new uint256[](targets.length);
        bytes32 salt = keccak256(abi.encode(address(vault), PAIR));
        bytes memory schedule = abi.encodeWithSignature(
            "scheduleBatch(address[],uint256[],bytes[],bytes32,bytes32,uint256)",
            targets,
            values,
            payloads,
            bytes32(0),
            salt,
            IDelayedAdmin(timelock).getMinDelay()
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
}
