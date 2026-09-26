// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { RobinhoodLendingPriceAdapter } from "../../src/RobinhoodLendingPriceAdapter.sol";
import { ReactivateLending } from "../../script/ReactivateLending.s.sol";

interface ILiveController {
    function borrowGuardianPaused(address) external view returns (bool);
    function seizeGuardianPaused() external view returns (bool);
    function _setBorrowPaused(address, bool) external returns (bool);
    function _setSeizePaused(bool) external returns (bool);
    function admin() external view returns (address);
    function oracle() external view returns (address);
    function _setPriceOracle(address) external returns (uint256);
    function enterMarkets(address[] calldata) external returns (uint256[] memory);
    function getAccountLiquidity(address) external view returns (uint256, uint256, uint256);
    function getHypotheticalAccountLiquidity(address, address, uint256, uint256)
        external
        view
        returns (uint256, uint256, uint256);
    function liquidateCalculateSeizeTokens(address, address, uint256)
        external
        view
        returns (uint256, uint256);
}

interface ILiveMarket {
    function exchangeRateStored() external view returns (uint256);
}

interface ILiveSource {
    function assetPrices(address) external view returns (uint256);
}

contract LendingMainnetForkTest is Test {
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant SOURCE = 0x266F014d1325774F1190f963Df4369E07dDA1d33;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    ILiveController internal controller = ILiveController(CONTROLLER);
    RobinhoodLendingPriceAdapter internal adapter;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        assertEq(block.chainid, 4663);
        if (controller.oracle() != SOURCE) {
            assertEq(
                controller.oracle(),
                0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4,
                "Review unexpected oracle change"
            );
            // Reproduce the original defect on this local fork after the mainnet repair.
            address admin = controller.admin();
            vm.prank(admin);
            assertEq(controller._setPriceOracle(SOURCE), 0);
        }
        adapter = new RobinhoodLendingPriceAdapter(SOURCE, PSTOCK, PUSDG);
    }

    function _switch() internal {
        address admin = controller.admin();
        vm.prank(admin);
        assertEq(controller._setPriceOracle(address(adapter)), 0);
        assertEq(controller.oracle(), address(adapter));
    }

    function _prepareReactivation() internal {
        address admin = controller.admin();
        vm.startPrank(admin);
        controller._setPriceOracle(0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4);
        controller._setBorrowPaused(PUSDG, true);
        controller._setBorrowPaused(PSTOCK, true);
        controller._setSeizePaused(true);
        vm.stopPrank();
    }

    function testReactivationRejectsUnavailableGuardBeforeAnyUnpause() public {
        _prepareReactivation();
        ReactivateLending script = new ReactivateLending();
        vm.mockCallRevert(
            0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741,
            abi.encodeWithSignature("pricesUSD18(bytes32)", keccak256("NVDA/USDG")),
            abi.encodeWithSignature("Error(string)", "UNAVAILABLE_GUARD")
        );
        vm.expectRevert("UNAVAILABLE_GUARD");
        script.run();
        assertTrue(controller.seizeGuardianPaused());
        assertTrue(controller.borrowGuardianPaused(PUSDG));
        assertTrue(controller.borrowGuardianPaused(PSTOCK));
    }

    function testReactivationWithSimulatedFreshMatchingPricesRestoresFlags() public {
        _prepareReactivation();
        uint256 stockPrice = ILiveSource(SOURCE).assetPrices(STOCK);
        vm.mockCall(
            0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741,
            abi.encodeWithSignature("pricesUSD18(bytes32)", keccak256("NVDA/USDG")),
            abi.encode(stockPrice, uint256(1e18))
        );
        new ReactivateLending().run();
        assertFalse(controller.seizeGuardianPaused());
        assertFalse(controller.borrowGuardianPaused(PUSDG));
        assertFalse(controller.borrowGuardianPaused(PSTOCK));
    }

    function testLiveOldQuoteReproducesThenFixesDollarDebt() public {
        (, uint256 beforeShares) = controller.liquidateCalculateSeizeTokens(PUSDG, PSTOCK, 1e6);
        assertEq(beforeShares, 0);
        _switch();
        (uint256 err, uint256 afterShares) =
            controller.liquidateCalculateSeizeTokens(PUSDG, PSTOCK, 1e6);
        uint256 price = ILiveSource(SOURCE).assetPrices(STOCK);
        uint256 rate = ILiveMarket(PSTOCK).exchangeRateStored();
        uint256 expected = 1e6 * 1e18 * 1.08e18 * 1e18 / (1e6 * price * rate);
        assertEq(err, 0);
        assertApproxEqAbs(afterShares, expected, 1);
        assertGt(afterShares, 0);
    }

    function testLiveStockDebtLiquidationUsesSixDecimalCollateral() public {
        _switch();
        (uint256 err, uint256 shares) =
            controller.liquidateCalculateSeizeTokens(PSTOCK, PUSDG, 1e16);
        uint256 price = ILiveSource(SOURCE).assetPrices(STOCK);
        uint256 rate = ILiveMarket(PUSDG).exchangeRateStored();
        uint256 expected = 1e16 * price * 1.08e18 * 1e6 / (1e18 * 1e18 * rate);
        assertEq(err, 0);
        assertApproxEqAbs(shares, expected, 1);
    }

    function testLiveControllerBorrowValuationAndDollarApiCompatibility() public {
        _switch();
        address user = makeAddr("synthetic-account");
        address[] memory markets = new address[](2);
        markets[0] = PUSDG;
        markets[1] = PSTOCK;
        vm.prank(user);
        controller.enterMarkets(markets);
        // Mock only account balances/rates; use the actual proxy, controller and source on the fork.
        vm.mockCall(
            PUSDG,
            abi.encodeWithSignature("getAccountSnapshot(address)", user),
            abi.encode(0, 50_000e8, 0, 2e14)
        );
        vm.mockCall(
            PSTOCK,
            abi.encodeWithSignature("getAccountSnapshot(address)", user),
            abi.encode(0, 0, 0, 2e26)
        );
        (uint256 err, uint256 liquidity, uint256 shortfall) = controller.getAccountLiquidity(user);
        assertEq(err, 0);
        assertEq(liquidity, 800e18);
        assertEq(shortfall, 0);
        (err, liquidity, shortfall) =
            controller.getHypotheticalAccountLiquidity(user, PUSDG, 0, 801e6);
        assertEq(err, 0);
        assertEq(liquidity, 0);
        assertEq(shortfall, 1e18);
        assertEq(adapter.assetPrices(USDG), 1e18);
        assertEq(ILiveSource(SOURCE).assetPrices(USDG), 1e18);
        assertEq(adapter.getUnderlyingPrice(PUSDG), 1e30);
    }
}
