"""Shared setup for the Phase 0 research scripts (read-only)."""
import sys
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "lib"))
from evidence_lib import Rpc, TimeIndex, keccak_topic, words, s256, sN, topic_addr, topic_int, addr_topic, iso, session_of  # noqa

CACHE = Path("/Users/joshua/Peridot/evidence-cache")
import os
RESULTS = Path(os.environ["RAMSES_RESULTS_DIR"]) if os.environ.get("RAMSES_RESULTS_DIR") else HERE / "results"  # offline reproduction points this at an unpacked input bundle
RESULTS.mkdir(exist_ok=True)

ADDR = {
    "SPY": dict(pool="0x38453c115607463ac284820ce959831042f3df4e", gauge="0x739e31798efe8cff3ef4c613d8ae751e179c7337",
                stock="0x117cc2133c37b721f49de2a7a74833232b3b4c0c", feed="0x319724394D3A0e3669269846abE664Cd621f9f6A",
                agg="0x78bcb218fa04b9b3a278ebc865ed320bf8defbac", stock_is_token0=True),
    "GLD": dict(pool="0x9896af2f6fed9939acc87637b973b6484e6619d8", gauge="0x7724fa91cd3bbf4bf1eee4e8383dbb74e08a975c",
                stock="0xc9a981fee1f9dec688bb123ccdecc63d0debfc4e", feed="0x470A51258068043bd43dC0a56245625C9fE86eB0",
                agg="0xea3f8e77a98d7877b6f8164569a8e82454cccdb8", stock_is_token0=False),
    "NVDA": dict(pool="0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b", gauge="0x93c6A733FB3165D94774B3A5Fc4319D29bd7b6ce",
                 stock="0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC", feed="0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15",
                 agg="0xc9d16e4f2569b9e3ea0468fd85844953713dc2a2", stock_is_token0=False),
}
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
USDG_FEED = "0x61B7e5650328764B076A108EFF5fa7282a1B9aD2"
USDG_AGG = "0x8beee3503f6860d5dac4ce26b5eee92982951c2e"
NFP = "0x2eBd7B85a4E08D5B508b04BA147976C94afE6590"
RAM = "0x5173D45A1191eE33cBB7D8c7e65f21B04eD54802"

def rpc(): return Rpc(cache_dir=CACHE, min_interval=0.3)
def T(sig): return keccak_topic(sig)
