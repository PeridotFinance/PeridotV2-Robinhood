// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { RobinhoodBoostedDelegateV2 } from "../../src/RobinhoodBoostedDelegateV2.sol";
import { LendingMintRouter } from "../../src/LendingMintRouter.sol";

/// @notice The min-shares router against the LIVE pNVDA and pUSDG markets, local fork only.
/// Run on the installed original delegate and again with the corrected delegate installed.
contract LendingDelegateMintRouterForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    bytes32 constant ORIGINAL = 0xa6913bd52087e56926b3f17fd131b7f331af194aaa77321e452582e75fb8cc34;

    LendingMintRouter internal router;
    address internal user = makeAddr("user");

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        assertEq(PErc20Delegator(payable(PSTOCK)).implementation().codehash, ORIGINAL);
        router = new LendingMintRouter(PSTOCK, PUSDG);
        deal(STOCK, user, 1e18);
        deal(USDG, user, 10e6);
        vm.startPrank(user);
        IERC20(STOCK).approve(address(router), type(uint256).max);
        IERC20(USDG).approve(address(router), type(uint256).max);
        vm.stopPrank();
    }

    function _install() internal {
        RobinhoodBoostedDelegateV2 candidate = new RobinhoodBoostedDelegateV2();
        vm.startPrank(GOVERNOR);
        PErc20Delegator(payable(PSTOCK))._setImplementation(address(candidate), false, "");
        PErc20Delegator(payable(PUSDG))._setImplementation(address(candidate), false, "");
        vm.stopPrank();
    }

    function _scenarios(bool corrected) internal {
        PErc20Delegator stockMarket = PErc20Delegator(payable(PSTOCK));
        PErc20Delegator usdgMarket = PErc20Delegator(payable(PUSDG));

        // Normal mints, both decimal configurations, shares land with the caller.
        uint256 rate = stockMarket.exchangeRateCurrent();
        uint256 amount = 1e15;
        uint256 expected = amount * 1e18 / rate;
        vm.prank(user);
        assertEq(router.mintWithMinShares(PSTOCK, amount, expected), expected);
        assertEq(stockMarket.balanceOf(user), expected);

        rate = usdgMarket.exchangeRateCurrent();
        expected = uint256(250_000) * 1e18 / rate;
        vm.prank(user);
        assertEq(router.mintWithMinShares(PUSDG, 250_000, expected), expected);
        assertEq(usdgMarket.balanceOf(user), expected);

        // The confirmed finding: NVDA dust below one share. The minimum (at least 1) stops it on
        // the ORIGINAL delegate; the corrected delegate reverts first. Nothing is taken either way.
        rate = stockMarket.exchangeRateCurrent();
        uint256 dust = rate / 1e18 - 1;
        assertEq(dust * 1e18 / rate, 0);
        uint256 before = IERC20(STOCK).balanceOf(user);
        vm.prank(user);
        if (corrected) {
            vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        } else {
            vm.expectRevert(
                abi.encodeWithSelector(LendingMintRouter.InsufficientShares.selector, 0, 1)
            );
        }
        router.mintWithMinShares(PSTOCK, dust, 1);
        assertEq(IERC20(STOCK).balanceOf(user), before);

        // A minimum above what the deposit can buy is rejected, with a full rollback.
        uint256 sharesBefore = stockMarket.balanceOf(user);
        uint256 possible = uint256(1e15) * 1e18 / stockMarket.exchangeRateCurrent();
        vm.prank(user);
        vm.expectRevert(
            abi.encodeWithSelector(
                LendingMintRouter.InsufficientShares.selector, possible, possible + 1
            )
        );
        router.mintWithMinShares(PSTOCK, 1e15, possible + 1);
        assertEq(stockMarket.balanceOf(user), sharesBefore);

        // Router holds nothing and leaves no allowance.
        assertEq(IERC20(STOCK).balanceOf(address(router)), 0);
        assertEq(IERC20(USDG).balanceOf(address(router)), 0);
        assertEq(stockMarket.balanceOf(address(router)), 0);
        assertEq(usdgMarket.balanceOf(address(router)), 0);
        assertEq(IERC20(STOCK).allowance(address(router), PSTOCK), 0);
        assertEq(IERC20(USDG).allowance(address(router), PUSDG), 0);
    }

    function testRouterOnTheInstalledOriginalDelegate() public {
        _scenarios(false);
    }

    function testRouterWithTheCorrectedDelegateInstalled() public {
        _install();
        _scenarios(true);
    }
}
