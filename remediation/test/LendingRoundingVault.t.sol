// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { MockERC20 } from "baseline/test/mocks/MockERC20.sol";
import { Peridottroller } from "peridot/Peridottroller.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { PToken } from "peridot/PToken.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { IRobinhoodBoostedVault } from "peridot/interfaces/IRobinhoodBoostedVault.sol";
import { RobinhoodBoostedDelegate } from "peridot/boosted/RobinhoodBoostedDelegate.sol";
import { RobinhoodBoostedDelegateV2 } from "../src/RobinhoodBoostedDelegateV2.sol";
import { RoundingZeroRate, RoundingOracle } from "./LendingRounding.t.sol";

/// @dev Stands in for the paired vault, which owns one side per pToken. Withdrawals can realize a
/// loss: the claim shrinks by `returned + loss` while only `returned` tokens come back.
contract MockSideVault is IRobinhoodBoostedVault {
    IERC20 public immutable token;
    address public side;
    uint256 public accounted;
    uint256 public pendingLoss;
    bool public reportsRevert;
    bool public withdrawalsRevert;

    constructor(IERC20 token_) {
        token = token_;
    }

    function setSide(address side_) external {
        side = side_;
    }

    /// Claim appreciation (fees, LP gains), with no token movement needed for these tests'
    /// accounting because `accountedAssets` is what the pToken prices against.
    function setAccounted(uint256 value) external {
        accounted = value;
    }

    function setPendingLoss(uint256 value) external {
        pendingLoss = value;
    }

    function setReportsRevert(bool value) external {
        reportsRevert = value;
    }

    function setWithdrawalsRevert(bool value) external {
        withdrawalsRevert = value;
    }

    function depositForPair(bytes32, address, uint256 amount) external returns (uint256) {
        require(msg.sender == side, "SIDE");
        token.transferFrom(msg.sender, address(this), amount);
        accounted += amount;
        return amount;
    }

    function withdrawForSide(bytes32, address, uint256 requested, address receiver, uint256)
        external
        returns (uint256 returned, uint256 realizedLoss)
    {
        require(msg.sender == side, "SIDE");
        require(!withdrawalsRevert, "WITHDRAW_DOWN");
        returned = requested;
        realizedLoss = pendingLoss;
        pendingLoss = 0;
        require(accounted >= returned + realizedLoss, "CLAIM");
        accounted -= returned + realizedLoss;
        token.transfer(receiver, returned);
    }

    function accountedAssets(bytes32, address) external view returns (uint256) {
        require(!reportsRevert, "VAULT_DOWN");
        return accounted;
    }

    function liquidAssets(bytes32, address) external view returns (uint256) {
        require(!reportsRevert, "VAULT_DOWN");
        return accounted;
    }

    function withdrawableAssets(bytes32, address) external view returns (uint256) {
        require(!reportsRevert, "VAULT_DOWN");
        return accounted;
    }

    function sideAccount(bytes32, address) external view returns (address) {
        return side;
    }
}

/// @notice Vault-backed coverage the unit model cannot give: strict mint valuation, loss
/// recognition inside a redemption, and the interaction with the rounded-up burn.
/// Every scenario is run against the frozen original delegate and the candidate.
contract LendingRoundingVaultTest is Test {
    bytes32 internal constant PAIR = keccak256("MODEL/PAIR");

    address internal victim = makeAddr("victim");
    address internal operator = makeAddr("operator");
    Peridottroller internal controller;
    MockERC20 internal stock;

    struct Env {
        PErc20Delegator market;
        MockSideVault vault;
    }

    function setUp() public {
        controller = new Peridottroller();
        assertEq(controller._setPriceOracle(new RoundingOracle()), 0); // no borrowing: prices unused
        stock = new MockERC20("Stock model", "STOCK", 18);
        stock.mint(victim, 100e18);
    }

    /// @dev A market with the vault configured, unpaused, and a 20% local buffer.
    function _vaultMarket(address delegate) internal returns (Env memory e) {
        RoundingZeroRate rate = new RoundingZeroRate();
        e.market = new PErc20Delegator(
            address(stock),
            PeridottrollerInterface(address(controller)),
            rate,
            2e26,
            "pModel",
            "pMODEL",
            8,
            payable(address(this)),
            delegate,
            ""
        );
        assertEq(controller._supportMarket(PToken(address(e.market))), 0);
        e.vault = new MockSideVault(stock);
        e.vault.setSide(address(e.market));
        e.market
            ._setImplementation(
                delegate, false, abi.encode(address(e.vault), PAIR, 0.2e18, operator)
            );

        RobinhoodBoostedDelegateV2 d = RobinhoodBoostedDelegateV2(address(e.market));
        assertTrue(d.vaultPaused(), "configure leaves the vault paused");
        d.queueSetVaultPaused(false);
        vm.warp(block.timestamp + 1 hours);
        d._setVaultPaused(false);
        assertFalse(d.vaultPaused());

        vm.prank(victim);
        stock.approve(address(e.market), type(uint256).max);
    }

    function _mintInto(Env memory e, uint256 amount) internal {
        vm.prank(victim);
        assertEq(e.market.mint(amount), 0);
    }

    // ---------------------------------------------------------------------
    // Vault wiring sanity (the same scenarios must hold for the original and V2)
    // ---------------------------------------------------------------------

    function _delegates() internal returns (address[2] memory d) {
        d[0] = address(new RobinhoodBoostedDelegate());
        d[1] = address(new RobinhoodBoostedDelegateV2());
    }

    function testMintDepositsOverBufferIntoVaultAndPricesAtInitialRate() public {
        address[2] memory ds = _delegates();
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            assertEq(e.market.balanceOf(victim), 5e10, "10e18 at rate 2e26");
            assertEq(stock.balanceOf(address(e.market)), 2e18, "20% buffer stays local");
            assertEq(e.vault.accounted(), 8e18, "80% moved into the vault claim");
            assertEq(e.market.exchangeRateStored(), 2e26);
        }
    }

    // ---------------------------------------------------------------------
    // Strict mint valuation
    // ---------------------------------------------------------------------

    /// A mint prices new shares against the vault claim, so it must not proceed on a guess.
    function testMintRevertsWhenVaultClaimIsUnavailableOnBothDelegates() public {
        address[2] memory ds = _delegates();
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            e.vault.setReportsRevert(true);
            uint256 victimBefore = stock.balanceOf(victim);
            vm.prank(victim);
            vm.expectRevert("VAULT_DOWN");
            e.market.mint(1e18);
            assertEq(stock.balanceOf(victim), victimBefore, "no underlying taken");
            e.vault.setReportsRevert(false);
            _mintInto(e, 1e18);
        }
    }

    /// Without the claim an unavailable report would price new shares too cheaply and dilute
    /// holders; the strict path forces a revert instead. Redemption stays available at the
    /// conservative (claim-excluded) rate so local cash is never frozen.
    function testRedeemStaysAvailableButConservativeWhenVaultClaimIsUnavailable() public {
        address[2] memory ds = _delegates();
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            e.vault.setReportsRevert(true);
            // Only local cash (2e18) is counted: 5e10 shares are worth 2e18, so the rate is lower.
            assertEq(e.market.exchangeRateStored(), uint256(2e18) * 1e18 / 5e10);
            uint256 before = stock.balanceOf(victim);
            vm.prank(victim);
            assertEq(e.market.redeem(1e10), 0);
            assertEq(
                stock.balanceOf(victim) - before, uint256(2e18) / 5, "paid at the conservative rate"
            );
        }
    }

    function testStrictMintPricesAgainstAppreciatedVaultClaim() public {
        address[2] memory ds = _delegates();
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            e.vault.setAccounted(e.vault.accounted() + 2e18); // claim appreciates 8e18 -> 10e18
            uint256 rate = e.market.exchangeRateStored();
            assertEq(rate, uint256(12e18) * 1e18 / 5e10);
            uint256 sharesBefore = e.market.balanceOf(victim);
            _mintInto(e, 3e18);
            assertEq(e.market.balanceOf(victim) - sharesBefore, uint256(3e18) * 1e18 / rate);
        }
    }

    // ---------------------------------------------------------------------
    // Zero-share mint with a vault-inflated rate
    // ---------------------------------------------------------------------

    function testZeroShareMintViaVaultClaimInflationIsRejectedOnlyByV2() public {
        address[2] memory ds = _delegates();
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 4e8); // two shares
            assertEq(e.market.totalSupply(), 2);
            e.vault.setAccounted(e.vault.accounted() + 1e18); // claim inflation, no token needed here
            uint256 rate = e.market.exchangeRateStored();
            uint256 deposit = 0.1e18;
            assertEq(deposit * 1e18 / rate, 0, "deposit prices to zero shares");
            uint256 victimBefore = stock.balanceOf(victim);
            uint256 sharesBefore = e.market.balanceOf(victim);
            vm.prank(victim);
            if (i == 0) {
                assertEq(e.market.mint(deposit), 0);
                assertEq(
                    e.market.balanceOf(victim),
                    sharesBefore,
                    "original: underlying taken, nothing minted"
                );
                assertEq(stock.balanceOf(victim), victimBefore - deposit);
            } else {
                vm.expectRevert(RobinhoodBoostedDelegateV2.ZeroSharesMinted.selector);
                e.market.mint(deposit);
                assertEq(stock.balanceOf(victim), victimBefore, "V2: atomic rejection");
                assertEq(e.market.balanceOf(victim), sharesBefore);
            }
        }
    }

    // ---------------------------------------------------------------------
    // Loss settled inside a redemption
    // ---------------------------------------------------------------------

    /// Local cash is 2e18, so redeeming 5e18 withdraws 3e18 from the vault, and the vault realizes
    /// a 1e18 loss on that call. The loss is shared by every holder BEFORE shares are priced:
    /// post-settlement the pool holds 5e18 cash + 4e18 claim over 5e10 shares.
    function testExactUnderlyingRedeemSharesVaultLossAtomicallyAndBurnsRoundedUp() public {
        address[2] memory ds = _delegates();
        uint256[2] memory burned;
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            uint256 rateBefore = e.market.exchangeRateStored();
            assertEq(rateBefore, 2e26);
            e.vault.setPendingLoss(1e18);

            uint256 sharesBefore = e.market.balanceOf(victim);
            uint256 underlyingBefore = stock.balanceOf(victim);
            vm.prank(victim);
            assertEq(e.market.redeemUnderlying(5e18), 0);

            assertEq(stock.balanceOf(victim) - underlyingBefore, 5e18, "exact output delivered");
            assertEq(RobinhoodBoostedDelegateV2(address(e.market)).cumulativeVaultLoss(), 1e18);
            assertEq(e.vault.accounted(), 4e18, "claim reduced by returned + loss");
            burned[i] = sharesBefore - e.market.balanceOf(victim);

            // Priced at the POST-loss rate 9e18 * 1e18 / 5e10 = 1.8e26, not the pre-loss 2e26.
            uint256 postLossRate = uint256(9e18) * 1e18 / 5e10;
            assertEq(postLossRate, 1.8e26);
            uint256 floorBurn = uint256(5e18) * 1e18 / postLossRate;
            assertGt(
                burned[i], uint256(5e18) * 1e18 / rateBefore, "loss increases the shares burned"
            );
            if (i == 0) assertEq(burned[i], floorBurn, "original rounds down");
            else assertEq(burned[i], floorBurn + 1, "V2 rounds up");
        }
        assertEq(burned[1], burned[0] + 1, "the only difference is the rounding direction");
    }

    function testShareDenominatedRedeemPaysPostLossValueIdenticallyOnBothDelegates() public {
        address[2] memory ds = _delegates();
        uint256[2] memory paid;
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            e.vault.setPendingLoss(1e18);
            uint256 underlyingBefore = stock.balanceOf(victim);
            // 3e10 shares at the pre-loss rate quote 6e18; local cash 2e18 forces a vault pull.
            vm.prank(victim);
            assertEq(e.market.redeem(3e10), 0);
            paid[i] = stock.balanceOf(victim) - underlyingBefore;
            assertLt(paid[i], 6e18, "loss is shared before payout");
            assertGt(paid[i], 0);
        }
        assertEq(paid[0], paid[1], "share-denominated redemption is unchanged by V2");
    }

    /// A failing vault withdrawal must not let a short exact redemption through at the old rate.
    function testExactUnderlyingRedeemRevertsWhenVaultCannotSupplyLiquidity() public {
        address[2] memory ds = _delegates();
        for (uint256 i; i < 2; i++) {
            Env memory e = _vaultMarket(ds[i]);
            _mintInto(e, 10e18);
            e.vault.setWithdrawalsRevert(true);
            uint256 sharesBefore = e.market.balanceOf(victim);
            vm.prank(victim);
            vm.expectRevert(abi.encodeWithSignature("RedeemTransferOutNotPossible()"));
            e.market.redeemUnderlying(5e18);
            assertEq(e.market.balanceOf(victim), sharesBefore);
            assertEq(e.vault.accounted(), 8e18);
        }
    }

    function testFuzzV2ExactRedeemWithVaultLossNeverBurnsTooFewShares(
        uint256 deposit,
        uint256 loss,
        uint256 want
    ) public {
        deposit = bound(deposit, 1e12, 50e18);
        Env memory e = _vaultMarket(address(new RobinhoodBoostedDelegateV2()));
        _mintInto(e, deposit);
        uint256 vaultClaim = e.vault.accounted();
        loss = bound(loss, 0, vaultClaim / 4);
        // Request more than local cash so a vault pull (and the loss) happens, within the claim.
        uint256 local = stock.balanceOf(address(e.market));
        want = bound(want, local + 1, local + vaultClaim / 2);
        e.vault.setPendingLoss(loss);

        uint256 supplyBefore = e.market.totalSupply();
        uint256 sharesBefore = e.market.balanceOf(victim);
        uint256 underlyingBefore = stock.balanceOf(victim);
        vm.prank(victim);
        (bool ok,) =
            address(e.market).call(abi.encodeWithSignature("redeemUnderlying(uint256)", want));
        assertTrue(ok, "exact redeem within the claim and loss bounds must not revert");
        uint256 burned = sharesBefore - e.market.balanceOf(victim);
        assertEq(stock.balanceOf(victim) - underlyingBefore, want);
        // Settled rate: (cash after the pull + remaining claim) / supply before burning.
        uint256 settledRate =
            (stock.balanceOf(address(e.market)) + want + e.vault.accounted()) * 1e18 / supplyBefore;
        // After the transfer out, pool cash dropped by `want`, so add it back to recover the settled pool.
        assertGe(
            burned * settledRate, want * 1e18, "never fewer shares than the exact output is worth"
        );
        assertLt(
            (burned - 1) * settledRate, want * 1e18, "and at most one share more than necessary"
        );
    }
}
