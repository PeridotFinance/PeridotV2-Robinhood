// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { RobinhoodBoostedDelegateV2 } from "../../src/RobinhoodBoostedDelegateV2.sol";
import { PToken } from "peridot/PToken.sol";
import {
    DeployLendingDelegate,
    InstallLendingDelegate
} from "../../script/UpgradeLendingDelegate.s.sol";

interface IUpgradeController {
    function enterMarkets(address[] calldata) external returns (uint256[] memory);
}

/// @notice Candidate installation on a local fork only, against existing vault and markets.
/// No oracle mocks, injected balances, mainnet transactions or governor keys.
contract LendingDelegateUpgradeMainnetForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        for (uint256 i; i < 2; i++) {
            PErc20Delegator m = PErc20Delegator(payable(i == 0 ? PSTOCK : PUSDG));
            assertEq(
                m.implementation().codehash,
                0xa6913bd52087e56926b3f17fd131b7f331af194aaa77321e452582e75fb8cc34
            );
            assertEq(m.admin(), GOVERNOR);
        }
    }

    /// @dev Every PToken delegate constructor creates its BorrowAccountingModule (an immutable).
    /// A contract's first CREATE uses nonce 1, so the module address is predictable for both the
    /// installed implementation and the candidate.
    function _module(address implementation) internal view returns (address) {
        return vm.computeCreateAddress(implementation, 1);
    }

    /// @dev Counts occurrences of one 32-byte word in contract code.
    function _countWord(bytes memory code, bytes32 word) internal pure returns (uint256 n) {
        if (code.length < 32) return 0;
        for (uint256 i; i + 32 <= code.length; i++) {
            bytes32 chunk;
            assembly ("memory-safe") {
                chunk := mload(add(add(code, 0x20), i))
            }
            if (chunk == word) n++;
        }
    }

    function _upgrade() internal returns (RobinhoodBoostedDelegateV2 candidate) {
        candidate = new RobinhoodBoostedDelegateV2();
        assertLe(address(candidate).code.length, 24576, "Candidate not deployable under EIP-170");
        address module = _module(address(candidate));
        assertGt(module.code.length, 0, "Candidate module not deployed");
        // The delegate's single immutable (the module) is inlined at four code positions.
        assertEq(
            _countWord(address(candidate).code, bytes32(uint256(uint160(module)))),
            4,
            "Immutable not set"
        );
        // Same structure as the installed original, whose module is also its first CREATE.
        address installed = PErc20Delegator(payable(PSTOCK)).implementation();
        address installedModule = _module(installed);
        assertEq(
            _countWord(installed.code, bytes32(uint256(uint160(installedModule)))),
            4,
            "Installed immutable shape"
        );
        assertGt(installedModule.code.length, 0);
        assertTrue(module != installedModule, "Candidate must use its own module");
        for (uint256 i; i < 2; i++) {
            address target = i == 0 ? PSTOCK : PUSDG;
            PErc20Delegator m = PErc20Delegator(payable(target));
            bytes32[29] memory beforeSlots;
            for (uint256 slot; slot < 29; slot++) {
                beforeSlots[slot] = vm.load(target, bytes32(slot));
            }
            bytes32 beforeViews = _views(m);
            vm.prank(GOVERNOR);
            m._setImplementation(address(candidate), false, "");
            for (uint256 slot; slot < 29; slot++) {
                if (slot != 20) {
                    assertEq(vm.load(target, bytes32(slot)), beforeSlots[slot], "Storage changed");
                }
            }
            assertEq(m.implementation(), address(candidate));
            assertEq(_views(m), beforeViews, "Upgrade changed economic state");
        }
    }

    function _views(PErc20Delegator m) internal view returns (bytes32) {
        RobinhoodBoostedDelegateV2 d = RobinhoodBoostedDelegateV2(address(m));
        return keccak256(
            abi.encode(
                m.underlying(),
                m.admin(),
                m.balanceOf(GOVERNOR),
                m.borrowBalanceStored(GOVERNOR),
                m.totalBorrows(),
                m.totalReserves(),
                m.totalSupply(),
                m.exchangeRateStored(),
                m.allowance(GOVERNOR, address(this)),
                d.vaultAccountedAssets(),
                d.vaultLiquidAssets(),
                d.vaultPaused(),
                d.vaultBufferMantissa(),
                d.robinhoodPairId(),
                address(d.robinhoodVault())
            )
        );
    }

    function testUpgradePreservesInstalledStorageAndViews() public {
        _upgrade();
    }

    /// The delegate constructor deploys its own module; both markets keep working through it.
    function testBothMarketsReferenceTheCandidateModuleAfterUpgrade() public {
        RobinhoodBoostedDelegateV2 candidate = _upgrade();
        address module = _module(address(candidate));
        assertEq(PErc20Delegator(payable(PSTOCK)).implementation(), address(candidate));
        assertEq(PErc20Delegator(payable(PUSDG)).implementation(), address(candidate));
        assertGt(module.code.length, 0);
    }

    function testUpgradePreservesOutstandingBorrowAndAllowsRepayment() public {
        address[] memory entered = new address[](1);
        entered[0] = PSTOCK;
        vm.startPrank(GOVERNOR);
        assertEq(IUpgradeController(CONTROLLER).enterMarkets(entered)[0], 0);
        assertEq(PErc20Delegator(payable(PUSDG)).borrow(50_000), 0);
        vm.stopPrank();
        uint256 debt = PErc20Delegator(payable(PUSDG)).borrowBalanceStored(GOVERNOR);
        assertGt(debt, 0);
        _upgrade();
        assertEq(PErc20Delegator(payable(PUSDG)).borrowBalanceStored(GOVERNOR), debt);
        vm.startPrank(GOVERNOR);
        IERC20(USDG).approve(PUSDG, debt);
        assertEq(PErc20Delegator(payable(PUSDG)).repayBorrow(debt), 0);
        vm.stopPrank();
        assertEq(PErc20Delegator(payable(PUSDG)).borrowBalanceStored(GOVERNOR), 0);
    }

    function testLegacyMintStillWorksThroughBothInstalledProxiesAndRoundTrips() public {
        _upgrade();
        for (uint256 i; i < 2; i++) {
            address asset = i == 0 ? STOCK : USDG;
            address market = i == 0 ? PSTOCK : PUSDG;
            uint256 amount = i == 0 ? 1e15 : 10_000;
            PErc20Delegator m = PErc20Delegator(payable(market));
            uint256 rate = m.exchangeRateCurrent();
            uint256 expected = amount * 1e18 / rate;
            assertGt(expected, 0);
            uint256 beforeShares = m.balanceOf(GOVERNOR);
            vm.startPrank(GOVERNOR);
            IERC20(asset).approve(market, amount);
            assertEq(m.mint(amount), 0);
            vm.stopPrank();
            uint256 received = m.balanceOf(GOVERNOR) - beforeShares;
            assertEq(received, expected, "legacy mint credits the floor share count");
            vm.prank(GOVERNOR);
            assertEq(m.redeem(received), 0);
            assertEq(m.balanceOf(GOVERNOR), beforeShares);
        }
    }

    /// The confirmed finding: NVDA dust that transfers underlying and mints nothing on the original.
    function testZeroShareNvdaMintIsRejectedAtomicallyAfterUpgrade() public {
        _upgrade();
        PErc20Delegator m = PErc20Delegator(payable(PSTOCK));
        uint256 rate = m.exchangeRateCurrent();
        uint256 dust = rate / 1e18 - 1; // one raw unit below the first share
        assertGt(dust, 0);
        assertEq(dust * 1e18 / rate, 0, "this amount prices to zero shares");
        uint256 underlyingBefore = IERC20(STOCK).balanceOf(GOVERNOR);
        uint256 cashBefore = IERC20(STOCK).balanceOf(PSTOCK);
        uint256 sharesBefore = m.balanceOf(GOVERNOR);
        uint256 supplyBefore = m.totalSupply();
        vm.startPrank(GOVERNOR);
        IERC20(STOCK).approve(PSTOCK, dust);
        vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
        m.mint(dust);
        vm.stopPrank();
        assertEq(IERC20(STOCK).balanceOf(GOVERNOR), underlyingBefore);
        assertEq(IERC20(STOCK).balanceOf(PSTOCK), cashBefore);
        assertEq(m.balanceOf(GOVERNOR), sharesBefore);
        assertEq(m.totalSupply(), supplyBefore);
    }

    /// USDG has 6 decimals: the smallest unit still mints shares, so it must stay accepted.
    function testSmallestUsdgUnitStillMintsSharesAfterUpgrade() public {
        _upgrade();
        PErc20Delegator m = PErc20Delegator(payable(PUSDG));
        uint256 rate = m.exchangeRateCurrent();
        uint256 expected = uint256(1) * 1e18 / rate;
        assertGt(expected, 0, "one raw USDG unit prices above zero shares");
        uint256 sharesBefore = m.balanceOf(GOVERNOR);
        vm.startPrank(GOVERNOR);
        IERC20(USDG).approve(PUSDG, 1);
        assertEq(m.mint(1), 0);
        vm.stopPrank();
        assertEq(m.balanceOf(GOVERNOR) - sharesBefore, expected);
    }

    function testDroppedMinSharesSelectorIsNotExposedOnInstalledProxies() public {
        _upgrade();
        for (uint256 i; i < 2; i++) {
            address market = i == 0 ? PSTOCK : PUSDG;
            (bool ok,) =
                market.call(abi.encodeWithSignature("mintWithMinShares(uint256,uint256)", 1, 1));
            assertFalse(ok);
        }
    }

    function testBorrowAfterUpgradeUsesTheNewModuleAndCanBeRepaid() public {
        _upgrade();
        address[] memory entered = new address[](1);
        entered[0] = PSTOCK;
        vm.startPrank(GOVERNOR);
        assertEq(IUpgradeController(CONTROLLER).enterMarkets(entered)[0], 0);
        PErc20Delegator usdg = PErc20Delegator(payable(PUSDG));
        uint256 debtBefore = usdg.borrowBalanceStored(GOVERNOR);
        assertEq(usdg.borrow(50_000), 0);
        uint256 debt = usdg.borrowBalanceStored(GOVERNOR);
        assertGe(debt, debtBefore + 50_000 - 1);
        IERC20(USDG).approve(PUSDG, debt);
        assertEq(usdg.repayBorrow(debt), 0);
        vm.stopPrank();
        assertEq(usdg.borrowBalanceStored(GOVERNOR), 0);
    }

    function testShareDenominatedRedeemStillPaysTheRoundedDownValue() public {
        _upgrade();
        PErc20Delegator m = PErc20Delegator(payable(PSTOCK));
        uint256 rate = m.exchangeRateCurrent();
        uint256 shares = 12_345;
        uint256 expected = rate * shares / 1e18;
        uint256 before = IERC20(STOCK).balanceOf(GOVERNOR);
        vm.prank(GOVERNOR);
        assertEq(m.redeem(shares), 0);
        assertEq(IERC20(STOCK).balanceOf(GOVERNOR) - before, expected);
    }

    function testExactUnderlyingRedeemBurnsRoundedUpShares() public {
        _upgrade();
        PErc20Delegator m = PErc20Delegator(payable(PSTOCK));
        uint256 rate = m.exchangeRateCurrent();
        uint256 amount = (rate / 1e18) * 3 / 2;
        uint256 beforeShares = m.balanceOf(GOVERNOR);
        uint256 beforeUnderlying = IERC20(STOCK).balanceOf(GOVERNOR);
        vm.prank(GOVERNOR);
        assertEq(m.redeemUnderlying(amount), 0);
        uint256 burned = beforeShares - m.balanceOf(GOVERNOR);
        assertEq(burned, 2);
        assertEq(IERC20(STOCK).balanceOf(GOVERNOR) - beforeUnderlying, amount);
        assertGe(burned * rate, amount * 1e18);
    }

    /// The reviewed two-stage procedure, run exactly as the governor will (broadcast simulated).
    function testDeployAndInstallScriptsUpgradeBothMarketsAndAreRepeatable() public {
        address delegate = new DeployLendingDelegate().run();
        assertLe(delegate.code.length, 24576);
        // In the real procedure this hash comes from verify_lending_delegate_deployment.py.
        vm.setEnv("NEW_LENDING_DELEGATE", vm.toString(delegate));
        vm.setEnv("EXPECTED_LENDING_DELEGATE_CODEHASH", vm.toString(delegate.codehash));
        InstallLendingDelegate installer = new InstallLendingDelegate();
        installer.run();
        assertEq(PErc20Delegator(payable(PSTOCK)).implementation(), delegate);
        assertEq(PErc20Delegator(payable(PUSDG)).implementation(), delegate);
        installer.run(); // already installed: skipped without error
        DeployLendingDelegate redeploy = new DeployLendingDelegate();
        vm.expectRevert("MARKETS_NOT_ON_THE_REVIEWED_ORIGINAL");
        redeploy.run();
    }

    function testInstallRejectsAMismatchedExpectedCodehash() public {
        address delegate = new DeployLendingDelegate().run();
        vm.setEnv("NEW_LENDING_DELEGATE", vm.toString(delegate));
        vm.setEnv("EXPECTED_LENDING_DELEGATE_CODEHASH", vm.toString(bytes32(uint256(1))));
        InstallLendingDelegate installer = new InstallLendingDelegate();
        vm.expectRevert("CANDIDATE_CODE_MISMATCH");
        installer.run();
        assertTrue(PErc20Delegator(payable(PSTOCK)).implementation() != delegate);
    }
}
