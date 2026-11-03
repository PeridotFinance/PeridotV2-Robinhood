"""Parse worker run logs into redacted JSONL + phase summaries. Drops raw 'output' (keeps only txHash), truncates detail to the revert selector."""
import json,re,sys,datetime as dt,collections
L=open(sys.argv[1]).read().splitlines()
rows=[]
for l in L:
    m=re.match(r'range-keeper (\S+) (.*)',l)
    j=json.loads(m.group(2))
    r={'t':m.group(1)[:19]+'Z'}
    r['unix']=int(dt.datetime.fromisoformat(m.group(1)[:19]).replace(tzinfo=dt.timezone.utc).timestamp())
    for k in ('status','simulation','rebalance','action','function','gasPriceWei','step','gasBalanceWei','gasReady','holdsKeeperRole','executionEnabled','code','errorType','reason','consecutive'):
        if k in j: r[k]=j[k]
    if 'detail' in j:
        s=re.search(r'data: \\?"(0x[0-9a-fA-F]+)',j['detail'])
        r['revert']=s.group(1)[:10] if s else None
        if not s: r['detailShort']=re.sub(r'0x[0-9a-fA-F]{40,}','0x<long-hex-redacted>',j['detail'])[:160]
    if 'output' in j:
        s=re.search(r'"transactionHash":"(0x[0-9a-f]{64})',j['output'])
        r['txHash']=s.group(1) if s else None
        if not s: r['outputShort']=re.sub(r'0x[0-9a-fA-F]{40,}','0x<long-hex-redacted>',j['output'])[:200]
    rows.append(r)
with open('run-logs-redacted.jsonl','w') as f:
    for r in rows: f.write(json.dumps(r)+'\n')
print(len(rows),rows[0]['t'],rows[-1]['t'])
