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
///   checkpoint (recognizes any loss first; reverts on a stale feed) -> take the pTokens -> compute the holder's pro-rata share of the side's deficit ->
///   borrow that amount of the scarce token from `bridge` for the length of this transaction -> exchange it for the other token's surplus at the fresh oracle price ->
///   `market.redeem` (the market burns the shares and pays the scarce token under its own rules) -> repay the bridge -> pay the holder the mixture.
/// The market contract is untouched. An indebted account is refused here and keeps the ordinary redeem with the controller's collateral check.
/// The holder is paid a MIXTURE and bears the other token's price risk. The prototype needs KEEPER_ROLE only to call `checkpoint`; production would do that internally.
contract InKindRedeemer {
    using SafeERC20 for IERC20;

    RamsesBoostedVault public immutable vault;
    bytes32 public immutable pairId;
    RamsesBoostedDelegate public immutable stockMarket;
    RamsesBoostedDelegate public immutable usdgMarket;
    address public immutable bridge;

    error HasDebt();
    error Slippage(uint256 ownOut, uint256 otherOut);

    event RedeemedInKind(address indexed account, bool usdgSide, uint256 shares, uint256 ownOut, uint256 otherOut, uint256 bridged);

    constructor(RamsesBoostedVault v, bytes32 id, RamsesBoostedDelegate sm, RamsesBoostedDelegate um, address bridge_) {
        vault = v;
        pairId = id;
        stockMarket = sm;
        usdgMarket = um;
        bridge = bridge_;
    }

    function redeemInKind(bool usdgSide, uint256 shares, uint256 minOwn, uint256 minOther, uint256 deadline)
        external
        returns (uint256 ownOut, uint256 otherOut)
    {
        if (stockMarket.borrowBalanceCurrent(msg.sender) != 0 || usdgMarket.borrowBalanceCurrent(msg.sender) != 0) revert HasDebt();
        vault.checkpoint(pairId, deadline);
        RamsesBoostedDelegate market = usdgSide ? usdgMarket : stockMarket;
        PairConfig memory c = vault.pairConfig(pairId);
        IERC20 own = IERC20(usdgSide ? c.usdg : c.stockToken);
        IERC20 other = IERC20(usdgSide ? c.stockToken : c.usdg);
        require(market.transferFrom(msg.sender, address(this), shares), "TRANSFER");

        uint256 rate = market.exchangeRateCurrent();
        uint256 value = Math.mulDiv(shares, rate, 1e18);
        uint256 total = Math.mulDiv(market.totalSupply(), rate, 1e18);
        uint256 deficit = _deficit(c, usdgSide);
        uint256 bridged = deficit == 0 ? 0 : Math.mulDiv(value, deficit, total, Math.Rounding.Ceil);
        if (bridged != 0) {
            own.safeTransferFrom(bridge, address(this), bridged);
            own.forceApprove(address(vault), bridged);
            otherOut = InKindExtension(address(vault)).exchangeSurplus(pairId, usdgSide, bridged);
            own.forceApprove(address(vault), 0);
        }
        uint256 before_ = own.balanceOf(address(this));
        require(market.redeem(shares) == 0, "REDEEM");
        uint256 got = own.balanceOf(address(this)) - before_;
        if (bridged != 0) own.safeTransfer(bridge, bridged);
        ownOut = got - bridged;
        if (ownOut < minOwn || otherOut < minOther) revert Slippage(ownOut, otherOut);
        own.safeTransfer(msg.sender, ownOut);
        if (otherOut != 0) other.safeTransfer(msg.sender, otherOut);
        emit RedeemedInKind(msg.sender, usdgSide, shares, ownOut, otherOut, bridged);
    }

    function _deficit(PairConfig memory c, bool usdgSide) private view returns (uint256) {
        PairLedger memory l = vault.ledger(pairId);
        uint160 ref = vault.oracleGuard().referenceSqrtPriceX96(pairId, false);
        RamsesVaultAdapter.PositionState memory p = vault.liquidityAdapter().positionStateAt(pairId, ref);
        c; // decimals are not needed: both sides of the comparison are the same token
        uint256 assets = usdgSide ? l.usdgIdle + p.usdgAmount : l.stockIdle + p.stockAmount;
        uint256 principal = usdgSide ? l.usdgPrincipal : l.stockPrincipal;
        return principal > assets ? principal - assets : 0;
    }
}
