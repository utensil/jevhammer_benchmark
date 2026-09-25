"""Copy a batch dataset with only sites not solved by the given-order pass."""

import argparse
import copy
import hashlib
import json
from pathlib import Path


def read_trials(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        site = row["site"]
        if site in rows:
            raise ValueError(f"duplicate given-order trial for {site}")
        if not isinstance(row.get("solved"), bool):
            raise ValueError(f"missing boolean solved outcome for {site}")
        rows[site] = row
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("trials", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    source = json.loads(args.dataset.read_text())
    original_sites = source["sites"]
    outcomes = read_trials(args.trials)
    expected = {row["site"] for row in original_sites}
    if set(outcomes) != expected:
        raise ValueError("given-order trials do not cover this batch exactly")
    reduced = copy.deepcopy(source)
    reduced["sites"] = [row for row in original_sites if not outcomes[row["site"]]["solved"]]
    for key in source:
        if key != "sites" and reduced[key] != source[key]:
            raise AssertionError(f"unexpected change to {key}")
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(reduced, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "inputSites": len(original_sites),
        "givenSolved": len(original_sites) - len(reduced["sites"]),
        "fallbackSites": len(reduced["sites"]),
        "inputSha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "outputSha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
