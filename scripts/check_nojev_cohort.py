"""Check a re-admitted ablation plan against the checked-in 1,024-site cohort."""

import argparse
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text())


def digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def check(reference, candidate, expected=1024):
    ref = read(reference / "dataset.json")
    new = read(candidate / "dataset.json")
    for key in ("sites", "sources", "imports"):
        if ref[key] != new[key]:
            raise ValueError(f"re-admitted full cohort changed {key}")
    sites = [row["site"] for row in new["sites"]]
    owners = [row["declaration"] for row in new["sites"]]
    if len(sites) != expected or len(set(sites)) != expected or len(set(owners)) != expected:
        raise ValueError(f"expected {expected} distinct sites and owners")

    reference_sites = {row["site"] for row in ref["sites"]}
    seen = set()
    for batch in range(6):
        path = candidate / f"batch-{batch}.json"
        if not path.exists():
            raise ValueError(f"missing {path.name}")
        old_part = read(reference / path.name)
        new_part = read(path)
        for key in ("sites", "sources", "imports"):
            if old_part[key] != new_part[key]:
                raise ValueError(f"batch {batch} changed {key}")
        part_sites = [row["site"] for row in new_part["sites"]]
        if seen.intersection(part_sites):
            raise ValueError(f"batch {batch} overlaps an earlier batch")
        seen.update(part_sites)
    if seen != reference_sites:
        raise ValueError("the six batches do not cover the reference cohort exactly")
    print(json.dumps({
        "sites": len(sites),
        "batches": 6,
        "sitesSha256": digest(new["sites"]),
        "sourcesSha256": digest(new["sources"]),
        "importsSha256": digest(new["imports"]),
        "projectIdentityChanged": ref["project"] != new["project"],
    }, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="checked-in cohort directory")
    parser.add_argument("candidate", type=Path, help="re-admitted plan directory")
    args = parser.parse_args()
    check(args.reference, args.candidate)


if __name__ == "__main__":
    main()
