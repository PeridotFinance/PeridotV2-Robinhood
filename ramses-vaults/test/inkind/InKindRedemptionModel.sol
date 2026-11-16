// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";

interface IDebtView {
    /// @return true if the account owes anything in either market of the pair
    function hasDebt(address account) external view returns (bool);
}

/// @notice PHASE 0 PROTOTYPE, TEST-ONLY. An isolated ACCOUNTING model of an optional mixed-token ("in-kind") emergency redemption for DEBT-FREE suppliers.
/// It moves no tokens and is not connected to any vault: the real vault pays a single token per side, and releasing the other side's surplus needs a new
/// vault entry point and pToken-delegate changes (production integration, listed in the README). What it fixes is the RULE and its fairness:
///
///  * A supplier of side X redeeming `claim` of principal receives X's token in the proportion the pair can actually pay it (assets/principal) and the rest
///    of the claim in the OTHER token at the FRESH oracle price (RamsesOracleGuard.pricesUSD18 reverts on a stale feed: a stale price is never used to make
///    an exit pass). Everyone redeeming gets the same mix whatever the order (no first-mover advantage, no run on the scarce token).
///  * The other token comes only from that side's SURPLUS (pair assets above that side's principal). After a recognized shared loss the surplus value equals
///    the scarce side's deficit, so nobody else is charged: remaining holders keep the same assets/principal ratio and the same share of the deficit.
///  * Rounding always favours the remaining holders. No reserve is read or used.
///  * The recipient is paid a mixture, not the originally deposited asset, and bears price risk on the other token afterwards.
/// Pair assets (idle + the position at the oracle reference price) are an input: unwinding the LP into idle balances is the vault's existing job.
contract InKindRedemptionModel {
    struct State {
        uint256 stockPrincipal;
        uint256 usdgPrincipal;
        uint256 stockAssets;
        uint256 usdgAssets;
    }

    RamsesOracleGuard public immutable guard;
    bytes32 public immutable pairId;
    IDebtView public immutable debt;
    State public s;

    error HasDebt();
    error NothingToRedeem();
    error InsufficientSurplus();
    error ClaimExceedsPrincipal();

    event RedeemedInKind(
        address indexed account, bool usdgSide, uint256 claim, uint256 usdgOut, uint256 stockOut
    );

    constructor(RamsesOracleGuard g, bytes32 id, IDebtView d) {
        guard = g;
        pairId = id;
        debt = d;
    }

    function load(State calldata st) external {
        s = st;
    }

    /// @dev Quote only: the same arithmetic as `redeemInKind` without state change.
    function quote(bool usdgSide, uint256 claim)
        public
        view
        returns (uint256 usdgOut, uint256 stockOut)
    {
        (uint256 ps,) = guard.pricesUSD18(pairId); // reverts on a stale feed
        State memory t = s;
        if (claim == 0) revert NothingToRedeem();
        if (usdgSide) {
            if (claim > t.usdgPrincipal) revert ClaimExceedsPrincipal();
            if (t.usdgAssets >= t.usdgPrincipal) return (claim, 0);
            usdgOut = Math.mulDiv(claim, t.usdgAssets, t.usdgPrincipal);
            uint256 restUsd18 = (claim - usdgOut) * 1e12;
            stockOut = Math.mulDiv(restUsd18, 1e18, ps); // floor: the remaining holders keep the dust
            if (stockOut > t.stockAssets - Math.min(t.stockAssets, t.stockPrincipal)) {
                revert InsufficientSurplus();
            }
        } else {
            if (claim > t.stockPrincipal) revert ClaimExceedsPrincipal();
            if (t.stockAssets >= t.stockPrincipal) return (0, claim);
            stockOut = Math.mulDiv(claim, t.stockAssets, t.stockPrincipal);
            uint256 restUsd18 = Math.mulDiv(claim - stockOut, ps, 1e18);
            usdgOut = restUsd18 / 1e12;
            if (usdgOut > t.usdgAssets - Math.min(t.usdgAssets, t.usdgPrincipal)) {
                revert InsufficientSurplus();
            }
        }
    }

    function redeemInKind(address account, bool usdgSide, uint256 claim)
        external
        returns (uint256 usdgOut, uint256 stockOut)
    {
        if (debt.hasDebt(account)) revert HasDebt();
        (usdgOut, stockOut) = quote(usdgSide, claim);
        if (usdgSide) {
            s.usdgPrincipal -= claim;
        } else {
            s.stockPrincipal -= claim;
        }
        s.usdgAssets -= usdgOut;
        s.stockAssets -= stockOut;
        emit RedeemedInKind(account, usdgSide, claim, usdgOut, stockOut);
    }
}
