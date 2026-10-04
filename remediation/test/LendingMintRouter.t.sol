// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { MockERC20 } from "baseline/test/mocks/MockERC20.sol";
import { Peridottroller } from "peridot/Peridottroller.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { InterestRateModel } from "peridot/InterestRateModel.sol";
import { PToken } from "peridot/PToken.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { RobinhoodBoostedDelegate } from "peridot/boosted/RobinhoodBoostedDelegate.sol";
import { RobinhoodBoostedDelegateV2 } from "../src/RobinhoodBoostedDelegateV2.sol";
import { LendingMintRouter } from "../src/LendingMintRouter.sol";
import { RoundingZeroRate, RoundingOracle } from "./LendingRounding.t.sol";

/// @notice The router against both delegates. Same scenarios run on the original (zero-share
/// accepting) and the corrected delegate: the router's minimum must protect in both.
contract LendingMintRouterTest is Test {
    address internal victim = makeAddr("victim");
    address internal attacker = makeAddr("attacker");
    Peridottroller internal controller;
    MockERC20 internal stock;
    MockERC20 internal dollar;
    PErc20Delegator[2] internal stockMarket; // [original, corrected]
    PErc20Delegator[2] internal dollarMarket;
    LendingMintRouter[2] internal router;

    event MintedWithMinimum(
        address indexed market,
        address indexed account,
        uint256 amount,
        uint256 minted,
        uint256 minimum
    );

    function setUp() public {
        controller = new Peridottroller();
        assertEq(controller._setPriceOracle(new RoundingOracle()), 0);
        stock = new MockERC20("Stock model", "STOCK", 18);
        dollar = new MockERC20("Dollar model", "DOLLAR", 6);
        RoundingZeroRate rate = new RoundingZeroRate();
        address[2] memory delegates =
            [address(new RobinhoodBoostedDelegate()), address(new RobinhoodBoostedDelegateV2())];
        for (uint256 i; i < 2; i++) {
            stockMarket[i] = _market(stock, rate, delegates[i], 2e26);
            dollarMarket[i] = _market(dollar, rate, delegates[i], 2e14);
            router[i] = new LendingMintRouter(address(stockMarket[i]), address(dollarMarket[i]));
        }
        stock.mint(victim, 100e18);
        stock.mint(attacker, 100e18);
        dollar.mint(victim, 100e6);
        for (uint256 i; i < 2; i++) {
            vm.startPrank(victim);
            stock.approve(address(router[i]), type(uint256).max);
            dollar.approve(address(router[i]), type(uint256).max);
            vm.stopPrank();
            vm.startPrank(attacker);
            stock.approve(address(stockMarket[i]), type(uint256).max);
            vm.stopPrank();
        }
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

    function _noResidue(uint256 i) internal view {
        assertEq(stock.balanceOf(address(router[i])), 0);
        assertEq(dollar.balanceOf(address(router[i])), 0);
        assertEq(stockMarket[i].balanceOf(address(router[i])), 0);
        assertEq(dollarMarket[i].balanceOf(address(router[i])), 0);
        assertEq(stock.allowance(address(router[i]), address(stockMarket[i])), 0);
        assertEq(dollar.allowance(address(router[i]), address(dollarMarket[i])), 0);
    }

    /// Donation leaves two seed shares worth a lot, so a 1e18 deposit prices to a single share.
    function _seedAndDonate(uint256 i) internal {
        vm.startPrank(attacker);
        assertEq(stockMarket[i].mint(4e8), 0);
        stock.transfer(address(stockMarket[i]), 1e18);
        vm.stopPrank();
    }

    // ---------------------------------------------------------------- happy path

    function testMintForwardsAllSharesToCallerOnBothDelegates() public {
        for (uint256 i; i < 2; i++) {
            vm.expectEmit(true, true, false, true);
            emit MintedWithMinimum(address(stockMarket[i]), victim, 1e18, 5e9, 5e9);
            uint256 underlyingBefore = stock.balanceOf(victim);
            vm.prank(victim);
            uint256 minted = router[i].mintWithMinShares(address(stockMarket[i]), 1e18, 5e9);
            assertEq(minted, 5e9);
            assertEq(stockMarket[i].balanceOf(victim), 5e9);
            assertEq(underlyingBefore - stock.balanceOf(victim), 1e18);
            _noResidue(i);
        }
    }

    function testSixDecimalDollarMintAndExactMinimum() public {
        for (uint256 i; i < 2; i++) {
            vm.startPrank(victim);
            assertEq(router[i].mintWithMinShares(address(dollarMarket[i]), 1, 5000), 5000);
            vm.expectRevert(
                abi.encodeWithSelector(LendingMintRouter.InsufficientShares.selector, 5000, 5001)
            );
            router[i].mintWithMinShares(address(dollarMarket[i]), 1, 5001);
            vm.stopPrank();
            assertEq(dollarMarket[i].balanceOf(victim), 5000);
            _noResidue(i);
        }
    }

    // ---------------------------------------------------------------- protection

    /// A nonzero but tiny share count is exactly what the delegate fix cannot bound.
    function testMinimumRejectsNonzeroRoundingLossOnBothDelegates() public {
        for (uint256 i; i < 2; i++) {
            _seedAndDonate(i);
            uint256 before = stock.balanceOf(victim);
            vm.prank(victim);
            vm.expectRevert(
                abi.encodeWithSelector(LendingMintRouter.InsufficientShares.selector, 1, 2)
            );
            router[i].mintWithMinShares(address(stockMarket[i]), 1e18, 2);
            assertEq(stock.balanceOf(victim), before, "atomic rollback");
            assertEq(stockMarket[i].balanceOf(victim), 0);
            _noResidue(i);
        }
    }

    /// Against the ORIGINAL delegate the router alone blocks the zero-share mint; against the
    /// corrected one the delegate itself reverts first. Either way nothing is taken.
    function testZeroShareMintIsBlockedOnBothDelegates() public {
        for (uint256 i; i < 2; i++) {
            _seedAndDonate(i);
            uint256 before = stock.balanceOf(victim);
            vm.prank(victim);
            if (i == 0) {
                vm.expectRevert(
                    abi.encodeWithSelector(LendingMintRouter.InsufficientShares.selector, 0, 1)
                );
            } else {
                vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
            }
            router[i].mintWithMinShares(address(stockMarket[i]), 0.1e18, 1);
            assertEq(stock.balanceOf(victim), before);
            _noResidue(i);
        }
    }

    // ---------------------------------------------------------------- input checks

    function testRejectsZeroAmountZeroMinimumAndUnsupportedMarket() public {
        vm.startPrank(victim);
        vm.expectRevert(LendingMintRouter.ZeroAmount.selector);
        router[1].mintWithMinShares(address(stockMarket[1]), 0, 1);
        vm.expectRevert(LendingMintRouter.ZeroMinShares.selector);
        router[1].mintWithMinShares(address(stockMarket[1]), 1e18, 0);
        vm.expectRevert(LendingMintRouter.UnsupportedMarket.selector);
        router[1].mintWithMinShares(address(stockMarket[0]), 1e18, 1); // other deployment's market
        vm.expectRevert(LendingMintRouter.UnsupportedMarket.selector);
        router[1].mintWithMinShares(address(stock), 1e18, 1);
        vm.expectRevert(LendingMintRouter.UnsupportedMarket.selector);
        router[1].mintWithMinShares(address(0), 1e18, 1);
        vm.stopPrank();
        _noResidue(1);
    }

    function testRevertsWithoutAllowance() public {
        address stranger = makeAddr("stranger");
        stock.mint(stranger, 1e18);
        vm.prank(stranger);
        vm.expectRevert();
        router[1].mintWithMinShares(address(stockMarket[1]), 1e18, 1);
        assertEq(stock.balanceOf(stranger), 1e18);
    }

    function testFeeOnTransferUnderlyingIsRejected() public {
        stock.setTransferFee(100, address(0), address(0));
        vm.prank(victim);
        vm.expectRevert(LendingMintRouter.TransferInMismatch.selector);
        router[1].mintWithMinShares(address(stockMarket[1]), 1e18, 1);
        stock.setTransferFee(0, address(0), address(0));
        _noResidue(1);
    }

    function testConstructorRejectsBadMarkets() public {
        vm.expectRevert("SAME_MARKET");
        new LendingMintRouter(address(stockMarket[1]), address(stockMarket[1]));
        vm.expectRevert("NO_CODE");
        new LendingMintRouter(address(stockMarket[1]), address(0xdead));
    }

    function testTokensSentToRouterByMistakeDoNotBreakOrLeakIntoMints() public {
        vm.prank(victim);
        stock.transfer(address(router[1]), 5e17); // pre-existing balance
        vm.prank(victim);
        router[1].mintWithMinShares(address(stockMarket[1]), 1e18, 5e9);
        assertEq(stockMarket[1].balanceOf(victim), 5e9, "only the pulled amount was supplied");
        assertEq(stock.balanceOf(address(router[1])), 5e17, "stray balance untouched");
    }

    // ---------------------------------------------------------------- property

    function testFuzzMintMatchesFloorEstimateAndMinimumIsExact(
        uint256 seed,
        uint256 donation,
        uint256 amount,
        uint256 minShares
    ) public {
        seed = bound(seed, 2e8, 1e22);
        donation = bound(donation, 0, 1e22);
        amount = bound(amount, 1, 50e18);
        minShares = bound(minShares, 1, 1e12);
        stock.mint(attacker, seed + donation);
        vm.startPrank(attacker);
        stockMarket[1].mint(seed);
        stock.transfer(address(stockMarket[1]), donation);
        vm.stopPrank();

        uint256 expected = amount * 1e18 / stockMarket[1].exchangeRateStored();
        uint256 balanceBefore = stock.balanceOf(victim);
        vm.prank(victim);
        (bool ok, bytes memory ret) = address(router[1])
            .call(
                abi.encodeCall(
                    LendingMintRouter.mintWithMinShares,
                    (address(stockMarket[1]), amount, minShares)
                )
            );
        if (expected >= minShares && expected > 0) {
            assertTrue(ok, "must succeed when the estimate meets the minimum");
            assertEq(abi.decode(ret, (uint256)), expected);
            assertEq(stockMarket[1].balanceOf(victim), expected);
            assertEq(stock.balanceOf(victim), balanceBefore - amount);
        } else {
            assertFalse(ok, "must revert when the minimum cannot be met");
            assertEq(stock.balanceOf(victim), balanceBefore);
            assertEq(stockMarket[1].balanceOf(victim), 0);
        }
        _noResidue(1);
    }
}
