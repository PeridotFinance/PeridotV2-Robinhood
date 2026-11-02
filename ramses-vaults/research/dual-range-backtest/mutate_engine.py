#!/usr/bin/env python3
"""Mutation check of the backtest engine: every mutation must make test_engine fail; engine.py bytes are always restored."""
import subprocess, sys
from pathlib import Path
HERE = Path(__file__).parent
ENG = HERE / "engine.py"
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
]

def run_tests():
    r = subprocess.run([sys.executable, "-m", "unittest", "test_engine"], cwd=HERE, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)

def main():
    original = ENG.read_text()
    survivors = []
    try:
        for name, old, new in CASES:
            assert original.count(old) == 1, f"anchor not unique: {name} ({original.count(old)})"
            ENG.write_text(original.replace(old, new))
            code, out = run_tests()
            print(("KILLED  " if code != 0 else "SURVIVED"), name, flush=True)
            if code == 0:
                survivors.append(name)
    finally:
        ENG.write_text(original)
    code, _ = run_tests()
    assert code == 0, "engine restore failed"
    if survivors:
        print("SURVIVORS:", survivors); sys.exit(1)
    print("all mutations killed")

if __name__ == "__main__":
    main()
