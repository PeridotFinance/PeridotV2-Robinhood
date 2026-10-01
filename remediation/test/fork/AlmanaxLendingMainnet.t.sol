// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {PErc20Delegator} from "peridot/PErc20Delegator.sol";
import {RobinhoodBoostedDelegate} from "peridot/boosted/RobinhoodBoostedDelegate.sol";

interface IScanController {
    function oracle() external view returns (address);
    function borrowAllowed(address, address, uint256) external returns (uint256);
    function enterMarkets(address[] calldata) external returns (uint256[] memory);
    function getAccountLiquidity(address) external view returns (uint256, uint256, uint256);
}

/// @notice Diagnostic tests of installed contracts; no signing or mainnet mutations.
/// Zero-price cases explicitly inject a failure at the oracle on the local fork only.
contract AlmanaxLendingMainnetForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    IScanController constant controller = IScanController(CONTROLLER);

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        for (uint256 i; i < 2; i++) {
            PErc20Delegator market = PErc20Delegator(payable(i == 0 ? PSTOCK : PUSDG));
            assertEq(market.implementation().codehash,
                0xa6913bd52087e56926b3f17fd131b7f331af194aaa77321e452582e75fb8cc34);
            assertEq(market.underlying(), i == 0 ? STOCK : USDG);
            assertGt(market.totalSupply(), 0);
        }
    }

    function testZeroBorrowPriceRejectedForBothMarkets() public {
        for (uint256 i; i < 2; i++) {
            address market = i == 0 ? PSTOCK : PUSDG;
            address asset = i == 0 ? STOCK : USDG;
            vm.mockCall(controller.oracle(), abi.encodeWithSignature("getUnderlyingPrice(address)", market), abi.encode(uint256(0)));
            vm.prank(market);
            assertEq(controller.borrowAllowed(market, GOVERNOR, 1), 13, "PRICE_ERROR expected");
            uint256 cash = IERC20(asset).balanceOf(market);
            uint256 balance = IERC20(asset).balanceOf(GOVERNOR);
            uint256 debt = PErc20Delegator(payable(market)).borrowBalanceStored(GOVERNOR);
            vm.expectRevert(abi.encodeWithSignature("BorrowPeridottrollerRejection(uint256)", 13));
            vm.prank(GOVERNOR);
            PErc20Delegator(payable(market)).borrow(1);
            assertEq(IERC20(asset).balanceOf(market), cash);
            assertEq(IERC20(asset).balanceOf(GOVERNOR), balance);
            assertEq(PErc20Delegator(payable(market)).borrowBalanceStored(GOVERNOR), debt);
            vm.clearMockedCalls();
        }
    }

    function testZeroCollateralPriceRejectsLiquidityCalculation() public {
        address[] memory markets = new address[](1);
        markets[0] = PSTOCK;
        vm.prank(GOVERNOR);
        assertEq(controller.enterMarkets(markets)[0], 0);
        vm.mockCall(controller.oracle(), abi.encodeWithSignature("getUnderlyingPrice(address)", PSTOCK), abi.encode(uint256(0)));
        (uint256 err,,) = controller.getAccountLiquidity(GOVERNOR);
        assertEq(err, 13);
    }

    function testConfirmedDustStockMintTransfersUnderlyingForZeroShares() public {
        PErc20Delegator market = PErc20Delegator(payable(PSTOCK));
        uint256 rate = market.exchangeRateCurrent();
        uint256 amount = (rate - 1) / 1e18;
        assertGt(amount, 0);
        assertGe(IERC20(STOCK).balanceOf(GOVERNOR), amount);
        uint256 beforeUnderlying = IERC20(STOCK).balanceOf(GOVERNOR);
        uint256 beforeShares = market.balanceOf(GOVERNOR);
        uint256 beforeSupply = market.totalSupply();
        vm.startPrank(GOVERNOR);
        IERC20(STOCK).approve(PSTOCK, amount);
        assertEq(market.mint(amount), 0);
        vm.stopPrank();
        assertEq(beforeUnderlying - IERC20(STOCK).balanceOf(GOVERNOR), amount);
        assertEq(market.balanceOf(GOVERNOR), beforeShares);
        assertEq(market.totalSupply(), beforeSupply);
        emit log_named_uint("NVDA raw units lost to zero-share mint", amount);
        emit log_named_uint("pNVDA exchange rate", rate);
        emit log_named_uint("pNVDA total supply raw", beforeSupply);
    }

    function testMinimumDollarUnitMintsNonzeroSharesAtPinnedState() public {
        PErc20Delegator market = PErc20Delegator(payable(PUSDG));
        uint256 beforeShares = market.balanceOf(GOVERNOR);
        vm.startPrank(GOVERNOR);
        IERC20(USDG).approve(PUSDG, 1);
        assertEq(market.mint(1), 0);
        vm.stopPrank();
        assertGt(market.balanceOf(GOVERNOR), beforeShares);
        emit log_named_uint("pUSDG shares for one raw USDG unit", market.balanceOf(GOVERNOR) - beforeShares);
    }

    function testInstalledProxyAndDelegateAgreeWithStorageSlots() public view {
        // Solc layouts for both captured sources: underlying at slot 19 offset 1,
        // implementation at slot 20 offset 0. The packed boolean occupies offset 0.
        for (uint256 i; i < 2; i++) {
            address target = i == 0 ? PSTOCK : PUSDG;
            PErc20Delegator market = PErc20Delegator(payable(target));
            address asset = i == 0 ? STOCK : USDG;
            address rawAsset = address(uint160(uint256(vm.load(target, bytes32(uint256(19)))) >> 8));
            address rawImplementation = address(uint160(uint256(vm.load(target, bytes32(uint256(20))))));
            address delegateAsset = abi.decode(market.delegateToViewImplementation(abi.encodeWithSignature("underlying()")), (address));
            address delegateImplementation = abi.decode(market.delegateToViewImplementation(abi.encodeWithSignature("implementation()")), (address));
            assertEq(rawAsset, asset);
            assertEq(market.underlying(), asset);
            assertEq(delegateAsset, asset);
            assertEq(rawImplementation, market.implementation());
            assertEq(delegateImplementation, rawImplementation);
        }
    }

    function testSubShareStockRedemptionRevertsWithoutTransfer() public {
        PErc20Delegator market = PErc20Delegator(payable(PSTOCK));
        uint256 rate = market.exchangeRateCurrent();
        uint256 beforeBalance = IERC20(STOCK).balanceOf(GOVERNOR);
        vm.expectRevert(bytes("redeemTokens zero"));
        vm.prank(GOVERNOR);
        market.redeemUnderlying((rate - 1) / 1e18);
        assertEq(IERC20(STOCK).balanceOf(GOVERNOR), beforeBalance);
    }
}
