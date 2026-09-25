"""Validate and score the full no-Jev given, random, and hybrid outputs.

The hybrid is the given-order pass over the full cohort plus the separately
executed random-norepeat fallback over given-order misses. The full random arm
is reported independently and is not used to fill the hybrid map.
"""

import argparse
import glob
import hashlib
import json
import math
from pathlib import Path

METHOD = "LeanHammerComparison.cpu"
LIMIT = 16_000_000_000


def read(path):
    return json.loads(Path(path).read_text())


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exact_mcnemar(jev_only, arm_only):
    n = jev_only + arm_only
    if not n:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(jev_only, arm_only) + 1)) / 2**n)


def load_arm(pattern, stats_path=None):
    files = sorted(glob.glob(pattern))
    if not files:
        raise ValueError(f"no trial files matched {pattern}")
    outcomes, visited = {}, set()
    for trial_path in files:
        run_dir = Path(trial_path).parent
        manifest = read(run_dir / "run.json")
        if manifest.get("status") != "complete":
            raise ValueError(f"incomplete run: {run_dir.name}")
        if (manifest.get("methods") != [METHOD] or manifest.get("configOverrides") != {}
                or manifest.get("guidance") != "mock" or manifest.get("threads") != 2
                or manifest.get("outerHeartbeats") != 200000):
            raise ValueError(f"unexpected method, configuration, or guidance mode: {run_dir.name}")
        if read(run_dir / "usage.json").get("attempts") != 0:
            raise ValueError(f"a no-Jev arm made ranking API calls: {run_dir.name}")
        cap = manifest["resources"]["initial"]
        if (cap.get("mode") != "cgroup" or cap.get("memoryMax", LIMIT + 1) > LIMIT
                or cap.get("swapMax") != "0"):
            raise ValueError(f"trial was not inside a verified 16 GiB zero-swap cgroup: {run_dir.name}")
        verification = read(run_dir / "verification.json")
        if verification.get("status") != "complete":
            raise ValueError(f"incomplete proof replay: {run_dir.name}")
        replay_path = run_dir / verification["directory"] / "replay.jsonl"
        replayed = set()
        for row in load_jsonl(replay_path):
            if row["method"] == METHOD and row.get("verified"):
                replayed.add(row["site"])
        for row in load_jsonl(trial_path):
            if row.get("method") != METHOD:
                raise ValueError(f"unexpected method in {trial_path}")
            site = row["site"]
            if site in outcomes:
                raise ValueError(f"duplicate trial for {site}")
            if not isinstance(row.get("solved"), bool) or not isinstance(row.get("onTime"), bool):
                raise ValueError(f"missing solved/onTime booleans for {site}")
            solved = row["solved"]
            verified = site in replayed
            if solved != verified:
                raise ValueError(f"trial/replay mismatch for {site}")
            outcomes[site] = {"solved": solved, "onTime": row["onTime"], "verified": verified}
            visited.add(site)
    if stats_path:
        rows = load_jsonl(stats_path)
        if not rows:
            raise ValueError(f"random-norepeat produced no continuation rank records: {stats_path}")
        for row in rows:
            n, order = row["candidates"], row["order"]
            if (row["site"] not in outcomes or sorted(order) != list(range(n))
                    or row["fresh"] + row["excluded"] != n):
                raise ValueError(f"invalid random-norepeat rank record for {row.get('site')}")
    return outcomes


def load_jev(path):
    records = {}
    for row in load_jsonl(path):
        if row.get("method") != METHOD:
            continue
        site = row["site"]
        if site in records:
            raise ValueError(f"duplicate Jev record for {site}")
        stats = row.get("stats") or {}
        records[site] = {
            "solved": bool(row.get("rawSolved")),
            "onTimeVerified": bool(row.get("onTimeVerified")),
            "stateRankCalls": int(stats.get("stateRankCalls") or 0),
        }
    return records


def metrics(outcomes, sites):
    rows = [outcomes[site] for site in sites]
    return {
        "sites": len(rows),
        "rawSolved": sum(row["solved"] for row in rows),
        "rawVerified": sum(row["verified"] for row in rows),
        "onTimeVerified": sum(row["solved"] and row["onTime"] and row["verified"] for row in rows),
    }


def compare(jev, arm, sites):
    jev_only = sum(jev[site]["solved"] and not arm[site]["solved"] for site in sites)
    arm_only = sum(arm[site]["solved"] and not jev[site]["solved"] for site in sites)
    return {"jevOnly": jev_only, "armOnly": arm_only,
            "twoSidedExactMcNemarP": exact_mcnemar(jev_only, arm_only)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True, help="re-admitted full plan directory")
    parser.add_argument("--jev", type=Path, required=True, help="published full CPU trial JSONL")
    parser.add_argument("--given", required=True, help="glob for six full given-order trials.jsonl files")
    parser.add_argument("--random", required=True, help="glob for six full random trials.jsonl files")
    parser.add_argument("--fallback", required=True, help="glob for random fallback trials on misses")
    parser.add_argument("--random-stats", type=Path, required=True)
    parser.add_argument("--fallback-stats", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials-output", type=Path, required=True)
    args = parser.parse_args()

    plan = read(args.plan / "dataset.json")
    sites = [row["site"] for row in plan["sites"]]
    cohort = set(sites)
    if len(cohort) != 1024:
        raise ValueError("the plan must contain exactly 1,024 unique sites")
    jev, given = load_jev(args.jev), load_arm(args.given)
    random_arm = load_arm(args.random, args.random_stats)
    fallback = load_arm(args.fallback, args.fallback_stats)
    for label, outcomes in [("Jev", jev), ("given-order", given), ("random", random_arm)]:
        if set(outcomes) != cohort:
            raise ValueError(f"{label} does not cover the 1,024-site cohort exactly")
    if set(jev) != cohort:
        raise ValueError("published Jev records do not cover the same cohort")

    misses = {site for site in sites if not given[site]["solved"]}
    if set(fallback) != misses:
        raise ValueError("hybrid fallback trials must cover exactly the given-order misses")
    hybrid = {}
    for site in sites:
        first = given[site]
        if first["solved"]:
            hybrid[site] = first
        else:
            hybrid[site] = fallback[site]
    full_random_union = {site: {
        "solved": given[site]["solved"] or random_arm[site]["solved"],
        "onTime": (given[site]["solved"] and given[site]["onTime"])
                  or (random_arm[site]["solved"] and random_arm[site]["onTime"]),
        "verified": given[site]["verified"] or random_arm[site]["verified"],
    } for site in sites}

    asked = [site for site in sites if jev[site]["stateRankCalls"] > 0]
    if len(asked) != 652:
        raise ValueError(f"expected 652 Jev-asked goals, found {len(asked)}")
    arm_maps = {"given-order": given, "random": random_arm, "hybrid": hybrid}
    counts = {name: metrics(values, sites) for name, values in arm_maps.items()}
    asked_counts = {name: metrics(values, asked) for name, values in arm_maps.items()}
    jev_counts = {
        "all": {"sites": len(sites), "rawSolved": sum(jev[s]["solved"] for s in sites),
                "onTimeVerified": sum(jev[s]["onTimeVerified"] for s in sites)},
        "asked": {"sites": len(asked), "rawSolved": sum(jev[s]["solved"] for s in asked),
                  "onTimeVerified": sum(jev[s]["onTimeVerified"] for s in asked)},
    }
    comparisons = {
        name: {"all": compare(jev, values, sites), "asked": compare(jev, values, asked)}
        for name, values in arm_maps.items()
    }
    consistency_sites = sorted(misses)
    consistency_flips = [site for site in consistency_sites
                         if random_arm[site]["solved"] != fallback[site]["solved"]]
    consistency_error_flags = [site for site in consistency_sites
                               if random_arm[site]["verified"] != fallback[site]["verified"]]
    hybrid_difference = [site for site in sites
                         if hybrid[site]["solved"] != full_random_union[site]["solved"]]

    public_trials = []
    for site in sites:
        calls = jev[site]["stateRankCalls"]
        bucket = "never asked" if calls == 0 else f"{calls} call" + ("s" if calls != 1 else "")
        public_trials.append({
            "site": site,
            "jevStateRankCalls": calls,
            "jevBucket": bucket,
            "jevRawSolved": jev[site]["solved"],
            "givenOrderSolved": given[site]["solved"],
            "givenOrderOnTimeVerified": given[site]["onTime"] and given[site]["verified"],
            "randomSolved": random_arm[site]["solved"],
            "randomOnTimeVerified": random_arm[site]["onTime"] and random_arm[site]["verified"],
            "hybridSolved": hybrid[site]["solved"],
            "hybridOnTimeVerified": hybrid[site]["onTime"] and hybrid[site]["verified"],
            "hybridFallbackRun": site in fallback,
            "hybridFallbackSolved": fallback[site]["solved"] if site in fallback else None,
        })

    trials_payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in public_trials)
    summary = {
        "schema": 1,
        "status": "complete",
        "kind": "full-no-jev-ordering-ablation",
        "method": METHOD,
        "sites": len(sites),
        "cohortSha256": sha(args.plan / "dataset.json"),
        "publishedJev": jev_counts,
        "arms": counts,
        "askedSubset652": asked_counts,
        "pairedAgainstJevRawSolved": comparisons,
        "hybrid": {
            "definition": "given-order full cohort, then random-norepeat fallback on given-order misses",
            "fallbackSites": len(misses),
            "fallbackRawSolved": sum(fallback[s]["solved"] for s in misses),
            "fallbackMarginalSolved": sum(fallback[s]["solved"] for s in misses),
            "randomFullVsFallbackSolvedFlagFlipsOnMisses": len(consistency_flips),
            "randomFullVsFallbackReplayFlagDifferencesOnMisses": len(consistency_error_flags),
            "derivedGivenUnionStandaloneRandomRawSolved": sum(v["solved"] for v in full_random_union.values()),
            "executedHybridVsFullArmUnionSolvedFlagFlips": len(hybrid_difference),
        },
        "protocol": {
            "perRunMemoryLimitBytes": LIMIT,
            "perRunSwapLimitBytes": 0,
            "batchOrderWithinEachArm": "sequential 0 through 5",
            "crossArmConcurrency": "given-order and standalone random may overlap; hybrid fallback starts after given-order completes",
            "deviation": "separate bounded arms may overlap; the upstream guide requested no concurrent heavy job",
            "timingClaims": "not made; elapsed times are retained in local raw outputs and omitted here",
        },
        "limitations": [
            "The cohort is a stratified, size-filtered Mathlib sample, not uniform sampling of all Mathlib goals.",
            "The five source-incompatible modules were excluded before sampling; no proof-trial failures were dropped.",
            "The hybrid reuses the completed given-order pass and executes random fallback only on its misses.",
            "The random arm and hybrid fallback are separate executions; report any per-site disagreement.",
            "Jev ranking latency is charged to its original deadline, while local no-Jev ranking is near-zero cost.",
        ],
        "publicTrialsSha256": hashlib.sha256(trials_payload.encode()).hexdigest(),
    }
    for path in (args.output, args.trials_output):
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    args.trials_output.write_text(trials_payload)
    print(json.dumps({"sites": len(sites), "publishedJev": jev_counts,
                      "arms": counts, "askedSubset652": asked_counts,
                      "hybrid": summary["hybrid"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
