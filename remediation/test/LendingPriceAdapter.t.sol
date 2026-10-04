// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { Peridottroller } from "peridot/Peridottroller.sol";
import { PToken } from "peridot/PToken.sol";
import { PriceOracle } from "peridot/PriceOracle.sol";
import { RobinhoodLendingPriceAdapter } from "../src/RobinhoodLendingPriceAdapter.sol";

contract DecimalToken {
    uint8 public immutable decimals;

    constructor(uint8 value) {
        decimals = value;
    }
}

contract SnapshotMarket {
    bool public constant isPToken = true;
    uint256 public constant borrowIndex = 1e18;
    uint256 public constant totalBorrows = 0;
    address public immutable underlying;
    uint256 public immutable exchangeRateStored;
    mapping(address => uint256) public balance;
    mapping(address => uint256) public debt;

    constructor(uint8 decimals_, uint256 rate) {
        underlying = address(new DecimalToken(decimals_));
        exchangeRateStored = rate;
    }

    function set(address user, uint256 shares, uint256 borrowed) external {
        balance[user] = shares;
        debt[user] = borrowed;
    }

    function borrowBalanceStored(address user) external view returns (uint256) {
        return debt[user];
    }

    function getAccountSnapshot(address user)
        external
        view
        returns (uint256, uint256, uint256, uint256)
    {
        return (0, balance[user], debt[user], exchangeRateStored);
    }
}

contract Usd18Source {
    mapping(address => uint256) public assetPrices;

    function set(address asset, uint256 value) external {
        assetPrices[asset] = value;
    }

    function getUnderlyingPrice(address market) external view returns (uint256) {
        return assetPrices[SnapshotMarket(market).underlying()];
    }
}

contract LendingPriceAdapterTest is Test {
    Peridottroller internal controller;
    SnapshotMarket internal stock;
    SnapshotMarket internal dollar;
    Usd18Source internal source;
    RobinhoodLendingPriceAdapter internal adapter;
    address internal alice = address(0xA11CE);

    function setUp() public {
        // 8-decimal pTokens, initial rate 0.02 underlying per pToken.
        stock = new SnapshotMarket(18, 2e26);
        dollar = new SnapshotMarket(6, 2e14);
        source = new Usd18Source();
        source.set(stock.underlying(), 200e18);
        source.set(dollar.underlying(), 1e18);
        adapter = new RobinhoodLendingPriceAdapter(address(source), address(stock), address(dollar));
        controller = new Peridottroller();
        assertEq(controller._setPriceOracle(PriceOracle(address(adapter))), 0);
        assertEq(controller._supportMarket(PToken(address(stock))), 0);
        assertEq(controller._supportMarket(PToken(address(dollar))), 0);
        assertEq(controller._setCollateralFactor(PToken(address(stock)), 0.8e18), 0);
        assertEq(controller._setCollateralFactor(PToken(address(dollar)), 0.8e18), 0);
        assertEq(controller._setLiquidationIncentive(1.08e18), 0);
        address[] memory markets = new address[](2);
        markets[0] = address(stock);
        markets[1] = address(dollar);
        vm.prank(alice);
        controller.enterMarkets(markets);
    }

    function testPriceApiScalesRemainDistinct() public view {
        assertEq(adapter.getUnderlyingPrice(address(dollar)), 1e30);
        assertEq(adapter.assetPrices(dollar.underlying()), 1e18);
        assertEq(adapter.getUnderlyingPrice(address(stock)), 200e18);
        assertEq(source.getUnderlyingPrice(address(dollar)), 1e18);
    }

    function testReproducesUnsafeBorrowBeforeFix() public {
        stock.set(alice, 50e8, 0); // 1 NVDA = $200, $160 borrow power.
        controller._setPriceOracle(PriceOracle(address(source)));
        (, uint256 liquidity, uint256 shortfall) =
            controller.getHypotheticalAccountLiquidity(alice, address(dollar), 0, 161e6);
        assertGt(liquidity, 0);
        assertEq(shortfall, 0);
        controller._setPriceOracle(PriceOracle(address(adapter)));
        (, liquidity, shortfall) =
            controller.getHypotheticalAccountLiquidity(alice, address(dollar), 0, 161e6);
        assertEq(liquidity, 0);
        assertEq(shortfall, 1e18);
        assertTrue(controller.borrowAllowed(address(dollar), alice, 161e6) != 0);
        assertEq(controller.borrowAllowed(address(dollar), alice, 160e6), 0);
    }

    function testDollarCollateralSupportsStockBorrowAtCorrectLimit() public {
        dollar.set(alice, 50_000e8, 0); // $1,000 supplied; $800 collateral value.
        assertEq(controller.borrowAllowed(address(stock), alice, 4e18), 0);
        assertTrue(controller.borrowAllowed(address(stock), alice, 4e18 + 1e15) != 0);
        stock.set(alice, 0, 3e18);
        (uint256 err, uint256 liquidity, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertEq(err, 0);
        assertEq(liquidity, 200e18);
        assertEq(shortfall, 0);
    }

    function testLiquidationBothDirections() public view {
        (uint256 err, uint256 shares) =
            controller.liquidateCalculateSeizeTokens(address(dollar), address(stock), 100e6);
        assertEq(err, 0);
        assertEq(shares, 27e8); // $108 / $200 / 0.02
        (err, shares) =
            controller.liquidateCalculateSeizeTokens(address(stock), address(dollar), 0.5e18);
        assertEq(err, 0);
        assertEq(shares, 5_400e8);
    }

    function testFuzzLiquidationDollarDebt(uint96 rawAmount) public view {
        uint256 amount = bound(rawAmount, 1, 1_000_000e6);
        (, uint256 shares) =
            controller.liquidateCalculateSeizeTokens(address(dollar), address(stock), amount);
        assertEq(shares, amount * 27);
    }

    function testFuzzCollateralAndDebtShareDollarUnits(uint64 supplied, uint64 borrowed) public {
        uint256 supply = bound(supplied, 1, 1_000_000);
        uint256 borrow = bound(borrowed, 0, 1_000_000);
        dollar.set(alice, supply * 50e8, borrow * 1e6);
        (, uint256 liquidity, uint256 shortfall) = controller.getAccountLiquidity(alice);
        uint256 capacity = supply * 0.8e18;
        uint256 debtValue = borrow * 1e18;
        assertEq(liquidity, capacity > debtValue ? capacity - debtValue : 0);
        assertEq(shortfall, debtValue > capacity ? debtValue - capacity : 0);
    }

    function testZeroPriceFailsClosed() public {
        source.set(dollar.underlying(), 0);
        assertEq(adapter.getUnderlyingPrice(address(dollar)), 0);
        (uint256 err,,) = controller.getAccountLiquidity(alice);
        assertTrue(err != 0);
    }

    function testRejectsWrongDecimalsAndUnknownMarkets() public {
        SnapshotMarket wrong = new SnapshotMarket(8, 2e16);
        vm.expectRevert(RobinhoodLendingPriceAdapter.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapter(address(source), address(stock), address(wrong));
        vm.expectRevert(RobinhoodLendingPriceAdapter.UnsupportedMarket.selector);
        adapter.getUnderlyingPrice(address(wrong));
        vm.expectRevert(RobinhoodLendingPriceAdapter.UnsupportedAsset.selector);
        adapter.assetPrices(address(wrong));
    }
}
