// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @dev Governance-set and bounded in `RangeLib.validatePolicy`. One storage slot.
struct RangePolicy {
    bool enabled;
    uint16 halfWidthTicks; // range is centred on the oracle price, +/- this many ticks
    uint16 triggerTicks; // recenter once the oracle centre moved at least this far
    uint32 minInterval; // seconds between recenters
    uint8 maxPerDay; // recenters per rolling 24h
    uint16 maxLossBps; // oracle-valued loss a recenter may cause, of pair assets
    uint128 maxRangedValueUsd; // cap on oracle-valued liquidity in the ranged position (USD 1e18)
}

struct RangeState {
    int24 centerTick; // raw oracle tick at the last recenter
    bool initialized;
    uint64 lastRecenter;
    uint64 windowStart;
    uint8 windowCount;
}
