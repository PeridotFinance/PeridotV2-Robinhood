#!/usr/bin/env python3
"""Verify the frozen Robinhood inputs; optionally reproduce archived bytecode."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts/robinhood-vaults"
ORIGINAL_PERIDOT = "/Users/joshua/Peridot/peridot-contracts-2-5/"


def source_path(source):
    """Resolve archived source-unit names without reading outside this clone."""
    if source.startswith(ORIGINAL_PERIDOT):
        path = ROOT / "contracts/peridot-contracts-2-5" / source.removeprefix(ORIGINAL_PERIDOT)
    else:
        path = CONTRACTS / source
    path = path.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise RuntimeError("Archived source is outside this snapshot: " + source)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify():
    snapshot = json.loads((ROOT / "snapshot/sources.json").read_text())
    for name, entry in snapshot["files"].items():
        path = ROOT / name
        if not path.is_file() or digest(path) != entry["sha256"]:
            raise RuntimeError("Frozen source changed or missing: " + name)
    for filename, key in [
        ("robinhood-mainnet.margin-user-runner-validation.json", "sourceSha256"),
        ("robinhood-mainnet.margin-5x-validation.json", "sourceHashes"),
    ]:
        original = json.loads((CONTRACTS / "deployments" / filename).read_text())
        for name, expected in original[key].items():
            if digest(CONTRACTS / name) != expected:
                raise RuntimeError("Original deployment input differs: " + name)
        print(f"Original {filename}: {len(original[key])} inputs match", flush=True)
    artifacts = json.loads((ROOT / "snapshot/artifacts.json").read_text())
    for name, entry in artifacts.items():
        if digest(ROOT / entry["path"]) != entry["sha256"]:
            raise RuntimeError("Archived artifact differs: " + name)
        artifact = json.loads((ROOT / entry["path"]).read_text())
        for source in artifact["metadata"]["sources"]:
            path = source_path(source)
            if path.relative_to(ROOT.resolve()).as_posix() not in snapshot["files"]:
                raise RuntimeError("Archived source is not pinned: " + source)
    frontend = CONTRACTS / "frontend/margin-mainnet"
    manifest = json.loads((frontend / "manifest.json").read_text())
    for entry in manifest["artifacts"].values():
        if digest(frontend / entry["abiFile"]) != entry["abiSha256"]:
            raise RuntimeError("Frontend ABI differs: " + entry["abiFile"])
    print(f"Frozen snapshot: {len(snapshot['files'])} files, "
          f"{len(artifacts)} artifacts and 16 ABIs match", flush=True)
    return artifacts


def compiler():
    version = "0.8.26"
    candidates = [os.environ.get("SOLC"), shutil.which("solc")]
    candidates += [str(Path.home() / suffix / version / ("solc-" + version))
                   for suffix in (".svm", "Library/Application Support/svm", ".local/share/svm")]
    for candidate in filter(None, candidates):
        if Path(candidate).is_file():
            output = subprocess.check_output([candidate, "--version"], text=True)
            if "0.8.26+commit.8a97fa7a" in output:
                return candidate
    raise RuntimeError("Solc 0.8.26 required. Run make build with Foundry, or set SOLC to that compiler.")


def reproduce(artifacts):
    groups = {}
    for name, entry in artifacts.items():
        artifact = json.loads((ROOT / entry["path"]).read_text())
        settings = dict(artifact["metadata"]["settings"])
        target = settings.pop("compilationTarget")
        key = json.dumps(settings, sort_keys=True)
        group = groups.setdefault(key, {"settings": settings, "sources": {}, "targets": []})
        for source in artifact["metadata"]["sources"]:
            # Preserve the source-unit name for identical metadata, but read only
            # the vendored bytes, even when the original Mac path still exists.
            group["sources"][source] = {"content": source_path(source).read_text()}
        source, contract = next(iter(target.items()))
        group["targets"].append((name, source, contract, artifact))
    executable = compiler()
    results = []
    for index, group in enumerate(groups.values(), 1):
        settings = group["settings"]
        settings["outputSelection"] = {}
        for _, source, contract, _ in group["targets"]:
            settings["outputSelection"].setdefault(source, {})[contract] = [
                "evm.bytecode.object", "evm.deployedBytecode.object", "abi"]
        request = {"language": "Solidity", "sources": group["sources"], "settings": settings}
        print(f"Recompiling group {index}/{len(groups)}: {len(group['targets'])} artifacts", flush=True)
        # Files avoid pipe truncation/EAGAIN issues with large compiler output.
        with tempfile.TemporaryFile(mode="w+") as stdin, tempfile.TemporaryFile(mode="w+") as stdout:
            json.dump(request, stdin)
            stdin.seek(0)
            subprocess.run([executable, "--standard-json"], stdin=stdin, stdout=stdout,
                           check=True, cwd=CONTRACTS)
            stdout.seek(0)
            output = json.load(stdout)
        errors = [e["formattedMessage"] for e in output.get("errors", []) if e["severity"] == "error"]
        if errors:
            raise RuntimeError("\n".join(errors))
        for name, source, contract, archived in group["targets"]:
            actual = output["contracts"][source][contract]
            for kind in ("bytecode", "deployedBytecode"):
                expected = archived[kind]["object"].removeprefix("0x")
                if actual["evm"][kind]["object"] != expected:
                    raise RuntimeError(f"Recompiled {kind} differs from archived artifact: {name}")
            # Foundry reorders top-level ABI entries; tuple/input order is preserved.
            normalize_abi = lambda entries: sorted(json.dumps(e, sort_keys=True) for e in entries)
            if normalize_abi(actual["abi"]) != normalize_abi(archived["abi"]):
                raise RuntimeError("Recompiled ABI differs: " + name)
            results.append(name)
            print("Exact creation/runtime/ABI match: " + name, flush=True)
    target = ROOT / "snapshot/reproduction-result.json"
    target.write_text(json.dumps({
        "compiler": "0.8.26+commit.8a97fa7a",
        "status": "EXACT_ARCHIVED_ARTIFACT_MATCH",
        "matches": results,
        "scope": "Creation and runtime templates including CBOR metadata; libraries and constructor immutables remain as in archived artifacts. On-chain linkage is established separately by historical deployment verification records."
    }, indent=2) + "\n")
    print(f"Reproduced {len(results)} archived artifacts exactly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compile", action="store_true", help="Recompile with archived compiler settings")
    args = parser.parse_args()
    entries = verify()
    if args.compile:
        reproduce(entries)
