#!/usr/bin/env python3
"""Fetch a URL (read-only GET) into raw/<name> and append a manifest entry. usage: fetch.py NAME URL"""
import sys, json, hashlib, datetime, urllib.request, urllib.error, os, socket
_o = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _o(*a, **k) if r[0] == socket.AF_INET]  # avoid ~30 s IPv6 stall on this host
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
name, url = sys.argv[1], sys.argv[2]
here = os.path.dirname(os.path.abspath(__file__))
req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
status, body, err = None, b"", None
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        status, body = r.status, r.read()
except urllib.error.HTTPError as e:
    status, body, err = e.code, e.read(), str(e)
except Exception as e:
    err = repr(e)
open(os.path.join(here, "raw", name), "wb").write(body)
entry = {"name": name, "url": url, "retrievedUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
         "httpStatus": status, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "error": err}
mp = os.path.join(here, "manifest.json")
m = json.load(open(mp)) if os.path.exists(mp) else []
m = [x for x in m if x["name"] != name] + [entry]
json.dump(m, open(mp, "w"), indent=1)
print(json.dumps(entry))
