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

import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { RamsesUnitroller, Peridottroller } from "../../src/lending/RamsesUnitroller.sol";
import { RamsesInterestRateModel } from "../../src/lending/RamsesInterestRateModel.sol";
import {
    RamsesLendingPriceSource,
    RobinhoodLendingPriceAdapter
} from "../../src/lending/RamsesLendingPriceSource.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { PToken } from "peridot/PToken.sol";
import { PriceOracle } from "peridot/PriceOracle.sol";

contract LendingForkTest is Test {
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

    RamsesBoostedDelegate sMarket;
    RamsesBoostedDelegate uMarket;
    RamsesBoostedDelegate delegate;
    Peridottroller controller;
    RamsesInterestRateModel rate;
    RobinhoodLendingPriceAdapter priceAdapter;
    address alice = address(0xa11ce);
    address bob = address(0xb0b);

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
        delegate = new RamsesBoostedDelegate();
        controller = Peridottroller(
            address(new RamsesUnitroller(address(this), address(new Peridottroller())))
        );
        rate = new RamsesInterestRateModel();
        sMarket = _market(address(stock), 2e26, "rpNVDA");
        uMarket = _market(address(usdg), 2e14, "rpUSDG");
        priceAdapter = new RobinhoodLendingPriceAdapter(
            address(new RamsesLendingPriceSource(guard, PAIR)), address(sMarket), address(uMarket)
        );
        require(controller._setPriceOracle(PriceOracle(address(priceAdapter))) == 0);
        require(controller._setCloseFactor(0.5e18) == 0);
        require(controller._setLiquidationIncentive(1.08e18) == 0);
        PairConfig memory c = PairConfig(
            address(stock),
            address(usdg),
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
        PToken[] memory ms = new PToken[](2);
        ms[0] = PToken(address(sMarket));
        ms[1] = PToken(address(uMarket));
        uint256[] memory caps = new uint256[](2);
        caps[0] = 1e18;
        caps[1] = 100e6;
        controller._setMarketBorrowCaps(ms, caps);
        deal(address(stock), address(this), 0.04e18);
        deal(address(usdg), address(this), 0.04e6);
        stock.approve(address(sMarket), type(uint256).max);
        usdg.approve(address(uMarket), type(uint256).max);
        sMarket.mint(0.04e18);
        uMarket.mint(0.04e6);
        deal(address(stock), alice, initialStock);
        deal(address(usdg), bob, 300e6);
        vm.prank(alice);
        stock.approve(address(sMarket), type(uint256).max);
        vm.prank(bob);
        usdg.approve(address(uMarket), type(uint256).max);
        vm.prank(alice);
        sMarket.mint(initialStock);
        vm.prank(bob);
        uMarket.mint(initialUsdg);
        vm.prank(keeper);
        vault.recenter(PAIR, block.timestamp);
    }

    function _market(address token, uint256 initialRate, string memory symbol)
        internal
        returns (RamsesBoostedDelegate)
    {
        return RamsesBoostedDelegate(
            address(
                new PErc20Delegator(
                    token,
                    PeridottrollerInterface(address(controller)),
                    rate,
                    initialRate,
                    symbol,
                    symbol,
                    8,
                    payable(address(this)),
                    address(delegate),
                    ""
                )
            )
        );
    }

    function _enableBorrow() internal {
        require(controller._setCollateralFactor(PToken(address(sMarket)), 0.3e18) == 0);
        require(controller._setCollateralFactor(PToken(address(uMarket)), 0.3e18) == 0);
        address[] memory ms = new address[](1);
        ms[0] = address(sMarket);
        vm.prank(alice);
        controller.enterMarkets(ms);
    }

    function _shift(int24 offset) internal {
        (, int24 tick,,,,,) = pool.slot0();
        int24 target = tick + offset;
        if (offset != 0) trader.move(offset < 0, TickMath.getSqrtPriceAtTick(target));
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

    function testForkMarketSupplyRebalanceBorrowRepayRedeemInterest() public {
        assertGt(adapter.positionState(PAIR).liquidity, 0);
        assertGe(stock.balanceOf(address(sMarket)), vault.accountedAssets(PAIR, address(stock)));
        assertGe(usdg.balanceOf(address(uMarket)), vault.accountedAssets(PAIR, address(usdg)));
        _enableBorrow();
        vm.prank(alice);
        uMarket.borrow(20e6);
        vm.roll(block.number + 10000);
        uMarket.accrueInterest();
        assertGt(uMarket.borrowBalanceStored(alice), 20e6);
        deal(address(usdg), alice, 25e6);
        vm.prank(alice);
        usdg.approve(address(uMarket), type(uint256).max);
        vm.prank(alice);
        uMarket.repayBorrow(type(uint256).max);
        assertEq(uMarket.totalBorrows(), 0);
        vm.prank(alice);
        sMarket.redeemUnderlying{ gas: 10_000_000 }(initialStock / 4);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 10_000_000 }(150e6);
    }

    function testForkStaleCashExitAtomicFailureRefresh() public {
        vm.prank(bob);
        uMarket.mint(10e6);
        uint256 cash = usdg.balanceOf(address(uMarket));
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        assertEq(uMarket.getCash(), cash);
        assertGt(uMarket.vaultLiquidAssets(), 0);
        assertEq(uMarket.vaultWithdrawableAssets(), 0);
        bytes32 beforeLedger = keccak256(abi.encode(vault.ledger(PAIR)));
        uint256 beforeShares = uMarket.balanceOf(bob);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 10_000_000 }(cash + 1);
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), beforeLedger);
        assertEq(uMarket.balanceOf(bob), beforeShares);
        assertEq(uMarket.cumulativeVaultLoss(), 0);
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 10_000_000 }(cash);
        assertEq(usdg.balanceOf(address(uMarket)), 0);
        _shift(0); // simulated subsequent feed refresh, real pool price
        vm.prank(bob);
        uMarket.redeemUnderlying{ gas: 10_000_000 }(1e6);
    }

    function testForkLossPreviewNoEarlyCashExit() public {
        uint256 sr = sMarket.exchangeRateStored();
        uint256 ur = uMarket.exchangeRateStored();
        _shift(500);
        assertLt(sMarket.exchangeRateStored(), sr);
        assertLt(uMarket.exchangeRateStored(), ur);
        vm.prank(alice);
        sMarket.redeemUnderlying(1e15);
        assertGt(vault.ledger(PAIR).cumulativeLossUSDG, 0);
        assertLt(sMarket.exchangeRateStored(), sr);
        assertLt(uMarket.exchangeRateStored(), ur);
    }

    function testForkUnderwaterLiquidation() public {
        _enableBorrow();
        vm.prank(alice);
        uMarket.borrow(50e6);
        _shift(9000);
        (,, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertGt(shortfall, 0);
        deal(address(usdg), address(this), 25e6);
        uMarket.liquidateBorrow(alice, 20e6, PToken(address(sMarket)));
        assertEq(uMarket.borrowBalanceStored(alice), 30e6);
        assertGt(sMarket.balanceOf(address(this)), 1e8);
    }

    function testForkPauseEmergencyAndDonations() public {
        uint256 sr = sMarket.exchangeRateStored();
        deal(address(stock), address(vault), stock.balanceOf(address(vault)) + 10e18);
        assertEq(sMarket.exchangeRateStored(), sr);
        uint128 l = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l, block.timestamp);
        vm.prank(alice);
        sMarket.redeemUnderlying(initialStock / 4);
        assertTrue(vault.pairConfig(PAIR).emergencyMode);
        _assertApprovals();
    }

    function testForkPartialEmergencyCashExit() public {
        _enableBorrow();
        uint128 liquidity = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, liquidity / 2, block.timestamp);
        assertGt(adapter.positionState(PAIR).liquidity, 0);
        bytes32 ledgerHash = keccak256(abi.encode(vault.ledger(PAIR)));
        vm.prank(bob);
        uMarket.redeemUnderlying(10e6);
        vm.prank(alice);
        vm.expectRevert();
        uMarket.borrow(1e6);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        uint256 cash = usdg.balanceOf(address(uMarket));
        uint256 shares = uMarket.balanceOf(bob);
        vm.prank(bob);
        vm.expectRevert();
        uMarket.redeemUnderlying{ gas: 10_000_000 }(cash + 1);
        assertEq(uMarket.balanceOf(bob), shares);
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash);
        vm.prank(bob);
        uMarket.redeemUnderlying(cash);
        assertEq(usdg.balanceOf(address(uMarket)), 0);
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerHash);
    }

    function testForkHarvestRamNeverInMarketNAV() public {
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        vm.prank(keeper);
        vault.harvest(PAIR, block.timestamp);
        assertGt(vault.externalRewards(PAIR, RAM), 0);
        uint256 sr = sMarket.exchangeRateStored();
        uint256 ur = uMarket.exchangeRateStored();
        vault.forwardExternalReward(PAIR, RAM, address(this), vault.externalRewards(PAIR, RAM));
        assertEq(sMarket.exchangeRateStored(), sr);
        assertEq(uMarket.exchangeRateStored(), ur);
    }

    function testForkDollarCollateralStockBorrowAndRepay() public {
        _enableBorrow();
        address[] memory ms = new address[](1);
        ms[0] = address(uMarket);
        vm.prank(bob);
        controller.enterMarkets(ms);
        vm.prank(bob);
        sMarket.borrow(0.1e18);
        assertEq(stock.balanceOf(bob), 0.1e18);
        vm.prank(bob);
        stock.approve(address(sMarket), type(uint256).max);
        vm.prank(bob);
        sMarket.repayBorrow(type(uint256).max);
        assertEq(sMarket.totalBorrows(), 0);
        vm.prank(bob);
        uMarket.redeemUnderlying(10e6);
    }

    function testForkRealSwapFeesLiftBothMarketRates() public {
        uint256 sr = sMarket.exchangeRateStored();
        uint256 ur = uMarket.exchangeRateStored();
        (uint160 initialSqrt, int24 tick,,,,,) = pool.slot0();
        // Real tick crossings in both directions; restore the initial oracle-aligned price.
        trader.move(true, TickMath.getSqrtPriceAtTick(tick - 100));
        trader.move(false, initialSqrt);
        vm.prank(keeper);
        (uint256 s, uint256 u) = vault.collectFees(PAIR, block.timestamp);
        assertGt(s, 0);
        assertGt(u, 0);
        assertGt(sMarket.exchangeRateStored(), sr);
        assertGt(uMarket.exchangeRateStored(), ur);
    }

    function testForkOracleFreshnessRepayAndIsolation() public {
        _enableBorrow();
        vm.prank(alice);
        uMarket.borrow(10e6);
        vm.prank(alice);
        usdg.approve(address(uMarket), type(uint256).max);
        vm.warp(vm.getBlockTimestamp() + 13 hours);
        vm.prank(alice);
        vm.expectRevert();
        uMarket.borrow(1);
        vm.prank(alice);
        vm.expectRevert();
        sMarket.redeemUnderlying(1);
        vm.prank(alice);
        uMarket.repayBorrow(type(uint256).max);
        assertEq(uMarket.totalBorrows(), 0);
        assertTrue(address(controller) != 0x6148183676E304dbe63a85C350c208DA3cEAc39C);
        assertTrue(address(sMarket) != 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26);
        assertTrue(address(uMarket) != 0x55aEd0569c8f0D166D71facE57B57C2f2624a563);
    }
}
