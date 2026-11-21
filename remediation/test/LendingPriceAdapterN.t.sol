// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { Peridottroller } from "peridot/Peridottroller.sol";
import { PToken } from "peridot/PToken.sol";
import { PriceOracle } from "peridot/PriceOracle.sol";
import { RobinhoodLendingPriceAdapter } from "../src/RobinhoodLendingPriceAdapter.sol";
import { RobinhoodLendingPriceAdapterN } from "../src/RobinhoodLendingPriceAdapterN.sol";
import { SnapshotMarket, Usd18Source } from "./LendingPriceAdapter.t.sol";

/// Shares one underlying with another market (plain pNVDA and opt-in upNVDA both hold NVDA).
contract SharedUnderlyingMarket {
    bool public constant isPToken = true;
    uint256 public constant borrowIndex = 1e18;
    uint256 public constant totalBorrows = 0;
    address public immutable underlying;
    uint256 public immutable exchangeRateStored;
    mapping(address => uint256) public balance;

    constructor(address underlying_, uint256 rate) {
        underlying = underlying_;
        exchangeRateStored = rate;
    }

    function set(address user, uint256 shares) external {
        balance[user] = shares;
    }

    function borrowBalanceStored(address) external pure returns (uint256) {
        return 0;
    }

    function getAccountSnapshot(address user)
        external
        view
        returns (uint256, uint256, uint256, uint256)
    {
        return (0, balance[user], 0, exchangeRateStored);
    }
}

contract LendingPriceAdapterNTest is Test {
    Peridottroller internal controller;
    SnapshotMarket internal stock; // pNVDA
    SnapshotMarket internal dollar; // pUSDG
    SnapshotMarket internal stock2; // pSPY
    SharedUnderlyingMarket internal optStock; // upNVDA
    SharedUnderlyingMarket internal optDollar; // upUSDG
    Usd18Source internal source;
    RobinhoodLendingPriceAdapter internal adapter2;
    RobinhoodLendingPriceAdapterN internal adapterN;
    address internal alice = address(0xA11CE);
    address internal griefer = address(0xBAD);

    function setUp() public {
        stock = new SnapshotMarket(18, 2e26);
        dollar = new SnapshotMarket(6, 2e14);
        stock2 = new SnapshotMarket(18, 2e26);
        optStock = new SharedUnderlyingMarket(stock.underlying(), 2e26);
        optDollar = new SharedUnderlyingMarket(dollar.underlying(), 2e14);
        source = new Usd18Source();
        source.set(stock.underlying(), 146.3683709e18);
        source.set(dollar.underlying(), 1e18);
        source.set(stock2.underlying(), 590.25e18);
        adapter2 = new RobinhoodLendingPriceAdapter(address(source), address(stock), address(dollar));
        adapterN = new RobinhoodLendingPriceAdapterN(address(source), _five());
        controller = new Peridottroller();
        assertEq(controller._setPriceOracle(PriceOracle(address(adapterN))), 0);
        assertEq(controller._supportMarket(PToken(address(stock))), 0);
        assertEq(controller._supportMarket(PToken(address(dollar))), 0);
        assertEq(controller._supportMarket(PToken(address(stock2))), 0);
        assertEq(controller._supportMarket(PToken(address(optStock))), 0);
        assertEq(controller._supportMarket(PToken(address(optDollar))), 0);
        assertEq(controller._setCollateralFactor(PToken(address(stock)), 0.8e18), 0);
        assertEq(controller._setCollateralFactor(PToken(address(dollar)), 0.8e18), 0);
        assertEq(controller._setLiquidationIncentive(1.08e18), 0);
        assertEq(controller._setCloseFactor(0.5e18), 0);
    }

    function _five() internal view returns (address[] memory m) {
        m = new address[](5);
        m[0] = address(stock);
        m[1] = address(dollar);
        m[2] = address(stock2);
        m[3] = address(optStock);
        m[4] = address(optDollar);
    }

    function testExistingMarketsReadIdenticallyToTwoMarketAdapter() public view {
        assertEq(adapterN.getUnderlyingPrice(address(stock)), adapter2.getUnderlyingPrice(address(stock)));
        assertEq(adapterN.getUnderlyingPrice(address(dollar)), adapter2.getUnderlyingPrice(address(dollar)));
        assertEq(adapterN.assetPrices(stock.underlying()), adapter2.assetPrices(stock.underlying()));
        assertEq(adapterN.assetPrices(dollar.underlying()), adapter2.assetPrices(dollar.underlying()));
        assertEq(adapterN.getUnderlyingPrice(address(dollar)), 1e30);
        assertEq(adapterN.getUnderlyingPrice(address(stock2)), 590.25e18);
    }

    function testSharedUnderlyingPricesEqual() public view {
        assertEq(adapterN.getUnderlyingPrice(address(optStock)), adapterN.getUnderlyingPrice(address(stock)));
        assertEq(adapterN.getUnderlyingPrice(address(optDollar)), adapterN.getUnderlyingPrice(address(dollar)));
        (address a, uint256 s) = adapterN.marketAsset(address(optDollar));
        assertEq(a, dollar.underlying());
        assertEq(s, 1e12);
        assertEq(adapterN.markets().length, 5);
    }

    /// The reason the adapter must be installed before listing: entering a priced CF-0 market with zero balance must
    /// not break a borrower's liquidity computation.
    function testEnteringOptInMarketKeepsBorrowerLiquidatable() public {
        stock.set(griefer, 50e8, 0); // 1 NVDA collateral
        dollar.set(griefer, 0, 140e6); // borrows above 80 % of $146.37
        address[] memory ms = new address[](4);
        ms[0] = address(stock);
        ms[1] = address(dollar);
        ms[2] = address(optStock);
        ms[3] = address(optDollar);
        vm.prank(griefer);
        controller.enterMarkets(ms);
        (uint256 err,, uint256 shortfall) = controller.getAccountLiquidity(griefer);
        assertEq(err, 0);
        assertGt(shortfall, 0);
        assertEq(controller.liquidateBorrowAllowed(address(dollar), address(stock), alice, griefer, 1e6), 0);
    }

    /// The same grief against the two-market adapter: the liquidity loop reverts, so the borrower is unliquidatable.
    function testTwoMarketAdapterWouldMakeEnteredBorrowerUnliquidatable() public {
        controller._setPriceOracle(PriceOracle(address(adapter2)));
        stock.set(griefer, 50e8, 0);
        dollar.set(griefer, 0, 140e6);
        address[] memory ms = new address[](3);
        ms[0] = address(stock);
        ms[1] = address(dollar);
        ms[2] = address(optStock);
        vm.prank(griefer);
        controller.enterMarkets(ms);
        vm.expectRevert(RobinhoodLendingPriceAdapter.UnsupportedMarket.selector);
        controller.getAccountLiquidity(griefer);
    }

    function testCollateralFactorZeroGivesNoBorrowPower() public {
        optStock.set(alice, 50e8);
        address[] memory ms = new address[](1);
        ms[0] = address(optStock);
        vm.prank(alice);
        controller.enterMarkets(ms);
        (uint256 err, uint256 liquidity, uint256 shortfall) = controller.getAccountLiquidity(alice);
        assertEq(err, 0);
        assertEq(liquidity, 0);
        assertEq(shortfall, 0);
    }

    function testZeroPriceFailsClosed() public {
        source.set(dollar.underlying(), 0);
        assertEq(adapterN.getUnderlyingPrice(address(optDollar)), 0);
    }

    function testFuzzScale(uint128 price) public {
        source.set(stock.underlying(), price);
        source.set(dollar.underlying(), price);
        assertEq(adapterN.getUnderlyingPrice(address(optStock)), uint256(price));
        assertEq(adapterN.getUnderlyingPrice(address(optDollar)), uint256(price) * 1e12);
        assertEq(adapterN.getUnderlyingPrice(address(stock)), adapter2.getUnderlyingPrice(address(stock)));
        assertEq(adapterN.getUnderlyingPrice(address(dollar)), adapter2.getUnderlyingPrice(address(dollar)));
    }

    function testRejectsUnknownMarketsAndAssets() public {
        vm.expectRevert(RobinhoodLendingPriceAdapterN.UnsupportedMarket.selector);
        adapterN.getUnderlyingPrice(address(0x1234));
        vm.expectRevert(RobinhoodLendingPriceAdapterN.UnsupportedAsset.selector);
        adapterN.assetPrices(address(0x1234));
    }

    function testConstructorValidation() public {
        address[] memory m = new address[](0);
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(source), m);

        m = new address[](1);
        m[0] = address(stock);
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(0xC0DE), m); // source without code

        m = new address[](2);
        m[0] = address(stock);
        m[1] = address(stock);
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(source), m); // duplicate market

        m = new address[](1);
        m[0] = address(new SnapshotMarket(8, 2e16));
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(source), m); // 8 decimals

        m = new address[](1);
        m[0] = address(0);
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(source), m);

        m = new address[](17);
        for (uint256 i = 0; i < 17; i++) m[i] = address(new SnapshotMarket(18, 2e26));
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(source), m);

        m = new address[](1);
        m[0] = address(new SharedUnderlyingMarket(address(0), 1));
        vm.expectRevert(RobinhoodLendingPriceAdapterN.InvalidConfiguration.selector);
        new RobinhoodLendingPriceAdapterN(address(source), m); // zero underlying
    }
}
