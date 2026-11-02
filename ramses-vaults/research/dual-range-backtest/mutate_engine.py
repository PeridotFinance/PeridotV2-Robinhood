#!/usr/bin/env python3
"""Mutation check of the backtest engine: every mutation must make test_engine fail; engine.py bytes are always restored."""
import subprocess, sys
from pathlib import Path
HERE = Path(__file__).parent
ENG = HERE / "engine.py"
FEAS = HERE / "feasibility.py"
CASES = [
    ("protocol share ignored", "rate = fee_ppm / 1e6 * (1.0 - (proto if c.protocol_share is None else c.protocol_share))", "rate = fee_ppm / 1e6"),
    ("cooldown ignored", "ready = [n for n in due if t - self.layers[n].last >= c.cooldown]", "ready = list(due)"),
    ("stale feed not blocking", "if age > c.max_stale:", "if False:"),
    ("closed session not blocking", "if self.session_of(int(t)) in c.block_sessions:", "if False:"),
    ("allocation gate ignored", "if abs(p_pool / price_o - 1.0) * 1e4 > c.gate_bps:", "if False:"),
    ("rolling budget ignored", "if len(self.recent) + len(ready) > c.max_per_day:", "if False:"),
    ("dilution ignored", "share = Lo / (L_tp + Lo) if c.dilute else Lo / L_tp", "share = Lo / L_tp"),
    ("gas not charged", "            self.gas += c.gas_usd\n", "            pass\n"),
    ("loss bound ignored", "if after + before * c.loss_bps / 1e4 < before:", "if False:"),
    ("rollback keeps swap counters", "\n            self.swapped_usd, self.swaps = snap[5], snap[6]\n", "\n"),
    ("unmatched not left idle (deploys max side)", "matched = min(v_stock, v_usdg, cap_usd_per_side)", "matched = min(max(v_stock, v_usdg), cap_usd_per_side)"),
    ("mirror: token order ignored", "return p * Q if self.stock_is_token0 else Q / p", "return p * Q"),
    ("out-of-range earns RAM", "if ly.lo <= tick_prev < ly.hi:", "if True:"),
    ("no-liquidity swap allowed", "        if L <= 0:\n            return None  # no third-party liquidity at the current price: nothing to swap against\n", ""),
    ("fees ignore path overlap", "if lo_s < e < hi_s:", "if False:"),
    # --- added with the contract-feasibility work (engine.py) ---
    ("explicit trigger ignored (legacy fraction used)", "trig = float(c.trigger_ticks) if c.trigger_ticks is not None else max(c.core_trigger_frac * c.core_half, c.spacing)", "trig = max(c.core_trigger_frac * c.core_half, c.spacing)"),
    ("ranged-value cap ignored", "            cap_usd_per_side = min(cap_usd_per_side, c.max_ranged_usd / 2.0)\n", "            pass\n"),
    ("reserve cut ignored", "kept = fee_raw * (1.0 - c.reserve_bps / 1e4)", "kept = fee_raw"),
    ("full range lower bound off by one (legacy)", "lo = -((-min_tick) // spacing) * spacing", "lo = (min_tick // spacing) * spacing + spacing"),
    ("full range upper bound off by one (legacy)", "hi = (max_tick // spacing) * spacing", "hi = (max_tick // spacing) * spacing - spacing"),
    # --- feasibility.py ---
    ("F: min half-width not enforced", "if not (MIN_HALF_WIDTH_TICKS <= half <= MAX_HALF_WIDTH_TICKS):", "if not (half <= MAX_HALF_WIDTH_TICKS):", "feas"),
    ("F: max half-width not enforced", "if not (MIN_HALF_WIDTH_TICKS <= half <= MAX_HALF_WIDTH_TICKS):", "if not (MIN_HALF_WIDTH_TICKS <= half):", "feas"),
    ("F: spacing multiple not enforced", "if half % spacing != 0:", "if False:", "feas"),
    ("F: trigger upper bound not enforced", "if trigger > half // 2:", "if False:", "feas"),
    ("F: trigger upper bound uses non-integer half", "if trigger > half // 2:", "if trigger > half / 2 + 1:", "feas"),
    ("F: trigger spacing floor not enforced", "if trigger < spacing:", "if trigger < 1:", "feas"),
    ("F: cooldown floor not enforced", "if cooldown_s < MIN_RECENTER_INTERVAL:", "if False:", "feas"),
    ("F: cooldown ceiling not enforced", "if cooldown_s > MAX_RECENTER_INTERVAL:", "if False:", "feas"),
    ("F: rolling limit zero allowed", "max_per_day == 0 or max_per_day > MAX_PER_DAY_LIMIT", "max_per_day > MAX_PER_DAY_LIMIT", "feas"),
    ("F: rolling limit ceiling not enforced", "max_per_day == 0 or max_per_day > MAX_PER_DAY_LIMIT", "max_per_day == 0", "feas"),
    ("F: loss bound not enforced", "loss_bps < 0 or loss_bps > MAX_RECENTER_LOSS_BPS", "loss_bps < 0", "feas"),
    ("F: cap > 0 not enforced", "if max_ranged_usd is None or not max_ranged_usd > 0:", "if False:", "feas"),
    ("F: dual layers accepted", 'hyp.append("two layers (core + backup): the contract holds ONE ranged position per pair")', "pass", "feas"),
    ("F: swap at recenter accepted", "if cfg.rebalance:\n        hyp.append", "if False:\n        hyp.append", "feas"),
    ("F: pool spacing mismatch accepted", "if cfg.spacing != spacing:", "if False:", "feas"),
]

def run_tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_engine", "test_feasibility"], cwd=HERE, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)

def main():
    originals = {ENG: ENG.read_text(), FEAS: FEAS.read_text()}
    survivors = []
    try:
        for case in CASES:
            name, old, new = case[:3]
            target = FEAS if len(case) > 3 and case[3] == "feas" else ENG
            original = originals[target]
            assert original.count(old) == 1, f"anchor not unique: {name} ({original.count(old)})"
            target.write_text(original.replace(old, new))
            code, out = run_tests()
            target.write_text(original)
            print(("KILLED  " if code != 0 else "SURVIVED"), name, flush=True)
            if code == 0:
                survivors.append(name)
    finally:
        for f, txt in originals.items():
            f.write_text(txt)
    code, _ = run_tests()
    assert code == 0, "engine restore failed"
    if survivors:
        print("SURVIVORS:", survivors); sys.exit(1)
    print("all mutations killed")

if __name__ == "__main__":
    main()
