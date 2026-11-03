// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { PairConfig } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesVaultAdapter } from "../../src/RamsesVaultAdapter.sol";

/// @notice PHASE 0 PROTOTYPE, TEST-ONLY. Not under `src/`, not deployed, not wired into any production contract.
/// Restriction on NEW risk when the loss reserve does not cover the exposure. It holds ONLY the vault's existing one-way GUARDIAN role: it can move the
/// pair toward paused (allocation and swaps) and can never unpause (`GuardianCannotUnpause`), so it can tighten and cannot loosen. Resuming stays an
/// admin decision.
///
/// Requirement (per token, in USD): reserve available >= requiredBps/10_000 x EXPOSURE, where exposure is the oracle-valued liquidity in the ranged
/// position (the part that can produce a strategy shortfall). The two token requirements differ because a price fall leaves USDG scarce and a price
/// rise leaves the stock scarce (reserve_sizing.py): one requirement per token, from the stress table, not one blended number.
///
/// What this CANNOT do without production integration: stop a single transaction. `enforce()` is permissionless and reactive, so the vault itself would
/// have to consult `allowedAfter(extraExposureUsd)` inside `rebalance`, `recenter` and `depositForPair`, and after every `cover()` call, to make the
/// restriction atomic. Prices that cannot be read (stale oracle) make `enforce()` REVERT instead of pausing, so a stale feed cannot be used to grief
/// the pair into a sticky pause.
contract CoverageGuard {
    RamsesBoostedVault public immutable vault;
    RamsesLossReserve public immutable reserve;
    RamsesOracleGuard public immutable oracleGuard;
    RamsesVaultAdapter public immutable adapter;
    bytes32 public immutable pairId;
    uint16 public immutable requiredUsdgBps;
    uint16 public immutable requiredStockBps;

    event CoverageShort(bytes32 indexed pairId, uint256 exposureUsd, uint256 usdgCoverageUsd, uint256 requiredUsdgUsd, uint256 stockCoverageUsd, uint256 requiredStockUsd);

    constructor(
        RamsesBoostedVault v,
        RamsesLossReserve r,
        RamsesOracleGuard g,
        RamsesVaultAdapter a,
        bytes32 id,
        uint16 usdgBps,
        uint16 stockBps
    ) {
        vault = v;
        reserve = r;
        oracleGuard = g;
        adapter = a;
        pairId = id;
        requiredUsdgBps = usdgBps;
        requiredStockBps = stockBps;
    }

    /// @return exposureUsd oracle-valued liquidity in the ranged position, USD 1e18
    /// @return usdgCoverageUsd reserve USDG, USD 1e18
    /// @return stockCoverageUsd reserve stock token valued at the oracle price, USD 1e18
    function snapshot() public view returns (uint256 exposureUsd, uint256 usdgCoverageUsd, uint256 stockCoverageUsd) {
        PairConfig memory c = vault.pairConfig(pairId);
        (uint256 sp, uint256 up) = oracleGuard.pricesUSD18(pairId); // reverts on a stale feed: no estimate is ever used
        uint160 ref = oracleGuard.referenceSqrtPriceX96(pairId, false);
        RamsesVaultAdapter.PositionState memory p = adapter.positionStateAt(pairId, ref);
        exposureUsd = VaultMath.valueUSD18(p.stockAmount, c.stockDecimals, sp, Math.Rounding.Floor)
            + VaultMath.valueUSD18(p.usdgAmount, c.usdgDecimals, up, Math.Rounding.Floor);
        usdgCoverageUsd = VaultMath.valueUSD18(reserve.available(pairId, c.usdg), c.usdgDecimals, up, Math.Rounding.Floor);
        stockCoverageUsd = VaultMath.valueUSD18(reserve.available(pairId, c.stockToken), c.stockDecimals, sp, Math.Rounding.Floor);
    }

    function required(uint256 exposureUsd) public view returns (uint256 usdgUsd, uint256 stockUsd) {
        usdgUsd = Math.mulDiv(exposureUsd, requiredUsdgBps, 10_000, Math.Rounding.Ceil);
        stockUsd = Math.mulDiv(exposureUsd, requiredStockBps, 10_000, Math.Rounding.Ceil);
    }

    /// @notice Would the reserve cover the exposure if `extraExposureUsd` of NEW liquidity were added?
    function allowedAfter(uint256 extraExposureUsd) public view returns (bool) {
        (uint256 exposure, uint256 u, uint256 s) = snapshot();
        (uint256 ru, uint256 rs) = required(exposure + extraExposureUsd);
        return u >= ru && s >= rs;
    }

    /// @notice How much MORE liquidity the current reserve supports (0 when already short).
    function headroomUsd() external view returns (uint256) {
        (uint256 exposure, uint256 u, uint256 s) = snapshot();
        uint256 byUsdg = requiredUsdgBps == 0 ? type(uint256).max : Math.mulDiv(u, 10_000, requiredUsdgBps);
        uint256 byStock = requiredStockBps == 0 ? type(uint256).max : Math.mulDiv(s, 10_000, requiredStockBps);
        uint256 cap = Math.min(byUsdg, byStock);
        return cap > exposure ? cap - exposure : 0;
    }

    /// @notice Permissionless and idempotent. Pauses ALLOCATION (and swaps) if the reserve no longer covers the exposure. Never unpauses.
    function enforce() external returns (bool paused) {
        (uint256 exposure, uint256 u, uint256 s) = snapshot();
        (uint256 ru, uint256 rs) = required(exposure);
        if (u >= ru && s >= rs) return false;
        PairConfig memory c = vault.pairConfig(pairId);
        if (!c.allocationPaused || !c.swapsPaused) vault.setPairPause(pairId, true, true, c.emergencyMode);
        emit CoverageShort(pairId, exposure, u, ru, s, rs);
        return true;
    }
}
