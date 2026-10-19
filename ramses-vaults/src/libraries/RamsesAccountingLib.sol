// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { SafeCast } from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IRamsesPool } from "../interfaces/IRamsesV3.sol";
import { RamsesOracleGuard } from "../RamsesOracleGuard.sol";
import { RamsesVaultAdapter } from "../RamsesVaultAdapter.sol";
import { RamsesV3PairedAdapter } from "../RamsesV3PairedAdapter.sol";

/// @notice V3 loss waterfall: oracle valuation, pro-rata loss, gains only with both sides covered.
library RamsesAccountingLib {
    using SafeCast for uint256;

    struct Ctx {
        bytes32 id;
        RamsesVaultAdapter adapter;
        uint256 stockPrice;
        uint256 usdgPrice;
        uint160 ref;
    }
    event PairCheckpoint(
        bytes32 indexed pairId,
        uint256 stockAssets,
        uint256 usdgAssets,
        uint256 benchmarkUSDG,
        int256 pnlUSDG,
        uint256 stockAccountedAssets,
        uint256 usdgAccountedAssets
    );

    function checkpoint(
        PairConfig storage c,
        PairLedger storage l,
        mapping(address => uint256) storage aggregate,
        Ctx memory p,
        bool gains
    ) external returns (int256 pnl) {
        RamsesV3PairedAdapter.PositionState memory position = p.adapter.positionStateAt(p.id, p.ref);
        uint256 stockAssets = l.stockIdle + position.stockAmount;
        uint256 usdgAssets = l.usdgIdle + position.usdgAmount;
        uint256 benchmark = value(c, p, l.stockPrincipal, l.usdgPrincipal);
        uint256 gross = value(c, p, stockAssets, usdgAssets);
        if (gross < benchmark && benchmark != 0) {
            uint256 loss = benchmark - gross;
            uint256 oldUsdg = l.usdgPrincipal;
            l.stockPrincipal = Math.mulDiv(l.stockPrincipal, gross, benchmark);
            l.usdgPrincipal = Math.mulDiv(l.usdgPrincipal, gross, benchmark);
            aggregate[c.usdg] -= oldUsdg - l.usdgPrincipal;
            l.cumulativeLossUSDG += loss;
            pnl = -loss.toInt256();
        } else if (!gains) {
            return 0;
        } else if (stockAssets >= l.stockPrincipal && usdgAssets >= l.usdgPrincipal) {
            uint256 stockGain = stockAssets - l.stockPrincipal;
            uint256 usdgGain = usdgAssets - l.usdgPrincipal;
            l.stockPrincipal += stockGain;
            l.usdgPrincipal += usdgGain;
            aggregate[c.usdg] += usdgGain;
            pnl = value(c, p, stockGain, usdgGain).toInt256();
        }
        l.lastCheckpoint = uint64(block.timestamp);
        emit PairCheckpoint(
            p.id, stockAssets, usdgAssets, benchmark, pnl, l.stockPrincipal, l.usdgPrincipal
        );
    }

    /// @notice Allocation cap includes unrecognized composition surplus, not just cached claims.
    function allocationCapExceeded(PairConfig storage c, PairLedger storage l, Ctx memory p)
        external
        view
        returns (bool)
    {
        if (c.maxPairValueUSDG == 0) return false;
        RamsesV3PairedAdapter.PositionState memory position = p.adapter.positionStateAt(p.id, p.ref);
        uint256 assets =
            value(c, p, l.stockIdle + position.stockAmount, l.usdgIdle + position.usdgAmount);
        uint256 claims = value(c, p, l.stockPrincipal, l.usdgPrincipal);
        return Math.max(assets, claims) > c.maxPairValueUSDG;
    }

    function value(PairConfig storage c, Ctx memory p, uint256 s, uint256 u)
        private
        view
        returns (uint256)
    {
        return VaultMath.valueUSD18(s, c.stockDecimals, p.stockPrice, Math.Rounding.Floor)
            + VaultMath.valueUSD18(u, c.usdgDecimals, p.usdgPrice, Math.Rounding.Floor);
    }

    function withdrawable(
        PairConfig storage c,
        PairLedger storage l,
        RamsesVaultAdapter liquidityAdapter,
        RamsesOracleGuard oracleGuard,
        bytes32 pairId,
        uint256 idle,
        uint256 principal
    ) external view returns (uint256) {
        uint256 available = Math.min(idle, principal);
        if (available == 0) return 0;

        try liquidityAdapter.positionState(pairId) returns (
            RamsesV3PairedAdapter.PositionState memory position
        ) {
            if (
                position.liquidity == 0
                    && (l.stockIdle >= l.stockPrincipal && l.usdgIdle >= l.usdgPrincipal)
            ) return available;
        } catch {
            return 0;
        }

        if (c.emergencyMode) return 0;
        // The pool key is read inside its own try: evaluated as an argument to the guarded
        // call below, an adapter-side revert would escape and make this view revert rather
        // than report nothing withdrawable.
        IRamsesPool key;
        try liquidityAdapter.poolOf(pairId) returns (IRamsesPool poolKey_) {
            key = poolKey_;
        } catch {
            return 0;
        }
        try oracleGuard.validateRemovalPrice(pairId, key) returns (uint256, uint256, uint160) {
            return available;
        } catch {
            return 0;
        }
    }
}
