# Dune queries for the NVDA/USDG vault

Dune indexes Robinhood Chain natively (`robinhood.logs`, `robinhood.transactions`, ...), so the vault's own
events need no upload. **These queries were written from the contract's event signatures and the documented
table names; they have not been run on Dune** (no Dune access from the repository tooling). Run each once in
the Dune editor and compare against `remediation/evidence/vault-yield/events.csv`, which decodes the same
logs independently: it holds 6 events, 3 `FeesProcessed`, 2 `PairCheckpoint`, 1 `LiquidityRebalanced`.

Exchange-rate history is **not an event**, so it comes from the recorder: upload
`remediation/evidence/vault-yield/snapshots.csv` (Upload Data in the Dune UI, or the Tables API for scheduled
updates) as a table, e.g. `vault_snapshots`. Replace `YOURTEAM` below with your Dune team name.

Constants: vault `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f`, pair id (`keccak("NVDA/USDG")`)
`0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06`.

## 1. Fees collected (vault event `FeesProcessed`)

```sql
SELECT
  block_time,
  tx_hash,
  bytearray_to_uint256(bytearray_substring(data, 1, 32))  / 1e18 AS nvda_fees,
  bytearray_to_uint256(bytearray_substring(data, 33, 32)) / 1e6  AS usdg_fees,
  bytearray_to_uint256(bytearray_substring(data, 65, 32)) / 1e18 AS nvda_to_reserve,
  bytearray_to_uint256(bytearray_substring(data, 97, 32)) / 1e6  AS usdg_to_reserve
FROM robinhood.logs
WHERE contract_address = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f
  AND topic0 = 0xba110988b967a943bf5656448361e5a0882318946fd68fd198a9ea67faefa525
  AND topic1 = 0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
ORDER BY block_time
```

## 2. Impermanent-loss / PnL per checkpoint (`PairCheckpoint`)

`pnlUSDG` is a signed 1e18 USD value; negative is loss recognised by the vault (shared by both sides).

```sql
SELECT
  block_time,
  tx_hash,
  bytearray_to_uint256(bytearray_substring(data, 1, 32))  / 1e18 AS stock_assets_nvda,
  bytearray_to_uint256(bytearray_substring(data, 33, 32)) / 1e6  AS usdg_assets,
  bytearray_to_uint256(bytearray_substring(data, 65, 32)) / 1e18 AS benchmark_usd,
  bytearray_to_int256(bytearray_substring(data, 97, 32))  / 1e18 AS pnl_usd
FROM robinhood.logs
WHERE contract_address = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f
  AND topic0 = 0x89435eb942d7fca3abf983fcf2b8e2b10331d832dcf9cb033b38fd800b02e379
  AND topic1 = 0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
ORDER BY block_time
```

## 3. LP deployments (`LiquidityRebalanced`)

```sql
SELECT
  block_time,
  tx_hash,
  bytearray_to_uint256(bytearray_substring(data, 1, 32))  / 1e18 AS nvda_used,
  bytearray_to_uint256(bytearray_substring(data, 33, 32)) / 1e6  AS usdg_used,
  bytearray_to_uint256(bytearray_substring(data, 65, 32))        AS liquidity_added
FROM robinhood.logs
WHERE contract_address = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f
  AND topic0 = 0x25686835ff59c0d0c24e8d2a01e3a337959b6a40fb47bc1dcd3ff366f4e00537
  AND topic1 = 0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06
ORDER BY block_time
```

## 4. Supplier APY from realised exchange-rate growth (uploaded snapshots)

Trailing 24 hours per market. Annualised with the actual elapsed seconds. Show the window next to the number,
and show "n/a" when there is less than an hour of history.

```sql
WITH s AS (
  SELECT from_unixtime(timestamp) AS t, timestamp,
         CAST(pnvda_exchange_rate AS double) AS pnvda_rate,
         CAST(pusdg_exchange_rate AS double) AS pusdg_rate
  FROM dune.YOURTEAM.vault_snapshots
),
w AS (
  SELECT max(timestamp) AS now_ts FROM s
),
pair AS (
  SELECT
    (SELECT pnvda_rate FROM s, w WHERE s.timestamp = w.now_ts)                      AS n_now,
    (SELECT pnvda_rate FROM s, w WHERE s.timestamp >= w.now_ts - 86400 ORDER BY s.timestamp LIMIT 1) AS n_then,
    (SELECT pusdg_rate FROM s, w WHERE s.timestamp = w.now_ts)                      AS u_now,
    (SELECT pusdg_rate FROM s, w WHERE s.timestamp >= w.now_ts - 86400 ORDER BY s.timestamp LIMIT 1) AS u_then,
    (SELECT now_ts FROM w)                                                           AS now_ts,
    (SELECT s.timestamp FROM s, w WHERE s.timestamp >= w.now_ts - 86400 ORDER BY s.timestamp LIMIT 1) AS then_ts
)
SELECT
  (now_ts - then_ts) / 3600.0 AS window_hours,
  CASE WHEN now_ts - then_ts >= 3600 THEN (power(n_now / n_then, 31536000.0 / (now_ts - then_ts)) - 1) * 100 END AS pnvda_apy_pct,
  CASE WHEN now_ts - then_ts >= 3600 THEN (power(u_now / u_then, 31536000.0 / (now_ts - then_ts)) - 1) * 100 END AS pusdg_apy_pct
FROM pair
```

## Caveats worth stating on the dashboard

* The LP position is currently closed, so vault fee yield is 0 until allocation is reopened and rebalanced.
* All history to date is one ~18 hour window on a roughly $4 position. Do not headline an annualised figure.
* `PairCheckpoint.pnlUSDG` and `ledger.cumulativeLossUSDG` are the vault's recognised loss, already reflected in the
  pToken exchange rate, so supplier yield is the exchange-rate growth, not fees alone.
