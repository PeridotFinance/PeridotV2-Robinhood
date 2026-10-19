// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Phase2Fixture } from "./Phase2.t.sol";
import { PausableTokenDouble, SwapPoolDouble } from "./mocks/Phase2Double.sol";
import { TokenDouble, NfpDouble, GaugeDouble } from "./mocks/RamsesV3Double.sol";
import { MultiFactoryDouble, MultiNfpDouble } from "./mocks/MultiNfpDouble.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { RamsesLossReserve } from "../src/RamsesLossReserve.sol";
import { RamsesVaultAdapter } from "../src/RamsesVaultAdapter.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesGauge, IRamsesNfp } from "../src/interfaces/IRamsesV3.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";

contract MultiPairTest is Phase2Fixture {
    bytes32 constant SECOND = keccak256("SPY/USDG");
    PausableTokenDouble secondStock;
    SwapPoolDouble secondPool;
    GaugeDouble secondGauge;

    function setUp() public override {
        super.setUp();
        MultiFactoryDouble mf = new MultiFactoryDouble();
        MultiNfpDouble mn = new MultiNfpDouble(address(mf));
        secondStock = new PausableTokenDouble("SPY", 18);
        pool = new SwapPoolDouble(address(usdg), address(stock), address(mf), 230270);
        secondPool = new SwapPoolDouble(address(usdg), address(secondStock), address(mf), 230270);
        pool.setManager(address(mn));
        secondPool.setManager(address(mn));
        address[] memory ts = new address[](3);
        ts[0] = address(usdg);
        ts[1] = address(stock);
        ts[2] = address(ram);
        gauge = new GaugeDouble(pool, NfpDouble(address(mn)), ts);
        ts[1] = address(secondStock);
        secondGauge = new GaugeDouble(secondPool, NfpDouble(address(mn)), ts);
        mf.set(pool, address(gauge));
        mf.set(secondPool, address(secondGauge));
        mn.addPool(pool, gauge);
        mn.addPool(secondPool, secondGauge);
        guard = new RamsesOracleGuard(address(this));
        vault = new RamsesBoostedVault();
        reserve = new RamsesLossReserve();
        adapter = new RamsesVaultAdapter(address(vault), IRamsesNfp(address(mn)), guard);
        reserve.initialize(address(this), address(vault));
        vault.initialize(address(this), keeper, guardian, guard, reserve, adapter);
        RamsesOracleGuard.FeedConfig memory c = superGuardConfig();
        guard.configurePair(PAIR, c);
        c.stockToken = address(secondStock);
        c.pool = IRamsesPool(address(secondPool));
        c.gauge = IRamsesGauge(address(secondGauge));
        guard.configurePair(SECOND, c);
        PairConfig memory pc = config();
        vault.registerPair(
            PAIR,
            pc,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock),
                address(usdg),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                400,
                300
            )
        );
        pc.stockToken = address(secondStock);
        vault.registerPair(
            SECOND,
            pc,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(secondStock),
                address(usdg),
                IRamsesPool(address(secondPool)),
                IRamsesGauge(address(secondGauge)),
                400,
                300
            )
        );
        reserve.configurePair(
            PAIR,
            RamsesLossReserve.ReserveConfig(
                address(stock), address(usdg), 1000e18, 10000e18, 10000, false, true
            )
        );
        reserve.configurePair(
            SECOND,
            RamsesLossReserve.ReserveConfig(
                address(secondStock), address(usdg), 1000e18, 10000e18, 10000, false, true
            )
        );
        vault.setRangePolicy(PAIR, policy());
        vault.setRangePolicy(SECOND, policy());
        vault.setDeployLossBps(PAIR, 10);
        vault.setDeployLossBps(SECOND, 10);
        secondStock.mint(stockAccount, 100e18);
        vm.prank(stockAccount);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(stockAccount);
        secondStock.approve(address(vault), type(uint256).max);
        vm.prank(usdgAccount);
        usdg.approve(address(vault), type(uint256).max);
    }

    function superGuardConfig() private view returns (RamsesOracleGuard.FeedConfig memory c) {
        c.stockToken = address(stock);
        c.usdg = address(usdg);
        c.stockFeed = IAggregatorV3(address(feed));
        c.pool = IRamsesPool(address(pool));
        c.gauge = IRamsesGauge(address(gauge));
        c.maxStaleness = 12 hours;
        c.maxPriceDeviationBps = 300;
        c.maxRemovalDeviationBps = 600;
        c.stockDecimals = 18;
        c.usdgDecimals = 6;
        c.stockFeedDecimals = 8;
        c.enabled = true;
    }

    function testMultiPairAccountingRewardsAndEmergencyIsolation() public {
        deposit(2e18, 200e6);
        recenter();
        vm.prank(stockAccount);
        vault.depositForPair(SECOND, address(secondStock), 1e18);
        vm.prank(usdgAccount);
        vault.depositForPair(SECOND, address(usdg), 100e6);
        vm.prank(keeper);
        vault.recenter(SECOND, block.timestamp);
        assertTrue(adapter.positionState(PAIR).tokenId != adapter.positionState(SECOND).tokenId);
        fundRewards();
        secondGauge.fund(address(ram), block.timestamp / 1 weeks, 20000e18);
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        vm.prank(keeper);
        vault.harvest(PAIR, block.timestamp);
        vm.prank(keeper);
        vault.harvest(SECOND, block.timestamp);
        uint256 r1 = vault.externalRewards(PAIR, address(ram));
        uint256 r2 = vault.externalRewards(SECOND, address(ram));
        assertGt(r1, 0);
        assertGt(r2, 0);
        assertEq(ram.balanceOf(address(vault)), r1 + r2);
        vault.forwardExternalReward(PAIR, address(ram), receiver, r1);
        assertEq(ram.balanceOf(address(vault)), r2);
        assertEq(
            vault.aggregateUsdgPrincipal(address(usdg)),
            vault.ledger(PAIR).usdgPrincipal + vault.ledger(SECOND).usdgPrincipal
        );
        PairLedger memory before_ = vault.ledger(SECOND);
        uint128 l2 = adapter.positionState(SECOND).liquidity;
        uint128 l1 = adapter.positionState(PAIR).liquidity;
        vm.prank(guardian);
        vault.emergencyDecrease(PAIR, l1, block.timestamp);
        assertEq(adapter.positionState(SECOND).liquidity, l2);
        assertEq(abi.encode(vault.ledger(SECOND)), abi.encode(before_));
        assertFalse(vault.pairConfig(SECOND).emergencyMode);
    }

    function testSharedUsdgAggregateCapAcrossPairs() public {
        vault.setAggregateUsdgDepositCap(address(usdg), 300e6);
        deposit(0, 200e6);
        vm.prank(usdgAccount);
        vm.expectRevert(RamsesBoostedVault.PairCapExceeded.selector);
        vault.depositForPair(SECOND, address(usdg), 101e6);
        vm.prank(usdgAccount);
        vault.depositForPair(SECOND, address(usdg), 100e6);
        assertEq(vault.aggregateUsdgPrincipal(address(usdg)), 300e6);
    }

    function testGuardIdentityCannotChangeToAnotherValidPair() public {
        RamsesOracleGuard.FeedConfig memory c = guard.feedConfig(SECOND);
        vm.expectRevert(RamsesOracleGuard.PoolMismatch.selector);
        guard.configurePair(PAIR, c);
    }

    function testRegisterDuplicatePoolOrMismatchedFeedFails() public {
        PairConfig memory c = config();
        RamsesOracleGuard.FeedConfig memory f = guard.feedConfig(PAIR);
        guard.configurePair(keccak256("third"), f);
        vm.expectRevert(RamsesV3PairedAdapter.InvalidConfiguration.selector);
        vault.registerPair(
            keccak256("third"),
            c,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock),
                address(usdg),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                400,
                300
            )
        );
        f.stockToken = address(secondStock);
        f.pool = IRamsesPool(address(secondPool));
        f.gauge = IRamsesGauge(address(secondGauge));
        guard.configurePair(keccak256("bad"), f);
        vm.expectRevert(RamsesBoostedVault.InvalidConfiguration.selector);
        vault.registerPair(
            keccak256("bad"),
            c,
            RamsesV3PairedAdapter.RegisterPairParams(
                address(stock),
                address(usdg),
                IRamsesPool(address(pool)),
                IRamsesGauge(address(gauge)),
                400,
                300
            )
        );
    }
}
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
