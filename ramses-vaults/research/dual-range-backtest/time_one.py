import time, sys
from bt_lib import *
ds, meta, i0 = load_dataset("SPY")
cfg = E.Config(name="t", capital_usd=10_000)
t = time.time()
sim = E.Sim(ds, cfg, session_of, start_index=i0).run()
r = sim.result()
print(time.time() - t, "s")
import json; print(json.dumps(r, indent=1))
