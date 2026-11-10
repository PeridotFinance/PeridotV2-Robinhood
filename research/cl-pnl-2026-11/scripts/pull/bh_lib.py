"""Shared helpers for the Blackhole (Avalanche C-Chain) data pull. Read-only, Python 3 stdlib only.

Reuses scripts/pull/avax_lib.py (endpoints, paced JSON-RPC, cached chunked eth_getLogs, header batches, exact v3
math) without modifying it. Adds: a pure-Python keccak256 (so selectors and event topics are computed here, not
pasted), ABI word helpers, and the Blackhole address book (every address is verified on chain by bh_00_state.py).

Only eth_blockNumber / eth_getBlockByNumber / eth_getLogs / eth_call / eth_getCode / eth_getStorageAt /
eth_getTransactionReceipt / eth_getTransactionByHash are used. Nothing is signed or sent.
Writes go only to data/blackhole/ and data/.cache/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import avax_lib as A  # noqa: E402

ROOT = A.ROOT
DATA_BH = ROOT / "data" / "blackhole"
CACHE_BH = ROOT / "data" / ".cache" / "blackhole"
for _d in (DATA_BH, CACHE_BH):
    _d.mkdir(parents=True, exist_ok=True)

END = 99924388
END_HASH = "0x6b4ae5c8e69d8e7bb381910a28ea9d61af1edab6bafabb8b57ec6aeab9c8323f"
END_TS = 1794301929
START = 91716610
START_TS = 1785542401

WAVAX = "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"
USDC = "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E"
PHARAOH_POOL = "0xf01449C0bA930B6e2CaCA3DEF3CCBd7a3E589534"
PHARAOH_FACTORY = "0xAE6E5c62328ade73ceefD42228528b70c8157D0d"

# ---- Blackhole address book, as published at https://docs.blackhole.xyz/technical-amm-documentation (read 2026-11-10)
DOCS = {
    "BLACK": "0xcd94a87696FAC69Edae3a70fE5725307Ae1c43f6",
    "RouterV2": "0x9EED160D7D8253DeC1A2A512e504DE5E7ff3C111",
    "VoterV3": "0xE30D0C8532721551a51a9FeC7FB233759964d9e3",
    "VotingEscrow": "0xEac562811cc6abDbB2c9EE88719eCA4eE79Ad763",
    "GaugeManager": "0x59aa177312Ff6Bdf39C8Af6F46dAe217bf76CBf6",
    "GaugeFactory": "0x9E95eF7D8b87708641923C48C4eB298ED7CA6552",
    "GaugeFactoryCL": "0x6B6a3D5A1c536aCE1D761685aF241b2cb7a6eA5E",
    "PairFactory": "0xfE926062Fb99CA5653080d6C14fE945Ad68c265C",
    "BribeFactory": "0xfE842861b9F79Bb77CCb6043731D433D63B365dF",
    "Minter": "0xAcc34Ad51457930989fB5050C2Dce6339F06479B",
    "AlgebraPoolApiStorage": "0xa90BC0E1D28151206530dABa53A5b8d28332cb7f",
    # the docs table labels this "AlgebraPool"; on chain it is the WAVAX/USDC tick-spacing-1 pool of the v1 deployer
    "docs_AlgebraPool_row": "0xA02Ec3Ba8d17887567672b2CDCAF525534636Ea0",
    "poolDeployer": "0x9B2441037E286d5Bf9456a3BE7b5273fe28DbA1e",
    "AlgebraFactory": "0x512eb749541B7cf294be882D636218c84a5e9E5F",
    "vaultFactory": "0xdC0b5db6f5f957AD0aC5e0Af8A2C084077D079B3",
    "BasePluginV1Factory": "0x038CBa84D1CDf5B0820fC6a1FAE93f42240268bb",
    "quoter": "0x7A88C46740fDFE446DD6Ad97cc0A94716848D214",
    "quoterV2": "0x3e182bcf14Be6142b9217847ec1112e3c39Eb689",
    "swapRouter": "0xaBfc48e8BED7b26762745f3139555F320119709d",
    "tickLens": "0xE66aAe9CB5aB27eC6662C0EC9B9b28764D4a822E",
    "NFPM_v1": "0x3fED017EC0f5517Cdf2E8a9a4156c64d74252146",
    "eternal_v1": "0x01A8A00A6fC8106B94f84aAbAef689Fd0D77271A",
    "farmingCenter_v1": "0xa47Ad2C95FaE476a73b85A355A5855aDb4b3A449",
    "cpd_v1_ts1": "0xDcFccf2e8c4EfBba9127B80eAc76c5A122125d29",
    "cpd_v1_ts50": "0x58b05074D52D1a84D8FfDAddA3c1b652e8C56994",
    "cpd_v1_ts100": "0xf9221dE143A0E57c324bF2a0f281e605e845D767",
    "cpd_v1_ts200": "0x5D433A94A4a2aA8f9AA34D8D15692Dc2E9960584",
    "BasePluginV3Factory": "0x5Af551Ba1bf743978B42d4e133bfF8B6231176fF",
    "SecurityRegistry": "0xd2fE840C24E1E4B217909BD3F88FeD6BF3537b02",
    "FeeDiscountRegistry": "0x6A8591aDb2ca2427021906E223e2B5de20106aa9",
    "NFPM_v2": "0xfD1c727D8A2259493C66899f478EbfeA41329545",
    "eternal_v2": "0x9c70BedD11Cf874F07B1Bd9C29e3e41f9F248F5c",
    "farmingCenter_v2": "0xCeCc64211f1Ed70a71BD47EB656f7067C1f45541",
    "cpd_v2_ts1": "0x43c4b7BD4C173992C6711c631859A2EAC84BF8Db",
    "cpd_v2_ts10": "0x48b1d49fB891bAb3543Db6e902cC54726A133acE",
    "cpd_v2_ts50": "0x14E4E36f70ff06DC874F0e827B174ced91e51Cc8",
    "cpd_v2_ts100": "0x5ef3876cA93b93c9BcfD637783Ed99412b1efF43",
    "cpd_v2_ts200": "0x0AFf494476Dc74CF7BB5Dd005c2B1fE7Be76efA2",
}

# ---- dataset pool and its satellites (found and verified on chain by bh_00_state.py; see data/blackhole/state_at_end.json)
POOL = "0x41100C6D2c6920B10d12Cd8D59c8A9AA2eF56fC7"            # AlgebraPool (Integral core 1.2), WAVAX/USDC, tick spacing 200
POOL_CREATED = 65228165                                       # CustomPool event of the AlgebraFactory
POOL_DEPLOYER = DOCS["cpd_v1_ts200"]
POOL_PLUGIN = "0xdB2093a4DF635dcE499A0db0BBA9ABe39dB6594A"
POOL_VAULT = "0xA35df874df4b412F845c1236C8Cca0FFcdb08c8B"     # AlgebraCommunityVault of the pool
GAUGE = "0x3ADE52f9779c07471F4B6d5997444C3c2124C1c0"          # GaugeCL
INTERNAL_BRIBE = "0x89Ac76981DE552E27083a53CDCc2359c9438E5cf"
EXTERNAL_BRIBE = "0x01601580eAAecF7D7F0042e3DDDE6e204BF6Fe88"
BLACK = DOCS["BLACK"]
BLACK_PAIR = "0x0D9Fd6dd9b1FF55fB0A9bB0e5f1B6a2D65b741A3"      # vAMM-USDC/BLACK (classic pair), token0 USDC, token1 BLACK
PHARAOH_QUOTER = "0xB7297301b7CC659BB96D51754643A0Df6eEA2138"  # Pharaoh QuoterV2 (same deployer EOA as the factory / NFPM)
OTHER_POOLS = {
    "v1_ts1": "0xA02Ec3Ba8d17887567672b2CDCAF525534636Ea0",
    "v1_ts50": "0x668Aa7AEfa8512416Fc6244afBE5129200277A69",
    "v2_ts1": "0x7789fA308147Eae364e3BCfa35351F2b9E0bd012",
}

# ---- event signatures (from the verified sources on Routescan; every topic is checked against a real log) ----
EV_POOL = ["Initialize(uint160,int24)", "Mint(address,address,int24,int24,uint128,uint256,uint256)",
           "Collect(address,address,int24,int24,uint128,uint128)", "Burn(address,int24,int24,uint128,uint256,uint256)",
           "BurnFee(address,uint24)", "Swap(address,address,int256,int256,uint160,uint128,int24)",
           "SwapFee(address,uint24,uint24)", "Flash(address,address,uint256,uint256,uint256,uint256)",
           "ExcessTokens(uint256,uint256)", "CommunityFee(uint16)", "TickSpacing(int24)", "Plugin(address)",
           "PluginConfig(uint8)", "Fee(uint16)", "CommunityVault(address)", "Skim(address,uint256,uint256)"]
EV_PRE = ["Initialize(uint160,int24)", "Mint(address,address,int24,int24,uint128,uint256,uint256)",
          "Burn(address,int24,int24,uint128,uint256,uint256)", "CommunityFee(uint16)", "TickSpacing(int24)",
          "Plugin(address)", "PluginConfig(uint8)", "Fee(uint16)", "CommunityVault(address)"]
TRANSFER = "Transfer(address,address,uint256)"

# The pre-window pull of the pool (creation..START-1, Mint/Burn/config events only) is cut into consecutive segments
# so that several processes can fetch at once; each segment is its own cache directory with its own ledger and the
# build step asserts that the segments tile [POOL_CREATED, START-1] without gap or overlap.
PRE_SEGMENTS = [
    ("bh_pool_pre", 65228165, 67178164),
    ("bh_pool_pre_1", 67178165, 68778164),
    ("bh_pool_pre_1b", 68778165, 69608078),
    ("bh_pool_pre_1c", 69608079, 70437992),
    ("bh_pool_pre_1d", 70437993, 71267905),
    ("bh_pool_pre_2", 71267906, 73217905),
    ("bh_pool_pre_2b", 73217906, 73931153),
    ("bh_pool_pre_2c", 73931154, 74644401),
    ("bh_pool_pre_2d", 74644402, 75357646),
    ("bh_pool_pre_3", 75357647, 79407646),
    ("bh_pool_pre_3b", 79407647, 79427517),
    ("bh_pool_pre_3c", 79427518, 79447387),
    ("bh_pool_pre_4", 79447388, 83537128),
    ("bh_pool_pre_5", 83537129, 87626869),
    ("bh_pool_pre_6", 87626870, 91716609),
]
assert PRE_SEGMENTS[-1][2] == START - 1 and all(x[2] + 1 == y[1] for x, y in zip(PRE_SEGMENTS, PRE_SEGMENTS[1:]))

# ---- keccak256 (pure Python, FIPS-202 permutation with the original Keccak padding 0x01) ----
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
_M = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _f(a: list) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136
    p = bytearray(data)
    p.append(0x01)
    while len(p) % rate:
        p.append(0)
    p[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(p), rate):
        blk = p[off:off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(blk[8 * i:8 * i + 8], "little")
        _f(a)
    out = b""
    for i in range(4):
        out += a[i % 5][i // 5].to_bytes(8, "little")
    return out


assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
assert keccak256(b"Transfer(address,address,uint256)").hex() == \
    "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def sel(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()[:8]


def topic(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()


def checksum(addr: str) -> str:
    a = addr.lower().replace("0x", "")
    h = keccak256(a.encode()).hex()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(a))


def call(to: str, sig: str, args: str = "", block: int | str = END, source: str = "avax") -> str:
    """eth_call of `sig` (args = pre-encoded hex words, no 0x) at `block`; returns the raw hex result."""
    return A.eth_call(source, to, sel(sig) + args, block)


def try_call(to: str, sig: str, args: str = "", block: int | str = END, source: str = "avax"):
    """Like call() but returns None on a revert (one attempt after the transport succeeded)."""
    import json
    import time
    import urllib.error
    url = A.RPC[source]
    tag = hex(block) if isinstance(block, int) else block
    delay = 1.5
    for _ in range(6):
        A._pace(source, 0.25)
        try:
            d = A.http_json(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                  "params": [{"to": to, "data": sel(sig) + args}, tag]}, timeout=60)
        except urllib.error.HTTPError as e:
            time.sleep(delay * (3 if e.code in (429, 403) else 1))
            delay *= 2
            continue
        except Exception:
            time.sleep(delay)
            delay *= 2
            continue
        if "error" in d:
            msg = json.dumps(d["error"]).lower()
            if "revert" in msg or "execution" in msg or "invalid opcode" in msg or "out of gas" in msg:
                return None
            time.sleep(delay)
            delay *= 2
            continue
        return d["result"]
    raise A.RpcError(f"try_call {to} {sig}: transport failed")


def W(h: str) -> list[int]:
    return A.words(h) if h and h != "0x" else []


def a(addr: str) -> str:
    return A.addr_arg(addr)


def u(n: int) -> str:
    return A.uint_arg(n)


def i256(n: int) -> str:
    return A.uint_arg(n)


def addr_of(word: int) -> str:
    return checksum(A.word_addr(word))


def s_int(x: int, bits: int) -> int:
    x &= (1 << bits) - 1
    return x - (1 << bits) if x >> (bits - 1) else x
