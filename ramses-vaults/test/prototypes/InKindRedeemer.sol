// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesVaultAdapter } from "../../src/RamsesVaultAdapter.sol";
import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { InKindExtension } from "./RoutedVault.sol";

/// @notice PHASE 0 PROTOTYPE, TEST-ONLY. Optional mixed-token redemption for DEBT-FREE suppliers, through the REAL market and vault code with real transfers:
///   recognize any loss first (reverts on a stale feed) -> take the pTokens -> compute the holder's pro-rata share of the side's deficit ->
///   FUND IT FROM THE EXIT ITSELF: redeem just enough shares through the real market to obtain that much of the scarce token -> exchange it for the other token's surplus at the
///   fresh oracle price -> redeem the remaining shares -> pay the holder the mixture. No outside money. If the first redeem cannot produce the scarce token at all (none anywhere in
///   the market or vault), an optional `bridge` account may float it for this transaction; with no bridge the whole call reverts and nothing changes.
/// Holds no keeper power: its only vault permission is IN_KIND_OPERATOR_ROLE. An indebted account is refused and keeps the ordinary redeem with the controller's collateral check.
/// The holder is paid a MIXTURE and bears the other token's price risk.
contract InKindRedeemer {
    using SafeERC20 for IERC20;

    RamsesBoostedVault public immutable vault;
    bytes32 public immutable pairId;
    RamsesBoostedDelegate public immutable stockMarket;
    RamsesBoostedDelegate public immutable usdgMarket;
    address public immutable bridge;

    uint256 private _entered;

    error ReentrancyGuardReentrantCall();
    error HasDebt();
    error NoFundingSource(uint256 needed, uint256 obtained);
    error Slippage(uint256 ownOut, uint256 otherOut);

    event RedeemedInKind(address indexed account, bool usdgSide, uint256 shares, uint256 ownOut, uint256 otherOut, uint256 exchanged, uint256 bridged);

    modifier nonReentrant() {
        if (_entered == 1) revert ReentrancyGuardReentrantCall();
        _entered = 1;
        _;
        _entered = 0;
    }

    constructor(RamsesBoostedVault v, bytes32 id, RamsesBoostedDelegate sm, RamsesBoostedDelegate um, address bridge_) {
        vault = v;
        pairId = id;
        stockMarket = sm;
        usdgMarket = um;
        bridge = bridge_;
    }

    function redeemInKind(bool usdgSide, uint256 shares, uint256 minOwn, uint256 minOther, uint256 deadline)
        external
        nonReentrant
        returns (uint256 ownOut, uint256 otherOut)
    {
        if (stockMarket.borrowBalanceCurrent(msg.sender) != 0 || usdgMarket.borrowBalanceCurrent(msg.sender) != 0) revert HasDebt();
        InKindExtension x = InKindExtension(address(vault));
        x.inKindCheckpoint(pairId, deadline);
        RamsesBoostedDelegate market = usdgSide ? usdgMarket : stockMarket;
        PairConfig memory c = vault.pairConfig(pairId);
        IERC20 own = IERC20(usdgSide ? c.usdg : c.stockToken);
        IERC20 other = IERC20(usdgSide ? c.stockToken : c.usdg);
        require(market.transferFrom(msg.sender, address(this), shares), "TRANSFER");

        uint256 rate = market.exchangeRateCurrent();
        uint256 value = Math.mulDiv(shares, rate, 1e18);
        uint256 total = Math.mulDiv(market.totalSupply(), rate, 1e18);
        uint256 deficit = _deficit(usdgSide);
        uint256 toExchange = deficit == 0 ? 0 : Math.mulDiv(value, deficit, total, Math.Rounding.Ceil);
        // Two raw units short of the whole value at most, so the floors in the two redeems can still repay a same-transaction float.
        toExchange = Math.min(toExchange, value > 2 ? value - 2 : 0);
        uint256 got;
        uint256 remaining = shares;
        uint256 bridged;
        uint256 start = own.balanceOf(address(this));
        if (toExchange != 0) {
            // Shares whose redemption yields the amount to exchange; rounded up, capped at all the shares.
            uint256 first = Math.min(shares, Math.mulDiv(toExchange, 1e18, rate, Math.Rounding.Ceil));
            try market.redeem(first) returns (uint256 err) {
                if (err == 0) remaining -= first;
            } catch { }
            got = own.balanceOf(address(this)) - start;
            if (got < toExchange) {
                if (bridge == address(0)) revert NoFundingSource(toExchange, got);
                bridged = toExchange - got;
                own.safeTransferFrom(bridge, address(this), bridged);
            }
            own.forceApprove(address(vault), toExchange);
            otherOut = x.exchangeSurplus(pairId, usdgSide, toExchange);
            own.forceApprove(address(vault), 0);
        }
        // When a float was needed there is no other scarce token, so burn only what the float can pay; rounding dust stays with the holder as shares.
        if (bridged != 0) remaining = Math.min(remaining, Math.mulDiv(bridged, 1e18, rate, Math.Rounding.Ceil));
        if (remaining != 0) require(market.redeem(remaining) == 0, "REDEEM");
        uint256 dustShares = market.balanceOf(address(this));
        if (dustShares != 0) require(market.transfer(msg.sender, dustShares), "DUST");
        got = own.balanceOf(address(this)) - start;
        if (bridged != 0) own.safeTransfer(bridge, bridged);
        ownOut = own.balanceOf(address(this));
        if (ownOut < minOwn || otherOut < minOther) revert Slippage(ownOut, otherOut);
        own.safeTransfer(msg.sender, ownOut);
        if (otherOut != 0) other.safeTransfer(msg.sender, otherOut);
        emit RedeemedInKind(msg.sender, usdgSide, shares, ownOut, otherOut, toExchange, bridged);
    }

    function _deficit(bool usdgSide) private view returns (uint256) {
        PairLedger memory l = vault.ledger(pairId);
        uint160 ref = vault.oracleGuard().referenceSqrtPriceX96(pairId, false);
        RamsesVaultAdapter.PositionState memory p = vault.liquidityAdapter().positionStateAt(pairId, ref);
        uint256 assets = usdgSide ? l.usdgIdle + p.usdgAmount : l.stockIdle + p.stockAmount;
        uint256 principal = usdgSide ? l.usdgPrincipal : l.stockPrincipal;
        return principal > assets ? principal - assets : 0;
    }
}
