// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { MockERC20 } from "baseline/test/mocks/MockERC20.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import { IStockOracleGuard } from "baseline/src/interfaces/IStockOracleGuard.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RangeLib } from "../src/libraries/RangeLib.sol";

/// @dev Adapter double with knobs for the behaviours RangeLib.deploy has to survive.
contract AdapterDouble {
    MockERC20 public stock;
    MockERC20 public usdg;
    bool public refuse; // revert like the real adapter does for unusable amounts
    uint256 public reportUsedStock; // 0 = honest
    uint256 public lpStock; // what positionStateAt reports
    uint256 public lpUsdg;
    uint256 public valueHaircutBps; // new liquidity is worth this much less at the oracle price

    constructor(MockERC20 s, MockERC20 u) {
        stock = s;
        usdg = u;
    }

    function setRefuse(bool v) external {
        refuse = v;
    }

    function setLie(uint256 v) external {
        reportUsedStock = v;
    }

    function setHaircut(uint256 v) external {
        valueHaircutBps = v;
    }

    function addLiquidity(bytes32, uint256 s, uint256 u, uint256)
        external
        returns (uint256, uint256, uint128)
    {
        if (refuse || s == 0 || u == 0) revert("ADAPTER_REFUSES");
        stock.transferFrom(msg.sender, address(this), s);
        usdg.transferFrom(msg.sender, address(this), u);
        lpStock += s * (10_000 - valueHaircutBps) / 10_000;
        lpUsdg += u * (10_000 - valueHaircutBps) / 10_000;
        return (reportUsedStock != 0 ? reportUsedStock : s, u, 1);
    }

    function positionStateAt(bytes32, uint160)
        external
        view
        returns (IUniswapV4PairedAdapter.PositionState memory)
    {
        return IUniswapV4PairedAdapter.PositionState(1, 1, lpStock, lpUsdg);
    }
}

/// @dev Holds the storage RangeLib works on, like the vault does.
contract DeployHarness {
    PairConfig internal config;
    PairLedger internal ledger;

    constructor(address stock, address usdg) {
        config.stockToken = stock;
        config.usdg = usdg;
        config.stockDecimals = 18;
        config.usdgDecimals = 6;
    }

    function setIdle(uint256 stockIdle, uint256 usdgIdle) external {
        ledger.stockIdle = stockIdle;
        ledger.usdgIdle = usdgIdle;
    }

    function idle() external view returns (uint256, uint256) {
        return (ledger.stockIdle, ledger.usdgIdle);
    }

    function run(address adapter, uint256 cap, uint16 lossBps, bool allowEmpty)
        external
        returns (uint128 liquidity, uint256 stockUsed, uint256 usdgUsed)
    {
        RangeLib.Ctx memory c = RangeLib.Ctx({
            pairId: bytes32(uint256(1)),
            stockPrice: 100e18,
            usdgPrice: 1e18,
            ref: 1,
            deadline: block.timestamp + 60,
            adapter: IUniswapV4PairedAdapter(adapter),
            guard: IStockOracleGuard(address(0))
        });
        return RangeLib.deploy(config, ledger, c, cap, lossBps, allowEmpty);
    }
}

contract RangeLibDeployTest is Test {
    MockERC20 stock;
    MockERC20 usdg;
    AdapterDouble adapter;
    DeployHarness harness;

    function setUp() public {
        stock = new MockERC20("NVDA", "NVDA", 18);
        usdg = new MockERC20("USDG", "USDG", 6);
        adapter = new AdapterDouble(stock, usdg);
        harness = new DeployHarness(address(stock), address(usdg));
    }

    function _fund(uint256 s, uint256 u) internal {
        stock.mint(address(harness), s);
        usdg.mint(address(harness), u);
        harness.setIdle(s, u);
    }

    function _noAllowance() internal view {
        assertEq(stock.allowance(address(harness), address(adapter)), 0);
        assertEq(usdg.allowance(address(harness), address(adapter)), 0);
    }

    function testDeploysMatchedValueAndBooksUsage() public {
        _fund(1e18, 200e6); // $100 of NVDA, $200 of USDG: the NVDA side limits
        (uint128 liq, uint256 s, uint256 u) =
            harness.run(address(adapter), type(uint256).max, 10_000, false);
        assertEq(liq, 1);
        assertEq(s, 1e18);
        assertEq(u, 100e6);
        (uint256 si, uint256 ui) = harness.idle();
        assertEq(si, 0);
        assertEq(ui, 100e6);
        _noAllowance();
    }

    function testCapLimitsEachSide() public {
        _fund(1e18, 200e6);
        (, uint256 s, uint256 u) = harness.run(address(adapter), 40e18, 10_000, false);
        assertEq(s, 0.4e18);
        assertEq(u, 40e6);
    }

    /// 1 wei of NVDA is worth 100 USD18 units but rounds to zero USDG: dust must not trap an exit.
    function testDustFinishesIdleOnlyWhenAllowed() public {
        _fund(1, 200e6);
        (uint128 liq, uint256 s, uint256 u) =
            harness.run(address(adapter), type(uint256).max, 10_000, true);
        assertEq(liq + s + u, 0);
        (uint256 si, uint256 ui) = harness.idle();
        assertEq(si, 1);
        assertEq(ui, 200e6);
        vm.expectRevert(bytes("ADAPTER_REFUSES"));
        harness.run(address(adapter), type(uint256).max, 10_000, false);
    }

    function testNothingIdleFinishesOnlyWhenAllowed() public {
        _fund(0, 200e6);
        (uint128 liq,,) = harness.run(address(adapter), type(uint256).max, 10_000, true);
        assertEq(liq, 0);
        vm.expectRevert(RangeLib.InsufficientLiquidity.selector);
        harness.run(address(adapter), type(uint256).max, 10_000, false);
    }

    function testAdapterRefusalLeavesAssetsIdleAndApprovalsRevokedWhenSoft() public {
        _fund(1e18, 200e6);
        adapter.setRefuse(true);
        (uint128 liq,,) = harness.run(address(adapter), type(uint256).max, 10_000, true);
        assertEq(liq, 0);
        (uint256 si, uint256 ui) = harness.idle();
        assertEq(si, 1e18);
        assertEq(ui, 200e6);
        assertEq(stock.balanceOf(address(harness)), 1e18);
        _noAllowance();
        vm.expectRevert(bytes("ADAPTER_REFUSES"));
        harness.run(address(adapter), type(uint256).max, 10_000, false);
    }

    function testLossBoundRejectsOverpricedLiquidity() public {
        _fund(1e18, 200e6);
        adapter.setHaircut(50); // liquidity is worth 0.5% less than what was paid in
        vm.expectRevert(RangeLib.DeployLossTooHigh.selector);
        harness.run(address(adapter), type(uint256).max, 10, false);
        // The same deployment passes under a 1% bound, and with the bound disabled.
        (uint128 liq,,) = harness.run(address(adapter), type(uint256).max, 100, false);
        assertEq(liq, 1);
    }

    function testLossBoundDisabledAtOrAboveTenThousand() public {
        _fund(1e18, 200e6);
        adapter.setHaircut(900);
        (uint128 liq,,) = harness.run(address(adapter), type(uint256).max, 10_000, false);
        assertEq(liq, 1);
    }

    function testReportedUsageMustMatchBalanceDelta() public {
        _fund(1e18, 200e6);
        adapter.setLie(0.5e18); // adapter keeps 1e18 but reports 0.5e18
        vm.expectRevert(RangeLib.BalanceDeltaMismatch.selector);
        harness.run(address(adapter), type(uint256).max, 10_000, false);
    }

    function testFuzzIdleNeverIncreasesAndNeverExceedsCap(uint96 s, uint96 u, uint96 cap) public {
        uint256 stockIdle = bound(s, 0, 1000e18);
        uint256 usdgIdle = bound(u, 0, 100_000e6);
        _fund(stockIdle, usdgIdle);
        uint256 capValue = bound(cap, 1, 1_000_000e18);
        try harness.run(address(adapter), capValue, 10_000, true) returns (
            uint128, uint256 su, uint256 uu
        ) {
            (uint256 si, uint256 ui) = harness.idle();
            assertEq(si, stockIdle - su);
            assertEq(ui, usdgIdle - uu);
            assertLe(su * 100, capValue, "NVDA side within the per-side cap");
            assertLe(uu * 1e12, capValue, "USDG side within the per-side cap");
        } catch {
            assertTrue(false, "allowEmpty must never revert on healthy inputs");
        }
    }
}
