// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import { StdInvariant } from "forge-std/StdInvariant.sol";
import { Test } from "forge-std/Test.sol";

import { RamsesBoostedVault as RobinhoodBoostedVault } from "../src/RamsesBoostedVault.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { RamsesVaultAdapter } from "../src/RamsesVaultAdapter.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import { MockERC20 } from "baseline/test/mocks/MockERC20.sol";
import { AccountingOracleDouble as MockOracleGuard } from "./mocks/AccountingOracleDouble.sol";
import { MockLossReserve } from "baseline/test/mocks/MockLossReserve.sol";
import {
    AccountingAdapterDouble as MockLiquidityAdapter
} from "./mocks/AccountingAdapterDouble.sol";

contract VaultV3Handler is Test {
    RobinhoodBoostedVault internal vault;
    MockERC20 internal stock;
    MockERC20 internal usdg;
    bytes32 internal pairId;
    address internal stockAccount;
    address internal usdgAccount;
    address internal keeper;

    constructor(
        RobinhoodBoostedVault vault_,
        MockERC20 stock_,
        MockERC20 usdg_,
        bytes32 pairId_,
        address stockAccount_,
        address usdgAccount_,
        address keeper_
    ) {
        vault = vault_;
        stock = stock_;
        usdg = usdg_;
        pairId = pairId_;
        stockAccount = stockAccount_;
        usdgAccount = usdgAccount_;
        keeper = keeper_;
    }

    function depositStock(uint96 seed) external {
        uint256 amount = bound(uint256(seed), 1, 100e18);
        stock.mint(stockAccount, amount);
        vm.prank(stockAccount);
        try vault.depositForPair(pairId, address(stock), amount) { } catch { }
    }

    function depositUsdg(uint64 seed) external {
        uint256 amount = bound(uint256(seed), 1, 100_000e6);
        usdg.mint(usdgAccount, amount);
        vm.prank(usdgAccount);
        try vault.depositForPair(pairId, address(usdg), amount) { } catch { }
    }

    function rebalance() external {
        vm.prank(keeper);
        try vault.rebalance(pairId, block.timestamp + 60) { } catch { }
    }

    function withdrawStock(uint96 seed) external {
        uint256 idle = vault.liquidAssets(pairId, address(stock));
        if (idle == 0) return;
        uint256 amount = bound(uint256(seed), 1, idle);
        vm.prank(stockAccount);
        try vault.withdrawForSide(pairId, address(stock), amount, stockAccount, 0) { } catch { }
    }

    function withdrawUsdg(uint64 seed) external {
        uint256 idle = vault.liquidAssets(pairId, address(usdg));
        if (idle == 0) return;
        uint256 amount = bound(uint256(seed), 1, idle);
        vm.prank(usdgAccount);
        try vault.withdrawForSide(pairId, address(usdg), amount, usdgAccount, 0) { } catch { }
    }

    function checkpoint() external {
        vm.prank(keeper);
        try vault.checkpoint(pairId, block.timestamp + 60) { } catch { }
    }
}

contract VaultV3AccountingInvariant is StdInvariant, Test {
    bytes32 internal constant PAIR_ID = keccak256("NVDA/USDG");
    address internal stockAccount = makeAddr("stockAccount");
    address internal usdgAccount = makeAddr("usdgAccount");
    address internal keeper = makeAddr("keeper");
    address internal guardian = makeAddr("guardian");

    RobinhoodBoostedVault internal vault;
    MockERC20 internal stock;
    MockERC20 internal usdg;
    MockLiquidityAdapter internal adapter;

    function setUp() external {
        stock = new MockERC20("NVIDIA", "NVDA", 18);
        usdg = new MockERC20("Global Dollar", "USDG", 6);
        MockOracleGuard oracle = new MockOracleGuard();
        MockLossReserve reserve = new MockLossReserve();
        adapter = new MockLiquidityAdapter();
        vault = new RobinhoodBoostedVault();
        adapter.setVault(address(vault));
        adapter.setOracle(address(oracle));
        oracle.setTokens(address(stock), address(usdg));
        vault.initialize(
            address(this),
            keeper,
            guardian,
            RamsesOracleGuard(address(oracle)),
            reserve,
            RamsesVaultAdapter(address(adapter))
        );
        vault.setAggregateUsdgDepositCap(address(usdg), type(uint128).max);

        IRamsesPool key = IRamsesPool(address(0x777));
        vault.registerPair(
            PAIR_ID,
            PairConfig({
                stockToken: address(stock),
                usdg: address(usdg),
                stockAccount: stockAccount,
                usdgAccount: usdgAccount,
                maxPairValueUSDG: type(uint128).max,
                maxSettlementSwapUSDG: type(uint128).max,
                maxCheckpointAge: 1 days,
                deprecatedMinDeadlineDelay: 0,
                maxDeadlineDelay: 300,
                reserveFeeBps: 2_000,
                maxSwapSlippageBps: 100,
                withdrawOverUnwindBps: 200,
                stockDecimals: 18,
                usdgDecimals: 6,
                allocationPaused: false,
                swapsPaused: false,
                emergencyMode: false,
                exists: true
            }),
            RamsesV3PairedAdapter.RegisterPairParams({
                stockToken: address(stock),
                usdg: address(usdg),
                pool: key,
                gauge: IRamsesGauge(address(0)),
                allocationDeviationBps: 300,
                removalToleranceBps: 400
            })
        );
        vm.prank(stockAccount);
        stock.approve(address(vault), type(uint256).max);
        vm.prank(usdgAccount);
        usdg.approve(address(vault), type(uint256).max);

        VaultV3Handler handler =
            new VaultV3Handler(vault, stock, usdg, PAIR_ID, stockAccount, usdgAccount, keeper);
        targetContract(address(handler));
    }

    function invariantCustodyCoversCachedClaims() external view {
        uint256 stockCustody = stock.balanceOf(address(vault)) + stock.balanceOf(address(adapter));
        uint256 usdgCustody = usdg.balanceOf(address(vault)) + usdg.balanceOf(address(adapter));
        assertGe(stockCustody, vault.accountedAssets(PAIR_ID, address(stock)));
        assertGe(usdgCustody, vault.accountedAssets(PAIR_ID, address(usdg)));
    }

    function invariantIdleNeverExceedsVaultCustody() external view {
        assertLe(vault.liquidAssets(PAIR_ID, address(stock)), stock.balanceOf(address(vault)));
        assertLe(vault.liquidAssets(PAIR_ID, address(usdg)), usdg.balanceOf(address(vault)));
    }

    function invariantAggregateUsdgMatchesSinglePairClaim() external view {
        assertEq(
            vault.aggregateUsdgPrincipal(address(usdg)),
            vault.accountedAssets(PAIR_ID, address(usdg))
        );
    }
}
