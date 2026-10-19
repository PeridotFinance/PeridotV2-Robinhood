// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {RamsesBoostedVault} from "../RamsesBoostedVault.sol";
import {RamsesOracleGuard} from "../RamsesOracleGuard.sol";
import {RamsesV3PairedAdapter} from "../RamsesV3PairedAdapter.sol";
import {PairConfig, PairLedger} from "baseline/src/libraries/VaultTypes.sol";

/// @notice Conservative live loss preview for controller snapshots, including the OTHER market.
/// Never recognizes gains before the vault does; ignores uncollected fees and reserve cover.
library RamsesMarketAccounting {
    function stale(bytes memory reason) private pure returns (bool) {
        return reason.length >= 4 && bytes4(reason) == RamsesOracleGuard.StaleOracle.selector;
    }

    function checkpoint(address target, bytes32 pair, bool allowStale) external {
        RamsesBoostedVault v = RamsesBoostedVault(target);
        PairConfig memory c = v.pairConfig(pair);
        PairLedger memory l = v.ledger(pair);
        // Emergency decrease already books losses. Closed, natively backed claims need no oracle.
        if (v.liquidityAdapter().positionState(pair).liquidity == 0 && l.stockIdle >= l.stockPrincipal && l.usdgIdle >= l.usdgPrincipal) return;
        if (c.emergencyMode) revert RamsesBoostedVault.EmergencyMode();
        try v.checkpoint(pair, block.timestamp) {} catch (bytes memory reason) {
            if (!allowStale || !stale(reason)) assembly ("memory-safe") { revert(add(reason,32), mload(reason)) }
        }
    }

    function assets(address target, bytes32 pair, address token) external view returns (uint256) {
        RamsesBoostedVault v = RamsesBoostedVault(target);
        PairConfig memory c = v.pairConfig(pair);
        PairLedger memory l = v.ledger(pair);
        uint256 principal = v.accountedAssets(pair, token);
        if (l.stockPrincipal == 0 && l.usdgPrincipal == 0) return principal;
        if (v.liquidityAdapter().positionState(pair).liquidity == 0 && l.stockIdle >= l.stockPrincipal && l.usdgIdle >= l.usdgPrincipal) return principal;
        uint256 price;
        try v.oracleGuard().pricesUSD18(pair) returns (uint256 p, uint256) { price = p; }
        catch (bytes memory reason) {
            // Explicit availability boundary: stale, unencumbered cash exits use the last booked claim.
            // No other failure is converted into a zero or cached valuation.
            if (stale(reason)) return principal;
            assembly ("memory-safe") { revert(add(reason,32), mload(reason)) }
        }
        uint160 ref = v.oracleGuard().referenceSqrtPriceX96(pair, false);
        RamsesV3PairedAdapter.PositionState memory p = v.liquidityAdapter().positionStateAt(pair, ref);
        uint256 benchmark = Math.mulDiv(l.stockPrincipal, price, 10 ** c.stockDecimals) + l.usdgPrincipal * 1e12;
        uint256 gross = Math.mulDiv(l.stockIdle + p.stockAmount, price, 10 ** c.stockDecimals) + (l.usdgIdle + p.usdgAmount) * 1e12;
        return gross < benchmark ? Math.mulDiv(principal, gross, benchmark) : principal;
    }
}
