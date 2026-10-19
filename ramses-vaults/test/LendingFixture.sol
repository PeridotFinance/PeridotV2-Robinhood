// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { CallbackRelay } from "./mocks/Phase2Double.sol";
import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../src/RamsesLossReserve.sol";
import { RamsesVaultAdapter } from "../src/RamsesVaultAdapter.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { RamsesRangeLib } from "../src/libraries/RamsesRangeLib.sol";
import { RamsesSwapLib } from "../src/libraries/RamsesSwapLib.sol";
import { RangePolicy } from "../src/libraries/RangeTypes.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import {
    TokenDouble,
    FactoryVoterDouble,
    NfpDouble,
    GaugeDouble
} from "./mocks/RamsesV3Double.sol";
import { PausableTokenDouble, FeedDouble, SwapPoolDouble } from "./mocks/Phase2Double.sol";

import {RamsesBoostedDelegate} from "../src/lending/RamsesBoostedDelegate.sol";
import {RamsesUnitroller, Peridottroller} from "../src/lending/RamsesUnitroller.sol";
import {RamsesInterestRateModel} from "../src/lending/RamsesInterestRateModel.sol";
import {RamsesLendingPriceSource, RobinhoodLendingPriceAdapter} from "../src/lending/RamsesLendingPriceSource.sol";
import {PErc20Delegator} from "peridot/PErc20Delegator.sol";
import {PeridottrollerInterface} from "peridot/PeridottrollerInterface.sol";
import {PToken} from "peridot/PToken.sol";
import {PriceOracle} from "peridot/PriceOracle.sol";

abstract contract LendingFixture is Test {
    bytes32 internal constant PAIR = keccak256("NVDA/USDG");
    address internal keeper = address(0x123);
    address internal guardian = address(0x456);
    address internal stockAccount = address(0x789);
    address internal usdgAccount = address(0xabc);
    address internal receiver = address(0xdef);
    PausableTokenDouble internal stock;
    TokenDouble internal usdg;
    TokenDouble internal ram;
    FeedDouble internal feed;
    SwapPoolDouble internal pool;
    FactoryVoterDouble internal factory;
    NfpDouble internal manager;
    GaugeDouble internal gauge;
    RamsesOracleGuard internal guard;
    RamsesVaultAdapter internal adapter;
    RamsesBoostedVault internal vault;
    RamsesLossReserve internal reserve;

    RamsesBoostedDelegate internal sMarket;
    RamsesBoostedDelegate internal uMarket;
    RamsesBoostedDelegate internal delegate;
    Peridottroller internal controller;
    RamsesUnitroller internal unitroller;
    RamsesInterestRateModel internal rate;
    RobinhoodLendingPriceAdapter internal priceAdapter;
    address internal alice = address(0xa11ce);
    address internal bob = address(0xb0b);

    function stockFirst() internal pure virtual returns (bool) {
        return false;
    }

    function setUp() public virtual {
        vm.warp(100 weeks + 1 days);
        PausableTokenDouble si = new PausableTokenDouble("NVDA", 18);
        TokenDouble ui = new TokenDouble("USDG", 6);
        stock = PausableTokenDouble(stockFirst() ? address(0x1000) : address(0x2000));
        usdg = TokenDouble(stockFirst() ? address(0x2000) : address(0x1000));
        vm.etch(address(stock), address(si).code);
        vm.etch(address(usdg), address(ui).code);
        ram = new TokenDouble("RAM", 18);
        feed = new FeedDouble(8, 100e8);
        factory = new FactoryVoterDouble();
        pool = new SwapPoolDouble(
            address(0x1000),
            address(0x2000),
            address(factory),
            stockFirst() ? -230270 : int24(230270)
        );
        manager = new NfpDouble(pool, address(factory));
        pool.setManager(address(manager));
        address[] memory tokens = new address[](3);
        tokens[0] = address(usdg);
        tokens[1] = address(stock);
        tokens[2] = address(ram);
        gauge = new GaugeDouble(pool, manager, tokens);
        manager.setGauge(gauge);
        factory.set(address(pool), address(gauge));
        guard = new RamsesOracleGuard(address(this));
        guard.configurePair(
            PAIR,
            RamsesOracleGuard.FeedConfig(
                address(stock),
                address(usdg),
                IAggregatorV3(address(feed)),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                12 hours,
                300,
                600,
                18,
                6,
                8,
                true
            )
        );
        vault = new RamsesBoostedVault();
        reserve = new RamsesLossReserve();
        adapter = new RamsesVaultAdapter(address(vault), IRamsesNfp(address(manager)), guard);
        reserve.initialize(address(this), address(vault));
        vault.initialize(address(this), keeper, guardian, guard, reserve, adapter);
        reserve.configurePair(
            PAIR,
            RamsesLossReserve.ReserveConfig(
                address(stock), address(usdg), 1000e18, 10000e18, 10000, false, true
            )
        );
        delegate = new RamsesBoostedDelegate();
        Peridottroller implementation = new Peridottroller();
        unitroller = new RamsesUnitroller(address(this), address(implementation));
        controller = Peridottroller(address(unitroller));
        rate = new RamsesInterestRateModel();
        sMarket = market(address(stock), 2e26, "rpNVDA");
        uMarket = market(address(usdg), 2e14, "rpUSDG");
        stockAccount = address(sMarket);
        usdgAccount = address(uMarket);
        priceAdapter = new RobinhoodLendingPriceAdapter(address(new RamsesLendingPriceSource(guard, PAIR)), stockAccount, usdgAccount);
        require(controller._setPriceOracle(PriceOracle(address(priceAdapter))) == 0);
        require(controller._setCloseFactor(0.5e18) == 0);
        require(controller._setLiquidationIncentive(1.08e18) == 0);
        vault.registerPair(
            PAIR,
            config(),
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock),
                address(usdg),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                400,
                300
            )
        );
        vault.setDeployLossBps(PAIR, 10);
        vault.setRangePolicy(PAIR, policy());
        for (uint256 i; i < 2; i++) {
            RamsesBoostedDelegate m = i == 0 ? sMarket : uMarket;
            require(controller._supportMarket(PToken(address(m))) == 0);
            vault.grantRole(vault.KEEPER_ROLE(), address(m));
            m.setVaultConfig(address(vault), PAIR, 0.5e18, keeper);
            m.setSupplyCap(i == 0 ? 10e18 : 1000e6);
            m.setVaultPaused(false);
            require(m._setReserveFactor(0.2e18) == 0);
            m._setFlashLoansPaused(true);
        }
        PToken[] memory markets = new PToken[](2);
        uint256[] memory caps = new uint256[](2);
        markets[0] = PToken(stockAccount); markets[1] = PToken(usdgAccount);
        caps[0] = 1e18; caps[1] = 100e6;
        controller._setMarketBorrowCaps(markets, caps);
        stock.mint(address(this), 100e18);
        usdg.mint(address(this), 10000e6);
        stock.mint(address(pool), 100000e18);
        usdg.mint(address(pool), 10000000e6);
        stock.approve(stockAccount, type(uint256).max);
        usdg.approve(usdgAccount, type(uint256).max);
        // Governance-controlled seed locks 1 whole pToken on each side forever.
        sMarket.mint(0.04e18);
        uMarket.mint(0.04e6);
        stock.mint(alice, 10e18); usdg.mint(bob, 1000e6);
        vm.prank(alice); stock.approve(stockAccount, type(uint256).max);
        vm.prank(bob); usdg.approve(usdgAccount, type(uint256).max);
    }

    function market(address token, uint256 initialRate, string memory symbol) internal returns (RamsesBoostedDelegate) {
        return RamsesBoostedDelegate(address(new PErc20Delegator(token, PeridottrollerInterface(address(controller)), rate, initialRate, symbol, symbol, 8, payable(address(this)), address(delegate), "")));
    }

    function supply() internal {
        vm.prank(alice); sMarket.mint(2e18);
        vm.prank(bob); uMarket.mint(200e6);
    }

    function enableBorrow() internal {
        require(controller._setCollateralFactor(PToken(stockAccount), 0.3e18) == 0);
        require(controller._setCollateralFactor(PToken(usdgAccount), 0.3e18) == 0);
        address[] memory ms = new address[](1); ms[0] = stockAccount;
        vm.prank(alice); controller.enterMarkets(ms);
    }

    function config() internal view returns (PairConfig memory c) {
        c = PairConfig(
            address(stock),
            address(usdg),
            stockAccount,
            usdgAccount,
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
    }

    function policy() internal pure returns (RangePolicy memory) {
        return RangePolicy(true, 2400, 600, 1 hours, 4, 10, type(uint128).max);
    }

    function deposit(uint256 s, uint256 u) internal {
        vm.prank(stockAccount);
        vault.depositForPair(PAIR, address(stock), s);
        vm.prank(usdgAccount);
        vault.depositForPair(PAIR, address(usdg), u);
    }

    function recenter() internal {
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
    }

    function fresh() internal {
        feed.set(feed.answer());
    }

    function shift(int24 ticks) internal {
        pool.setTick(pool.tick() + ticks);
        uint256 quote = VaultMath.quoteAtTick(pool.tick(), 1e18, address(stock), address(usdg));
        feed.set(int256(quote * 100));
    }

    function fundRewards() internal {
        gauge.fund(address(stock), block.timestamp / 1 weeks, 100e18);
        gauge.fund(address(usdg), block.timestamp / 1 weeks, 10000e6);
        gauge.fund(address(ram), block.timestamp / 1 weeks, 10000e18);
    }

    function noApprovals() internal view {
        assertEq(stock.allowance(address(vault), address(adapter)), 0);
        assertEq(usdg.allowance(address(vault), address(adapter)), 0);
        assertEq(stock.allowance(address(adapter), address(manager)), 0);
        assertEq(usdg.allowance(address(adapter), address(manager)), 0);
        assertEq(stock.allowance(address(vault), address(reserve)), 0);
        assertEq(usdg.allowance(address(vault), address(reserve)), 0);
    }
}

