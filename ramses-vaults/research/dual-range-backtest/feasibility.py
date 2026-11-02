"""Is a backtest configuration something the CURRENT contract can run?

Mirrors, literally, ``RamsesRangeLib.validatePolicy`` (ramses-vaults/src/libraries/RamsesRangeLib.sol) and the
behaviour of ``RamsesRangeLib.recenter`` / ``RamsesBoostedVault`` that the engine would otherwise silently assume
away. A configuration that fails is reported as HYPOTHETICAL with the exact reasons; nothing here changes a result.

Contract rules (one RangePolicy per pair, uint16/uint32/uint8 fields):
  600 <= halfWidthTicks <= 4800 and halfWidthTicks % tickSpacing == 0
  tickSpacing <= triggerTicks <= halfWidthTicks / 2          (integer division)
  3600 s <= minInterval <= 7 days (uint32, whole seconds)
  1 <= maxPerDay <= 24
  maxLossBps <= 100 (uint16)
  maxRangedValueUsd > 0
Behaviour the contract fixes and the engine must not assume away:
  * ONE ranged position per pair (so no dual layers) centred on the oracle tick;
  * recenter does not swap: only matched value is redeployed (no ``rebalance`` swap);
  * full range is not a RangePolicy: it is the adapter's default (minUsableTick .. maxUsableTick), checked separately.
Not modelled here (declared, not hidden): the integer oracle tick vs the engine's float tick, ``_requireBandCoversGate``
(removal tolerance vs the guard's removal deviation) and the per-recenter deploy loss bound set by the operator.
"""
from __future__ import annotations

from dataclasses import dataclass, field

MIN_HALF_WIDTH_TICKS = 600
MAX_HALF_WIDTH_TICKS = 4_800
MIN_RECENTER_INTERVAL = 3_600
MAX_RECENTER_INTERVAL = 7 * 86_400
MAX_PER_DAY_LIMIT = 24
MAX_RECENTER_LOSS_BPS = 100
MIN_TICK, MAX_TICK = -887_272, 887_272

# Pool tick spacings as read from the pools (results/pools_static.json). NEVER use the Config default.
POOL_SPACING = {"SPY": 1, "GLD": 1, "NVDA": 10}


@dataclass
class Feasibility:
    mode: str                     # "single_range_policy" | "full_range" | "dual_layer" | "invalid"
    contract_feasible: bool
    violations: list = field(default_factory=list)      # validatePolicy-style failures
    hypothetical: list = field(default_factory=list)    # engine behaviour the contract does not have
    assumptions: list = field(default_factory=list)     # parameters outside RangePolicy that the run assumes

    def asdict(self) -> dict:
        return dict(self.__dict__)


class InfeasibleConfig(ValueError):
    pass


U16, U32, U8, U128 = 2**16 - 1, 2**32 - 1, 2**8 - 1, 2**128 - 1
MAX_RANGED_USD_E18 = U128 / 1e18  # maxRangedValueUsd is a uint128 holding USD * 1e18


def _is_num(x) -> bool:
    """A real, finite number (bool excluded). NaN and +/-inf are never valid policy inputs."""
    import math
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _whole(x, lo, hi, what, out):
    """Validate an unsigned integer field: finite, whole, within [lo, hi]. Returns the int or None (violation appended)."""
    if not _is_num(x):
        out.append(f"{what} {x!r} is not a finite number")
        return None
    if x != int(x):
        out.append(f"{what} {x} is not a whole number")
        return None
    x = int(x)
    if x < 0:
        out.append(f"{what} {x} is negative (unsigned field)")
        return None
    if x > hi:
        out.append(f"{what} {x} exceeds the field's maximum {hi}")
        return None
    return x


def validate_policy(half: int, trigger: int, cooldown_s: float, max_per_day: int, loss_bps: float,
                    max_ranged_usd: float | None, spacing: int) -> list:
    """Return every ``validatePolicy`` violation (empty list = the policy would be accepted on chain).

    Types follow RangeTypes.RangePolicy (halfWidthTicks/triggerTicks/maxLossBps uint16, minInterval uint32, maxPerDay uint8,
    maxRangedValueUsd uint128 of USD*1e18): negative, non-integer, non-finite (NaN/inf) and out-of-width values are rejected
    before any rule is evaluated, so they can neither crash the check nor slip through a comparison."""
    v = []
    if not _is_num(spacing) or spacing != int(spacing) or spacing <= 0:
        return [f"tickSpacing {spacing!r} must be a positive whole number"]
    spacing = int(spacing)
    h = _whole(half, 0, U16, "halfWidthTicks", v)
    t = None if trigger is None else _whole(trigger, 0, U16, "triggerTicks", v)
    if trigger is None:
        v.append("triggerTicks is not an explicit integer (legacy fractional trigger)")
    c = _whole(cooldown_s, 0, U32, "minInterval", v)
    d = _whole(max_per_day, 0, U8, "maxPerDay", v)
    lo = _whole(loss_bps, 0, U16, "maxLossBps", v)
    if h is not None:
        if not (MIN_HALF_WIDTH_TICKS <= h <= MAX_HALF_WIDTH_TICKS):
            v.append(f"halfWidthTicks {h} outside [{MIN_HALF_WIDTH_TICKS}, {MAX_HALF_WIDTH_TICKS}]")
        if h % spacing != 0:
            v.append(f"halfWidthTicks {h} is not a multiple of tickSpacing {spacing}")
    if t is not None:
        if t < spacing:
            v.append(f"triggerTicks {t} < tickSpacing {spacing}")
        if h is not None and t > h // 2:
            v.append(f"triggerTicks {t} > halfWidthTicks/2 = {h // 2}")
    if c is not None:
        if c < MIN_RECENTER_INTERVAL:
            v.append(f"minInterval {c} s < {MIN_RECENTER_INTERVAL} s (1 hour)")
        if c > MAX_RECENTER_INTERVAL:
            v.append(f"minInterval {c} s > 7 days")
    if d is not None and (d == 0 or d > MAX_PER_DAY_LIMIT):
        v.append(f"maxPerDay {d} outside [1, {MAX_PER_DAY_LIMIT}]")
    if lo is not None and lo > MAX_RECENTER_LOSS_BPS:
        v.append(f"maxLossBps {lo} > {MAX_RECENTER_LOSS_BPS}")
    if max_ranged_usd is None:
        v.append("maxRangedValueUsd must be set (the engine's None means uncapped, which the contract cannot express)")
    elif not _is_num(max_ranged_usd):
        v.append(f"maxRangedValueUsd {max_ranged_usd!r} is not a finite number")
    elif max_ranged_usd <= 0:
        v.append(f"maxRangedValueUsd {max_ranged_usd} must be > 0")
    elif max_ranged_usd > MAX_RANGED_USD_E18:
        v.append(f"maxRangedValueUsd {max_ranged_usd} exceeds uint128 of USD*1e18")
    return v


def full_range_violations(lo: int, hi: int, spacing: int) -> list:
    """The adapter's default range (TickMath.minUsableTick .. maxUsableTick for the pool's spacing)."""
    v = []
    exp_lo = -((-MIN_TICK) // spacing) * spacing  # Solidity truncates toward zero: the lower bound rounds up
    exp_hi = (MAX_TICK // spacing) * spacing
    if lo != exp_lo:
        v.append(f"lower {lo} != minUsableTick({spacing}) = {exp_lo}")
    if hi != exp_hi:
        v.append(f"upper {hi} != maxUsableTick({spacing}) = {exp_hi}")
    if lo % spacing or hi % spacing:
        v.append("range is not aligned to the tick spacing")
    if not (MIN_TICK <= lo < hi <= MAX_TICK):
        v.append("range is outside [MIN_TICK, MAX_TICK] or empty")
    return v


def assess(cfg, spacing: int) -> Feasibility:
    """Classify an ``engine.Config`` for a pool with the given ACTUAL tick spacing."""
    from engine import full_range_ticks
    assumptions = [
        f"feed guard: max_stale {cfg.max_stale / 3600:.0f} h, blocked sessions {list(cfg.block_sessions)}, pool-vs-oracle allocation gate {cfg.gate_bps:.0f} bp (vault guard values are configuration, not RangePolicy fields)",
        f"per-recenter loss bound {cfg.loss_bps:g} bp at the oracle reference (RangePolicy.maxLossBps; the contract also applies the operator's deployLossBps)",
        "oracle tick is a float here and an integer floor on chain; _requireBandCoversGate is not modelled",
    ]
    if cfg.spacing != spacing:
        return Feasibility("invalid", False, [f"Config.spacing {cfg.spacing} != the pool's actual tickSpacing {spacing}"], [], assumptions)
    if cfg.full_range:
        lo, hi = full_range_ticks(spacing, cfg.min_tick, cfg.max_tick)
        v = full_range_violations(lo, hi, spacing)
        return Feasibility("full_range", not v, v, [], assumptions + ["full range is the adapter default, not a RangePolicy; it never recenters"])
    hyp = []
    if cfg.core_half and cfg.wide_half:
        hyp.append("two layers (core + backup): the contract holds ONE ranged position per pair")
    if cfg.wide_half and not cfg.core_half:
        hyp.append("backup-only layer is not distinguishable from a single range; use core_half")
    if cfg.rebalance:
        hyp.append("recenter swaps the imbalance through the pool: RamsesRangeLib.recenter has no swap (matched value only)")
    half = cfg.core_half
    v = validate_policy(half, cfg.trigger_ticks, cfg.cooldown, cfg.max_per_day, cfg.loss_bps, cfg.max_ranged_usd, spacing)
    if cfg.core_half and cfg.wide_half:
        mode = "dual_layer"
    elif cfg.core_half:
        mode = "single_range_policy"
    else:
        mode = "invalid"
    return Feasibility(mode, not v and not hyp, v, hyp, assumptions)


def require_feasible(cfg, spacing: int) -> Feasibility:
    f = assess(cfg, spacing)
    if not f.contract_feasible:
        raise InfeasibleConfig(f"{cfg.name}: " + "; ".join(f.violations + f.hypothetical))
    return f


def legacy_result_feasibility(name: str, cfg: dict, spacing: int = 1) -> Feasibility:
    """Classify an ALREADY PRODUCED result row from its recorded ``cfg`` dict (historical grids). Their trigger was the
    fractional engine default (0.70 x core half-width, 0.60 x wide), their cooldown 900 s, and most swapped at recenter."""
    from engine import Config
    c = Config(name=name, core_half=cfg.get("core_half", 0), wide_half=cfg.get("wide_half", 0), full_range=bool(cfg.get("full_range")),
               cooldown=cfg.get("cooldown", 900.0), max_per_day=cfg.get("max_per_day", 24), spacing=spacing,
               rebalance=bool(cfg.get("rebalance", False)))
    f = assess(c, spacing)
    if c.core_half and not c.full_range:
        trig = max(0.70 * c.core_half, spacing)
        f.violations = [x for x in f.violations if "triggerTicks is not an explicit" not in x]
        if trig > c.core_half // 2:
            f.violations.append(f"legacy trigger 0.70 x {c.core_half} = {trig:g} ticks > halfWidthTicks/2 = {c.core_half // 2}")
        f.contract_feasible = not f.violations and not f.hypothetical
    return f
