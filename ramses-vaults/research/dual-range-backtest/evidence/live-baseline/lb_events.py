"""Event signature registry built from the contract sources (read-only), plus decoding."""
import re, subprocess, json, glob
from pathlib import Path
from lb_rpc import topic, HERE
SRC = [
 "/Users/joshua/Peridot/RobinhoodVaults/src/**/*.sol",
 "/Users/joshua/Peridot/ramses-group-pr/remediation/src/**/*.sol",
 "/Users/joshua/Peridot/RobinhoodVaults/PeridotV2-Robinhood/contracts/peridot-contracts-2-5/contracts/contracts/**/*.sol",
 "/Users/joshua/Peridot/RobinhoodVaults/lib/openzeppelin-contracts-upgradeable/contracts/**/*.sol",
 "/Users/joshua/Peridot/RobinhoodVaults/lib/openzeppelin-contracts/contracts/access/**/*.sol",
 "/Users/joshua/Peridot/RobinhoodVaults/lib/openzeppelin-contracts/contracts/governance/TimelockController.sol",
]
PAIRCFG = "(address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool)"
STRUCTS = {"PairConfig":PAIRCFG,"RangePolicy":"(bool,uint16,uint16,uint32,uint8,uint16,uint128)",
 "FeedConfig":"(address,address,address,address,address,bytes32,uint64,uint64,uint16,uint8,uint8,uint8,uint8,bool,bool,uint16)",
 "ReserveConfig":"(address,address,uint128,uint128,uint16,bool,bool)"}
def build():
    reg = {}
    for pat in SRC:
        for f in glob.glob(pat, recursive=True):
            try: s = Path(f).read_text()
            except Exception: continue
            for m in re.finditer(r"\bevent\s+(\w+)\s*\(([^;]*?)\)\s*;", s, re.S):
                name, args = m.group(1), m.group(2)
                types, idx, names = [], [], []
                for a in [x.strip() for x in args.split(",") if x.strip()]:
                    parts = a.split()
                    t = parts[0]
                    t = STRUCTS.get(t, t)
                    # contract-type args => address
                    if re.match(r"^[A-Z]\w*$", t) and t not in ("PairConfig",) and not t.startswith("("): t = "address"
                    types.append(t); idx.append("indexed" in parts); names.append(parts[-1] if len(parts) > 1 and parts[-1] != "indexed" else "")
                sig = f"{name}({','.join(types)})"
                reg.setdefault(topic(sig), (sig, idx, names))
    return reg
def load():
    p = HERE/"raw"/"event-registry.json"
    if p.exists(): return {k:tuple(v) for k,v in json.loads(p.read_text()).items()}
    r = build(); p.write_text(json.dumps(r, indent=0)); return r
def decode_data(types, data):
    """minimal ABI decode of static types from data hex (no 0x). types list of strings; returns list of ints/addresses (tuples flattened in order)."""
    flat=[]
    for t in types:
        if t.startswith("("): flat += t.strip("()").split(",")
        else: flat.append(t)
    h = data[2:] if data.startswith("0x") else data
    ws = [int(h[i:i+64],16) for i in range(0,len(h),64)]
    out=[]
    for t,w in zip(flat,ws):
        if t=="address": out.append("0x"+hex(w)[2:].rjust(40,"0"))
        elif t.startswith("int"):
            bits=int(t[3:] or 256); 
            out.append(w-(1<<256) if w>=(1<<255) else w)  # sign-extended 256-bit words
        elif t=="bool": out.append(bool(w))
        elif t.startswith("bytes32"): out.append("0x"+hex(w)[2:].rjust(64,"0"))
        else: out.append(w)
    return out
def decode(log, reg):
    t0 = log["topics"][0]
    if t0 not in reg: return None
    sig, idx, names = reg[t0]
    inner = sig[sig.index("(")+1:-1]
    # split top-level
    types=[]; depth=0; cur=""
    for ch in inner:
        if ch=="(" : depth+=1
        if ch==")" : depth-=1
        if ch=="," and depth==0: types.append(cur); cur=""
        else: cur+=ch
    if cur: types.append(cur)
    ind_vals=[]; ti=1; data_types=[]
    for t,i in zip(types, idx):
        if i:
            v = log["topics"][ti]; ti+=1
            ind_vals.append(("0x"+v[-40:]) if t=="address" else int(v,16) if t.startswith(("uint","int")) else v)
        else: data_types.append(t)
    return sig.split("(")[0], ind_vals, decode_data(data_types, log["data"])
if __name__=="__main__":
    r = load(); print(len(r))
