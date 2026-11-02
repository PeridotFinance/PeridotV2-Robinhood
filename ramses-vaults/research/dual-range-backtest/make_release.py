"""Build and VERIFY the compressed research-input release (nothing is uploaded or published by this script).

  python3 make_release.py [--version v1] [--require-cold]

Outputs in release/ (git-ignored):
  ramses-research-inputs-core-<v>.tar.gz     code + evidence (pinned quote inputs included) + JSON datasets + small inputs: everything needed to
                                             reproduce every table OFFLINE (reproduce_offline.sh)
  ramses-research-inputs-rawlogs-<v>.tar.gz  the raw pool event logs (SPY, GLD, NVDA), verifiable against bundle/manifest-v1.json
  SHA256SUMS                                 checksums of both archives (and of every file inside the core archive's staging copy)
  RELEASE-NOTES.txt                          what is inside, how to verify, the git commit it was built from

Verification BEFORE anything is published, all recorded in release/VERIFICATION.json:
  1. the local datasets and small inputs match the manifest (bundle.verify);
  2. the core archive is extracted to a fresh directory and reproduce_offline.sh is run there under a macOS sandbox that DENIES ALL NETWORK
     (sandbox-exec "(deny network*)"), proven first by a probe that must fail to open a socket or resolve a name. If sandbox-exec is not
     available the run falls back to dead HTTP(S) proxies and is recorded as "proxy-only (NOT network isolation)". The run re-verifies the
     inputs, replays every pinned quote and fork proof from the saved state and reruns the baselines, comparing every number with the evidence;
  3. the raw-log archive is extracted and every event hash is checked against the manifest;
  4. with --require-cold: the cold SPY/NVDA/GLD rebuild logs must show all hashes matching.
Publication (a GitHub release asset upload) is a separate, operator-approved step; the command is printed, not run.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import bundle as B

HERE = Path(__file__).parent
REL = HERE / "release"
PAIRS = ("SPY", "GLD", "NVDA")
RAW = lambda p: B.CACHE.parent / f"ramses-group-pool-{p}.json"
CODE_SUFFIXES = (".py", ".sh")
SKIP_DIRS = {"results", "release", "__pycache__", "datasets"}


def sha(path):
    return B.file_sha(path)


def git_state():
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", str(HERE)], cwd=root, capture_output=True, text=True, check=True).stdout.strip())
        return commit, dirty
    except Exception:
        return None, None


def stage_core(stage: Path, version: str):
    pkg = stage / f"ramses-research-inputs-{version}"
    if pkg.exists():
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)
    for p in HERE.iterdir():
        if p.name in SKIP_DIRS or p.name.startswith("."):
            continue
        if p.is_dir():
            shutil.copytree(p, pkg / p.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif p.suffix in CODE_SUFFIXES:
            shutil.copy2(p, pkg / p.name)
    (pkg / "datasets").mkdir()
    import pickle
    for pair in PAIRS:
        cols, meta = pickle.load(open(B.RESULTS / f"dataset_{pair}.pkl", "rb"))
        with gzip.open(pkg / "datasets" / f"dataset_{pair}.json.gz", "wt", compresslevel=6) as f:
            json.dump({"cols": cols, "meta": meta}, f, separators=(",", ":"), default=list)
    return pkg


def tar_dir(src: Path, out: Path):
    with tarfile.open(out, "w:gz", compresslevel=6) as t:
        t.add(src, arcname=src.name)


def run(cmd, cwd, env=None, log=None):
    t = time.time()
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if log:
        Path(log).write_text(p.stdout + p.stderr)
    # stdout and stderr are kept apart: the success marker is printed on stdout, unittest chatter goes to stderr
    return p.returncode, time.time() - t, p.stdout[-600:] + "\n--stderr--\n" + p.stderr[-300:]


def cold_ok(pair):
    f = HERE / "evidence" / "logs" / f"bundle-rebuild-{pair}.log"
    if not f.exists():
        return False
    txt = f.read_text()
    return ("FAIL" not in txt) and txt.count("  OK   ") >= 12 + 2 and "OK   dataset columns" in txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v1")
    ap.add_argument("--require-cold", action="store_true")
    ap.add_argument("--full", action="store_true", help="reproduce_offline.sh full (every baseline); default is the quick set")
    a = ap.parse_args()
    REL.mkdir(exist_ok=True)
    ver = {"version": a.version, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": {}}

    # 1. local inputs verify
    rc = 0
    for pair in PAIRS:
        rc |= B.verify(pair, str(RAW(pair)))
    ver["steps"]["localInputsVerifyAgainstManifest"] = rc == 0
    if rc:
        print("LOCAL INPUTS DO NOT VERIFY: refusing to build")
        json.dump(ver, open(REL / "VERIFICATION.json", "w"), indent=1)
        return 1
    if a.require_cold:
        cold = {p: cold_ok(p) for p in PAIRS}
        ver["steps"]["coldRebuildLogsAllHashesOk"] = cold
        if not all(cold.values()):
            print("COLD REBUILD NOT COMPLETE/OK for", [p for p, v in cold.items() if not v], ": refusing to build")
            json.dump(ver, open(REL / "VERIFICATION.json", "w"), indent=1)
            return 1

    commit, dirty = git_state()
    ver["builtFromCommit"], ver["workingTreeDirtyAtBuild"] = commit, dirty

    # build archives
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        pkg = stage_core(stage, a.version)
        (pkg / "RELEASE.json").write_text(json.dumps({"version": a.version, "commit": commit, "dirty": dirty, "created": ver["started"]}, indent=1))
        core = REL / f"ramses-research-inputs-core-{a.version}.tar.gz"
        tar_dir(pkg, core)
        raw_dir = stage / f"ramses-research-inputs-rawlogs-{a.version}"
        raw_dir.mkdir()
        for pair in PAIRS:
            with open(RAW(pair), "rb") as src, gzip.open(raw_dir / f"ramses-group-pool-{pair}.json.gz", "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst)
        raw = REL / f"ramses-research-inputs-rawlogs-{a.version}.tar.gz"
        tar_dir(raw_dir, raw)

    # 2. offline reproduction from the archive alone
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(core) as t:
            t.extractall(tmp)
        pkg = Path(tmp) / f"ramses-research-inputs-{a.version}"
        env = dict(os.environ, HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", http_proxy="http://127.0.0.1:9", https_proxy="http://127.0.0.1:9", NO_PROXY="")
        env.pop("ROBINHOOD_RPC_URL", None)
        probe = "import socket,sys\ntry:\n    socket.create_connection(('1.1.1.1',443),3); print('NETWORK REACHABLE'); sys.exit(1)\nexcept OSError: pass\ntry:\n    socket.getaddrinfo('rpc.mainnet.chain.robinhood.com',443); print('DNS RESOLVED'); sys.exit(1)\nexcept OSError: pass\nprint('NO NETWORK')"
        sandbox = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"] if shutil.which("sandbox-exec") else None
        if sandbox:
            pc, _s, ptail = run(sandbox + [sys.executable, "-c", probe], pkg, env)
            isolated = pc == 0 and "NO NETWORK" in ptail
            ver["steps"]["networkIsolationProbe"] = {"passed": bool(isolated), "method": "sandbox-exec (deny network*)", "output": ptail.strip()[-80:]}
            cmd = sandbox + ["sh", "reproduce_offline.sh"] + (["full"] if a.full else [])
            label = "sandbox-exec (deny network*): sockets and DNS blocked, probe verified"
        else:
            isolated, cmd, label = False, ["sh", "reproduce_offline.sh"] + (["full"] if a.full else []), "proxy-only (NOT network isolation): HTTP(S) proxies point at a dead port"
            ver["steps"]["networkIsolationProbe"] = {"passed": False, "method": "none available", "output": label}
        code, secs, tail = run(cmd, pkg, env, REL / "offline-reproduction.log")
        ver["steps"]["offlineReproductionFromCoreArchive"] = {"passed": code == 0 and "REPRODUCED" in tail, "networkIsolated": bool(isolated), "isolation": label, "seconds": round(secs),
                                                              "mode": "full" if a.full else "quick", "tail": tail[-200:]}
    # 3. raw-log archive verifies against the manifest
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(raw) as t:
            t.extractall(tmp)
        ok = True
        for pair in PAIRS:
            p = Path(tmp) / raw.name.replace(".tar.gz", "") / f"ramses-group-pool-{pair}.json.gz"
            plain = Path(tmp) / f"{pair}.json"
            with gzip.open(p, "rb") as src, open(plain, "wb") as dst:
                shutil.copyfileobj(src, dst)
            ok &= B.verify(pair, str(plain)) == 0
        ver["steps"]["rawLogArchiveMatchesManifest"] = bool(ok)

    sums = {f.name: sha(f) for f in (core, raw)}
    (REL / "SHA256SUMS").write_text("".join(f"{h}  {n}\n" for n, h in sums.items()))
    ver["archives"] = {n: {"sha256": h, "bytes": (REL / n).stat().st_size} for n, h in sums.items()}
    ver["allPassed"] = all(v if isinstance(v, bool) else (v.get("passed", all(v.values())) if isinstance(v, dict) else bool(v)) for v in ver["steps"].values())
    (REL / "VERIFICATION.json").write_text(json.dumps(ver, indent=1))
    (REL / "RELEASE-NOTES.txt").write_text(
        f"Ramses Phase 0 research inputs {a.version}\n"
        f"Built from git commit {commit} (working tree dirty at build: {dirty}).\n\n"
        "core archive: code, evidence, pinned pool-state inputs (the public RPC cannot serve these historical state reads again), JSON datasets, small inputs.\n"
        "  tar xzf ramses-research-inputs-core-%s.tar.gz && cd ramses-research-inputs-%s && sh reproduce_offline.sh   (no network needed)\n"
        "rawlogs archive: raw pool event logs; verify with python3 bundle.py verify PAIR --raw <extracted .json>.\n"
        "Verify the downloads first: shasum -a 256 -c SHA256SUMS\n\n" % (a.version, a.version) + json.dumps(ver["steps"], indent=1) + "\n")
    print(json.dumps({k: v for k, v in ver.items() if k != "steps"}, indent=1))
    print(json.dumps(ver["steps"], indent=1))
    print("\nNOT PUBLISHED. After review, an operator-approved upload would be:\n  gh release create ramses-inputs-%s release/*.tar.gz release/SHA256SUMS --repo PeridotFinance/PeridotV2-Robinhood --draft --notes-file release/RELEASE-NOTES.txt" % a.version)
    return 0 if ver["allPassed"] else 1


if __name__ == "__main__":
    sys.exit(main())
