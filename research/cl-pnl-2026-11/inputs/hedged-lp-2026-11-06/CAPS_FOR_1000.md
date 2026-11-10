# What would have to move for a $1,000 hedged position (prepared, NOT executed)

Read at the head on 2026-11-06. Every change below is a Ledger transaction; none has been sent. Both calls were
simulated with `eth_call` from the Ledger and succeed. `HEDGED_LP.md` is a one-window result, not a recommendation to do
this.

## Sizing

- **The hedge's NVDA debt equals the LP's NVDA holding:** about 40% of a $1,000 LP at the centre of the 2400-tick band,
  and up to the LP's whole value at the lower edge. Plan for **$1,000 of NVDA debt**.
- **Margin is half the debt, so equity is $500 and position value is $1,500.** That is 3x by the contract's measure
  (position value / equity), within the pair's 5x.

## Parameter changes (all owned by the Ledger `0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD`)

| what | now | needed | call |
| --- | --- | --- | --- |
| Short pair (margin pUSDG, position pUSDG, debt pNVDA): `maxPositionValueUsd` | $2 | **$1,500** | queue, then set ≥ 3,600 s later |
| Same pair: `maxDebtValueUsd` | $1 | **$1,000** | same call |
| pNVDA borrow cap (controller `0x6148…9c39`) | 4.35 NVDA | **7 NVDA** (about $1,300 at $186.5) | `_setMarketBorrowCaps`, immediate |

Every other risk field stays exactly as it is live: 5x, 20% initial, 10% maintenance, 125% target, 50/50% liquidation,
5% bonus, 1% slippage, 1% deviation.

```sh
R=https://rpc.mainnet.chain.robinhood.com; L=0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD
C=0x09F94fe0B79E000c8a26617c63E3427fdECB528b; PU=0x55aEd0569c8f0D166D71facE57B57C2f2624a563; PN=0xa155ccCB986774AE818b3F10F07d01D1b7A47b26
S='(address,address,address,(bool,uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint128,uint128))'
T='(true,500,2000,1000,12500,5000,5000,500,100,100,1500000000000000000000,1000000000000000000000)'
# 1. Ledger: queue (starts the 3,600 s action delay)
cast send $C "queuePairRisk$S" $PU $PU $PN "$T" --ledger --from $L --rpc-url $R
# 2. Ledger, at least 3,600 s later: apply
cast send $C "setPairRisk$S" $PU $PU $PN "$T" --ledger --from $L --rpc-url $R
# 3. Ledger: borrow cap
cast send 0x6148183676e304dbe63a85c350c208da3ceac39c '_setMarketBorrowCaps(address[],uint256[])' "[$PN]" "[7000000000000000000]" --ledger --from $L --rpc-url $R
# verify
cast call $C "getPairRisk(address,address,address)((bool,uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint128,uint128))" $PU $PU $PN --rpc-url $R
```

## Not parameters, but also needed (liquidity, so these are funding decisions)

| what | now | needed for a $1,000 short | why |
| --- | --- | --- | --- |
| NVDA lendable in pNVDA (cash) | 0.033 NVDA | **≥ 5.4 NVDA** | the short borrows NVDA from pNVDA |
| NVDA in the flash vault `0x79d3…8b06` | 0.025 NVDA | **≥ 5.4 NVDA** | the executor flash-borrows the debt asset on both open and close |
| The NVDA/USDG pair itself | allocation paused | $1,000 of LP | NVDA allocation stays paused until the range-order product exists (operator decision) |

**Even with all three caps moved, a $1,000 hedge cannot open until NVDA supply and flash liquidity exist at that size.**
Raising the caps without that liquidity changes nothing in practice. It also means the margin stack's risk now admits
positions about 500 to 1,000 times larger than any tested live, which keeper gas and liquidation capacity (handoff 00
§4) would need to support first.
