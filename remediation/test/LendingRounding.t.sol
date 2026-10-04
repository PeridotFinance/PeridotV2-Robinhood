// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { MockERC20 } from "baseline/test/mocks/MockERC20.sol";
import { Peridottroller } from "peridot/Peridottroller.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { InterestRateModel } from "peridot/InterestRateModel.sol";
import { PriceOracle } from "peridot/PriceOracle.sol";
import { PToken } from "peridot/PToken.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { TokenErrorReporter } from "peridot/ErrorReporter.sol";
import { RobinhoodBoostedDelegate } from "peridot/boosted/RobinhoodBoostedDelegate.sol";
import { RobinhoodBoostedDelegateV2 } from "../src/RobinhoodBoostedDelegateV2.sol";

contract RoundingZeroRate is InterestRateModel {
    function getBorrowRate(uint256, uint256, uint256) external pure override returns (uint256) {
        return 0;
    }

    function getSupplyRate(uint256, uint256, uint256, uint256)
        external
        pure
        override
        returns (uint256)
    {
        return 0;
    }
}

contract RoundingOracle is PriceOracle {
    mapping(address => uint256) public prices;

    function set(address market, uint256 price) external {
        prices[market] = price;
    }

    function getUnderlyingPrice(PToken market) public view override returns (uint256) {
        return prices[address(market)];
    }
}

/// @notice Adversarial fresh-market model using the frozen real controller/delegate.
/// Mock assets, fixed prices and zero interest isolate rounding; this is not a claim
/// that current mainnet seed holders have exited or that an attack happened there.
/// The V2 markets run the same model behind the same delegator against the remediated delegate.
contract LendingRoundingTest is Test {
    address internal attacker = makeAddr("attacker");
    address internal victim = makeAddr("victim");
    MockERC20 internal stock;
    MockERC20 internal dollar;
    PErc20Delegator internal stockMarket;
    PErc20Delegator internal dollarMarket;
    Peridottroller internal controller;

    RobinhoodBoostedDelegateV2 internal delegateV2;
    PErc20Delegator internal stockMarketV2;
    PErc20Delegator internal dollarMarketV2;

    function setUp() public {
        controller = new Peridottroller();
        RoundingOracle oracle = new RoundingOracle();
        assertEq(controller._setPriceOracle(oracle), 0);
        stock = new MockERC20("Stock model", "STOCK", 18);
        dollar = new MockERC20("Dollar model", "DOLLAR", 6);
        RoundingZeroRate rate = new RoundingZeroRate();
        RobinhoodBoostedDelegate delegate = new RobinhoodBoostedDelegate();
        stockMarket = _market(stock, rate, address(delegate), 2e26);
        dollarMarket = _market(dollar, rate, address(delegate), 2e14);
        oracle.set(address(stockMarket), 1e18);
        oracle.set(address(dollarMarket), 1e30);
        assertEq(controller._setCollateralFactor(PToken(address(stockMarket)), 0.75e18), 0);
        dollar.mint(address(this), 10e6);
        dollar.approve(address(dollarMarket), type(uint256).max);
        assertEq(dollarMarket.mint(10e6), 0);
        stock.mint(attacker, 3e18);
        stock.mint(victim, 3e18);
        vm.prank(attacker);
        stock.approve(address(stockMarket), type(uint256).max);
        vm.prank(victim);
        stock.approve(address(stockMarket), type(uint256).max);

        delegateV2 = new RobinhoodBoostedDelegateV2();
        stockMarketV2 = _market(stock, rate, address(delegateV2), 2e26);
        dollarMarketV2 = _market(dollar, rate, address(delegateV2), 2e14);
        oracle.set(address(stockMarketV2), 1e18);
        oracle.set(address(dollarMarketV2), 1e30);
        assertEq(controller._setCollateralFactor(PToken(address(stockMarketV2)), 0.75e18), 0);
        vm.prank(attacker);
        stock.approve(address(stockMarketV2), type(uint256).max);
        vm.prank(victim);
        stock.approve(address(stockMarketV2), type(uint256).max);
    }

    function _market(MockERC20 asset, InterestRateModel rate, address delegate, uint256 initialRate)
        internal
        returns (PErc20Delegator market)
    {
        market = new PErc20Delegator(
            address(asset),
            PeridottrollerInterface(address(controller)),
            rate,
            initialRate,
            "pModel",
            "pMODEL",
            8,
            payable(address(this)),
            delegate,
            ""
        );
        assertEq(controller._supportMarket(PToken(address(market))), 0);
    }

    /// @dev Reads delegate-only getters the delegator does not declare, through its fallback.
    function _v2(PErc20Delegator market) internal pure returns (RobinhoodBoostedDelegateV2) {
        return RobinhoodBoostedDelegateV2(address(market));
    }

    function _seedAndDonate() internal {
        _seedAndDonateInto(stockMarket);
    }

    function _seedAndDonateInto(PErc20Delegator market) internal {
        vm.startPrank(attacker);
        assertEq(market.mint(4e8), 0);
        assertEq(market.totalSupply(), 2);
        stock.transfer(address(market), 1e18);
        vm.stopPrank();
    }

    function testBaselineDonationMakesVictimDepositMintZeroShares() public {
        _seedAndDonate();
        uint256 beforeBalance = stock.balanceOf(victim);
        vm.prank(victim);
        assertEq(stockMarket.mint(0.1e18), 0);
        assertEq(stockMarket.balanceOf(victim), 0);
        assertEq(beforeBalance - stock.balanceOf(victim), 0.1e18);
    }

    function testBaselineLowSupplyRedemptionLeavesUnsecuredDebt() public {
        _seedAndDonate();
        address[] memory markets = new address[](1);
        markets[0] = address(stockMarket);
        vm.startPrank(attacker);
        assertEq(controller.enterMarkets(markets)[0], 0);
        assertEq(dollarMarket.borrow(350_000), 0);
        uint256 cash = stock.balanceOf(address(stockMarket));
        assertEq(stockMarket.redeemUnderlying(cash - 1), 0);
        vm.stopPrank();
        assertEq(stockMarket.balanceOf(attacker), 1);
        assertEq(stock.balanceOf(address(stockMarket)), 1);
        assertEq(dollar.balanceOf(attacker), 350_000);
        assertEq(dollarMarket.borrowBalanceStored(attacker), 350_000);
        (uint256 error,, uint256 shortfall) = controller.getAccountLiquidity(attacker);
        assertEq(error, 0);
        assertGt(shortfall, 349_999e12);
        emit log_named_uint("Unsecured debt USD18 in conditional model", shortfall);
    }

    // ---------------------------------------------------------------------
    // Zero-share mint
    // ---------------------------------------------------------------------

    function testOldDelegateAcceptsButV2RejectsSameZeroShareMint() public {
        _seedAndDonateInto(stockMarket);
        _seedAndDonateInto(stockMarketV2);
        assertEq(stockMarket.exchangeRateStored(), stockMarketV2.exchangeRateStored());

        vm.startPrank(victim);
        assertEq(stockMarket.mint(0.1e18), 0);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        stockMarketV2.mint(0.1e18);
        vm.stopPrank();

        assertEq(stockMarket.balanceOf(victim), 0);
        assertEq(stockMarketV2.balanceOf(victim), 0);
        assertEq(stock.balanceOf(victim), 3e18 - 0.1e18, "only the old market took the deposit");
    }

    function testV2ZeroShareMintRollsBackAtomically() public {
        _seedAndDonateInto(stockMarketV2);
        uint256 victimBefore = stock.balanceOf(victim);
        uint256 cashBefore = stock.balanceOf(address(stockMarketV2));
        uint256 rateBefore = stockMarketV2.exchangeRateStored();

        vm.prank(victim);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        stockMarketV2.mint(0.1e18);

        assertEq(stock.balanceOf(victim), victimBefore);
        assertEq(stock.balanceOf(address(stockMarketV2)), cashBefore);
        assertEq(stockMarketV2.balanceOf(victim), 0);
        assertEq(stockMarketV2.totalSupply(), 2);
        assertEq(stockMarketV2.exchangeRateStored(), rateBefore);
    }

    function testV2RejectsZeroAmountMint() public {
        vm.prank(victim);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        stockMarketV2.mint(0);
    }

    function testUpgradeInPlaceToV2RejectsZeroShareMint() public {
        _seedAndDonateInto(stockMarket);
        uint256 rateBefore = stockMarket.exchangeRateStored();

        stockMarket._setImplementation(address(delegateV2), false, "");
        assertEq(stockMarket.implementation(), address(delegateV2));
        assertEq(stockMarket.exchangeRateStored(), rateBefore);
        assertEq(stockMarket.totalSupply(), 2);
        assertEq(stockMarket.balanceOf(attacker), 2);
        assertTrue(_v2(stockMarket).vaultPaused());
        assertEq(_v2(stockMarket).actionDelay(), 1 hours);

        uint256 victimBefore = stock.balanceOf(victim);
        vm.prank(victim);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        stockMarket.mint(0.1e18);
        assertEq(stock.balanceOf(victim), victimBefore);
    }

    // ---------------------------------------------------------------------
    // Legacy mint: residual risk and unchanged conventions
    // ---------------------------------------------------------------------

    /// RESIDUAL RISK, pinned deliberately: rejecting zero shares does not bound a NONZERO rounding
    /// loss. After a donation a 1e18 deposit prices to one share and is accepted because the
    /// legacy selector carries no caller-selected minimum.
    function testResidualRiskLegacyMintAcceptsNonzeroRoundingLoss() public {
        _seedAndDonateInto(stockMarketV2);
        uint256 rate = stockMarketV2.exchangeRateStored();
        assertEq(uint256(1e18) * 1e18 / rate, 1, "1e18 deposit prices to one share");

        vm.prank(victim);
        assertEq(stockMarketV2.mint(1e18), 0);
        assertEq(stockMarketV2.balanceOf(victim), 1);
        uint256 shareValue = stockMarketV2.exchangeRateStored() / 1e18;
        assertLt(shareValue, 0.7e18);
        emit log_named_uint("Legacy mint value retained for 1e18 deposit", shareValue);
    }

    /// The in-delegate minimum-share entry point was dropped: it does not fit under EIP-170
    /// (see LENDING_DELEGATE_CANDIDATE.md). Frontends must not call it on this implementation.
    function testMintWithMinSharesSelectorIsNotExposed() public {
        vm.prank(victim);
        (bool ok,) = address(stockMarketV2)
            .call(abi.encodeWithSignature("mintWithMinShares(uint256,uint256)", 1e18, 1));
        assertFalse(ok);
        assertEq(stock.balanceOf(victim), 3e18);
        assertEq(stockMarketV2.totalSupply(), 0);
    }

    function testLegacyMintKeepsNoErrorConvention() public {
        vm.prank(victim);
        assertEq(stockMarketV2.mint(1e18), 0);
        assertEq(stockMarketV2.balanceOf(victim), 5e9);
        assertEq(stock.balanceOf(victim), 2e18);

        vm.prank(attacker);
        assertEq(stockMarketV2.mint(2e8), 0);
        assertEq(stockMarketV2.balanceOf(attacker), 1);
    }

    // ---------------------------------------------------------------------
    // Six-decimal USDG model
    // ---------------------------------------------------------------------

    function testUsdgSixDecimalMintBoundsAndRedemptionRounding() public {
        dollar.mint(victim, 20e6);
        vm.startPrank(victim);
        dollar.approve(address(dollarMarketV2), type(uint256).max);

        assertEq(dollarMarketV2.mint(10e6), 0);
        assertEq(dollarMarketV2.balanceOf(victim), 5e10);
        assertEq(dollarMarketV2.mint(1), 0);
        assertEq(dollarMarketV2.balanceOf(victim), 5e10 + 5000, "one raw unit mints 5000 shares");
        assertEq(dollarMarketV2.exchangeRateStored(), 2e14);

        // Exact rate: ceil and floor agree.
        uint256 shares = dollarMarketV2.balanceOf(victim);
        assertEq(dollarMarketV2.redeemUnderlying(1), 0);
        assertEq(shares - dollarMarketV2.balanceOf(victim), 5000);
        vm.stopPrank();

        // A one-unit donation makes the rate non-integral per share.
        dollar.mint(attacker, 1);
        vm.prank(attacker);
        dollar.transfer(address(dollarMarketV2), 1);
        uint256 rate = dollarMarketV2.exchangeRateStored();
        assertEq(rate, 200_000_020_000_000);

        shares = dollarMarketV2.balanceOf(victim);
        uint256 dollarsBefore = dollar.balanceOf(victim);
        vm.prank(victim);
        assertEq(dollarMarketV2.redeemUnderlying(3), 0);
        assertEq(shares - dollarMarketV2.balanceOf(victim), 15_000, "ceil(14999.9985)");
        assertEq(dollar.balanceOf(victim) - dollarsBefore, 3);

        // Share-denominated exit still rounds payout down.
        shares = dollarMarketV2.balanceOf(victim);
        rate = dollarMarketV2.exchangeRateStored();
        uint256 cash = dollar.balanceOf(address(dollarMarketV2));
        dollarsBefore = dollar.balanceOf(victim);
        vm.prank(victim);
        assertEq(dollarMarketV2.redeem(shares), 0);
        uint256 paid = dollar.balanceOf(victim) - dollarsBefore;
        assertEq(paid, rate * shares / 1e18);
        assertLt(paid, cash);
        assertEq(dollarMarketV2.balanceOf(victim), 0);
        assertEq(dollarMarketV2.totalSupply(), 0);
    }

    // ---------------------------------------------------------------------
    // Exact-underlying redemption
    // ---------------------------------------------------------------------

    function testV2LowSupplyRedemptionRejectedWithoutUnsecuredDebt() public {
        _seedAndDonateInto(stockMarketV2);
        address[] memory markets = new address[](1);
        markets[0] = address(stockMarketV2);
        uint256 shareValue = stockMarketV2.exchangeRateStored() / 1e18;
        assertEq(shareValue, 5e17 + 2e8);

        vm.startPrank(attacker);
        assertEq(controller.enterMarkets(markets)[0], 0);
        assertEq(dollarMarket.borrow(350_000), 0);
        uint256 cash = stock.balanceOf(address(stockMarketV2));
        uint256 attackerStock = stock.balanceOf(attacker);

        // The baseline burns one share here; V2 burns both and the real controller rejects it.
        vm.expectRevert(
            abi.encodeWithSelector(TokenErrorReporter.RedeemPeridottrollerRejection.selector, 4)
        );
        stockMarketV2.redeemUnderlying(cash - 1);

        // One unit above a single share's value also needs two shares.
        vm.expectRevert(
            abi.encodeWithSelector(TokenErrorReporter.RedeemPeridottrollerRejection.selector, 4)
        );
        stockMarketV2.redeemUnderlying(shareValue + 1);
        vm.stopPrank();

        assertEq(stockMarketV2.balanceOf(attacker), 2);
        assertEq(stockMarketV2.totalSupply(), 2);
        assertEq(stock.balanceOf(address(stockMarketV2)), cash);
        assertEq(stock.balanceOf(attacker), attackerStock);
        (uint256 error,, uint256 shortfall) = controller.getAccountLiquidity(attacker);
        assertEq(error, 0);
        assertEq(shortfall, 0);

        // Exactly one share's value burns one share and stays collateralized.
        vm.prank(attacker);
        assertEq(stockMarketV2.redeemUnderlying(shareValue), 0);
        assertEq(stockMarketV2.balanceOf(attacker), 1);
        assertEq(stock.balanceOf(attacker) - attackerStock, shareValue);
        (error,, shortfall) = controller.getAccountLiquidity(attacker);
        assertEq(error, 0);
        assertEq(shortfall, 0);
    }

    function testV2ExactRedeemOfNearlyAllCashBurnsAllShares() public {
        _seedAndDonateInto(stockMarketV2);
        uint256 cash = stock.balanceOf(address(stockMarketV2));
        vm.prank(attacker);
        assertEq(stockMarketV2.redeemUnderlying(cash - 1), 0);
        assertEq(stockMarketV2.balanceOf(attacker), 0);
        assertEq(stockMarketV2.totalSupply(), 0);
        assertEq(stock.balanceOf(address(stockMarketV2)), 1);
    }

    function testExactUnderlyingRedeemOnExactRateBurnsExactShares() public {
        vm.startPrank(victim);
        assertEq(stockMarketV2.mint(1e18), 0);
        assertEq(stockMarketV2.exchangeRateStored(), 2e26);
        assertEq(stockMarketV2.redeemUnderlying(2e8), 0);
        assertEq(stockMarketV2.balanceOf(victim), 5e9 - 1);
        assertEq(stockMarketV2.redeemUnderlying(1e17), 0);
        assertEq(stockMarketV2.balanceOf(victim), 5e9 - 1 - 5e8);
        vm.stopPrank();
    }

    function testPartialExactRedeemBurnsRoundedUpVersusBaseline() public {
        PErc20Delegator[2] memory markets = [stockMarket, stockMarketV2];
        uint256[2] memory burned;
        for (uint256 i; i < 2; i++) {
            vm.prank(victim);
            assertEq(markets[i].mint(1e18), 0);
            vm.prank(attacker);
            stock.transfer(address(markets[i]), 1);
            assertEq(markets[i].exchangeRateStored(), 2e26 + 2e8);

            uint256 stockBefore = stock.balanceOf(victim);
            vm.prank(victim);
            assertEq(markets[i].redeemUnderlying(1e17), 0);
            assertEq(stock.balanceOf(victim) - stockBefore, 1e17);
            burned[i] = 5e9 - markets[i].balanceOf(victim);
        }
        assertEq(burned[0], 499_999_999, "baseline rounds burned shares down");
        assertEq(burned[1], 500_000_000, "V2 rounds burned shares up");
    }

    /// @dev Two holders plus a 7 wei donation leave a rate that is not integral per share.
    function _twoHolderNonIntegralRate() internal returns (uint256 shares, uint256 value) {
        vm.prank(attacker);
        assertEq(stockMarketV2.mint(2e8), 0);
        vm.prank(victim);
        assertEq(stockMarketV2.mint(1e18), 0);
        vm.prank(attacker);
        stock.transfer(address(stockMarketV2), 7);
        uint256 rate = stockMarketV2.exchangeRateStored();
        assertEq(rate, 2e26 + 1_399_999_999);
        shares = stockMarketV2.balanceOf(victim);
        assertEq(shares, 5e9);
        value = rate * shares / 1e18;
        assertEq(value, 1e18 + 6);
    }

    function testAllShareExactUnderlyingExitBurnsWholeBalance() public {
        (, uint256 value) = _twoHolderNonIntegralRate();
        uint256 stockBefore = stock.balanceOf(victim);
        vm.prank(victim);
        assertEq(stockMarketV2.redeemUnderlying(value), 0);
        assertEq(stockMarketV2.balanceOf(victim), 0);
        assertEq(stock.balanceOf(victim) - stockBefore, value);
        assertEq(stockMarketV2.totalSupply(), 1);
    }

    function testAllShareRedeemPaysRoundedDownValue() public {
        (uint256 shares, uint256 value) = _twoHolderNonIntegralRate();
        uint256 stockBefore = stock.balanceOf(victim);
        vm.prank(victim);
        assertEq(stockMarketV2.redeem(shares), 0);
        assertEq(stockMarketV2.balanceOf(victim), 0);
        assertEq(stock.balanceOf(victim) - stockBefore, value);
        assertEq(stockMarketV2.totalSupply(), 1);
    }

    function testFuzzExactUnderlyingRedeemNeverBurnsTooFewShares(
        uint256 seedAmount,
        uint256 donation,
        uint256 deposit,
        uint256 redeemAmount
    ) public {
        seedAmount = bound(seedAmount, 2e8, 1e24);
        donation = bound(donation, 0, 1e24);
        stock.mint(attacker, seedAmount + donation);
        vm.startPrank(attacker);
        assertEq(stockMarketV2.mint(seedAmount), 0);
        stock.transfer(address(stockMarketV2), donation);
        vm.stopPrank();

        uint256 rate = stockMarketV2.exchangeRateStored();
        uint256 minDeposit = (rate + 1e18 - 1) / 1e18;
        deposit = bound(deposit, minDeposit, minDeposit + 1e24);
        stock.mint(victim, deposit);
        vm.prank(victim);
        assertEq(stockMarketV2.mint(deposit), 0);

        uint256 shares = stockMarketV2.balanceOf(victim);
        rate = stockMarketV2.exchangeRateStored();
        redeemAmount = bound(redeemAmount, 1, rate * shares / 1e18);
        uint256 stockBefore = stock.balanceOf(victim);
        vm.prank(victim);
        assertEq(stockMarketV2.redeemUnderlying(redeemAmount), 0);

        uint256 burned = shares - stockMarketV2.balanceOf(victim);
        assertEq(stock.balanceOf(victim) - stockBefore, redeemAmount);
        assertGe(burned * rate, redeemAmount * 1e18, "burned shares worth less than the payout");
        assertLt((burned - 1) * rate, redeemAmount * 1e18, "burned more than the ceiling");
        assertGe(stockMarketV2.exchangeRateStored(), rate, "remaining suppliers diluted");
    }
}
