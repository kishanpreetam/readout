"""Command line: `python -m readout <step>`; `python -m readout all` runs every step in order."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from . import config

STEPS = ["fetch", "load", "resolve", "warehouse", "evaluate", "extract", "bench", "report"]


def fetch() -> None:
    from . import ctgov, sec

    ctgov.fetch()
    sec.fetch()  # stops cleanly and records the reason if SEC refuses the request
    manifest_summary()


def manifest_summary() -> dict:
    """Write results/data_manifest.json: every downloaded file with its URL, size and checksum."""
    files = []
    if config.MANIFEST.exists():
        for line in config.MANIFEST.read_text().splitlines():
            if line.strip():
                files.append(json.loads(line))
    summary = {
        "snapshot": json.loads(config.SNAPSHOT.read_text()) if config.SNAPSHOT.exists() else None,
        "files": len(files),
        "bytes_uncompressed": sum(f["bytes"] for f in files),
        "bytes_stored_gzip": sum(f["bytes_stored_gz"] for f in files),
        "sec_access": json.loads((config.RESULTS / "sec_access.json").read_text())
        if (config.RESULTS / "sec_access.json").exists() else None,
        "listing": [{k: f[k] for k in ("url", "path", "bytes", "bytes_stored_gz", "sha256", "fetched_at")}
                    for f in files],
    }
    config.RESULTS.mkdir(parents=True, exist_ok=True)
    (config.RESULTS / "data_manifest.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def load() -> None:
    from . import load as loader

    print("load:", loader.load_ctgov(), loader.load_sec())


def resolve() -> None:
    from . import linking, resolve as resolver

    print("resolve:", {k: v for k, v in resolver.run().items() if k != "largest_organizations"})
    print("sec link:", linking.run())


def warehouse() -> None:
    env = dict(os.environ, READOUT_DUCKDB=str(config.WAREHOUSE_DB), DBT_SEND_ANONYMOUS_USAGE_STATS="false")
    dbt = str(config.ROOT / ".venv" / "bin" / "dbt")
    args = ["--project-dir", str(config.DBT_DIR), "--profiles-dir", str(config.DBT_DIR),
            "--vars", json.dumps({"as_of_date": config.as_of_date()})]
    for cmd in (["build"], ["docs", "generate"]):
        run = subprocess.run([dbt, *cmd, *args], env=env, cwd=config.DBT_DIR, capture_output=True, text=True)
        tail = "\n".join(run.stdout.strip().splitlines()[-2:])
        print(f"dbt {' '.join(cmd)}:\n{tail}")
        if run.returncode != 0:
            print(run.stdout[-3000:])
            raise SystemExit(f"dbt {' '.join(cmd)} failed")
        if cmd == ["build"]:  # docs generate overwrites run_results.json, so summarize the build first
            results = json.loads((config.DBT_DIR / "target" / "run_results.json").read_text())
            summary: dict[str, dict[str, int]] = {}
            for r in results["results"]:
                kind = r["unique_id"].split(".")[0]
                summary.setdefault(kind, {})
                summary[kind][r["status"]] = summary[kind].get(r["status"], 0) + 1
            warnings = [r["unique_id"].split(".")[2] for r in results["results"] if r["status"] == "warn"]
            (config.RESULTS / "dbt_build.json").write_text(json.dumps(
                {"as_of_date": config.as_of_date(), "by_kind": summary, "warnings": warnings}, indent=2) + "\n")


def evaluate() -> None:
    from . import resolution_eval

    m = resolution_eval.run()
    print("resolution eval:", {k: m["gold_groups"][k] for k in
                               ("name_precision_micro", "name_recall_micro", "trial_recall_micro")})


def extract() -> None:
    from . import extract as extractor

    r = extractor.run()
    print("extraction:", {"baseline_f1": {f: r["baseline"][f]["f1"] for f in ("conditions", "interventions")},
                          "llm_f1": {f: r["llm"][f]["f1"] for f in ("conditions", "interventions")} if r["llm"] else None})


def bench() -> None:
    from . import llm, qa

    if llm.backend() == "none" and not llm.CACHE_DIR.exists() and (qa.OUT / "predictions.jsonl").exists():
        print("bench: no model available; re-scoring the saved predictions")
        print(qa.score())
        return
    print("bench:", json.dumps(qa.run()["variants"]))


def rescore() -> None:
    """Recompute every hand-label metric from the label files, with no network and no model calls."""
    from . import extract as extractor, qa, resolution_eval

    resolution_eval.run()
    qa.score()
    path = extractor.OUT / "metrics.json"
    if path.exists():
        m = json.loads(path.read_text())
        m["hand_review_of_sampled_disagreements"] = extractor._review_summary()
        path.write_text(json.dumps(m, indent=2) + "\n")
    report()


def report() -> None:
    from . import report as reporter

    print("report:", reporter.build())


def sites_fetch() -> None:
    from . import sites

    sites.fetch()


def sites_load() -> None:
    from . import sites

    print("sites load:", sites.load())


def sites_build() -> None:
    from . import sites

    print("sites build:", json.dumps(sites.build()))


# Trial site locations for Readout Globe (`make sites`). `sites` runs all three in order, after `warehouse`.
SITE_STEPS = {"sites-fetch": sites_fetch, "sites-load": sites_load, "sites-build": sites_build}


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__ + "\nSteps: " + ", ".join(STEPS + ["all", "rescore", "manifest", "as-of", "sites", *SITE_STEPS]))
        return
    step = argv[0]
    if step == "as-of":
        print(config.as_of_date())
        return
    if step == "sites" or step in SITE_STEPS:
        for name, run in SITE_STEPS.items():
            if step in ("sites", name):
                run()
        return
    if step == "rescore":
        rescore()
        return
    if step == "manifest":
        print(json.dumps({k: v for k, v in manifest_summary().items() if k != "listing"}, indent=1))
        return
    todo = STEPS if step == "all" else [step]
    for s in todo:
        if s not in STEPS:
            raise SystemExit(f"unknown step {s!r}; choose from {', '.join(STEPS)} or all")
        globals()[s]()


if __name__ == "__main__":
    main(sys.argv[1:])
