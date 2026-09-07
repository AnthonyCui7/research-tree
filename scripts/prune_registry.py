"""Delete registry manifests that no kept image needs.

Every deploy pushes one image per commit and retags `buildcache`, which leaves
the previous cache manifest (about 1.8 GB) untagged in the registry, where ACR
keeps it forever. The Basic tier includes 10 GiB, so the registry fills after a
day of deploys. This keeps the newest `--keep` SHA-tagged images (the running
image and its rollback targets), the current `buildcache`, and every manifest
those reference (a buildx push is an index over the platform image and its
attestation), and deletes everything else in the repository.

Runs from the deploy job with the CI identity (AcrDelete on the registry) and
from a laptop with the operator's login. `--dry-run` only prints the plan.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


def az(*args: str) -> object:
    result = subprocess.run(
        ["az", *args, "--output", "json"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        sys.exit(f"az {' '.join(args)} failed:\n{result.stderr.strip()[-600:]}")
    return json.loads(result.stdout or "null")


def usage_gb(registry: str) -> float:
    for row in az("acr", "show-usage", "--name", registry)["value"]:
        if row["name"] == "Size":
            return row["currentValue"] / 1e9
    return float("nan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--registry", default="researchtree7")
    parser.add_argument("--repository", default="research-tree")
    parser.add_argument("--keep", type=int, default=3, help="newest SHA-tagged images to keep")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, delete nothing")
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")

    manifests = az(
        "acr", "manifest", "list-metadata",
        "--registry", args.registry, "--name", args.repository, "--orderby", "time_desc",
    )
    tagged = [m for m in manifests if m.get("tags")]
    images = [m for m in tagged if "buildcache" not in m["tags"]]
    caches = [m for m in tagged if "buildcache" in m["tags"]]
    kept = images[: args.keep] + caches
    keep_digests = {m["digest"] for m in kept}
    for manifest in kept:
        body = az(
            "acr", "manifest", "show",
            "--registry", args.registry, "--name", f"{args.repository}@{manifest['digest']}",
        )
        for child in body.get("manifests", []) if isinstance(body, dict) else []:
            keep_digests.add(child["digest"])

    victims = [m for m in manifests if m["digest"] not in keep_digests]
    print(f"registry {args.registry}: {usage_gb(args.registry):.2f} GB used")
    print("keeping", ", ".join(m["tags"][0][:12] for m in kept), f"({len(keep_digests)} manifests)")
    nominal = sum(m.get("imageSize") or 0 for m in victims) / 1e9
    verb = "would delete" if args.dry_run else "deleting"
    print(f"{verb} {len(victims)} manifests ({nominal:.2f} GB before shared layers):")
    for manifest in victims:
        label = (manifest.get("tags") or ["untagged"])[0][:12]
        size = (manifest.get("imageSize") or 0) / 1e6
        print(f"  {label:12} {size:7.0f} MB  {manifest['createdTime'][:16]}")
    if args.dry_run or not victims:
        return 0

    failures = 0
    for manifest in victims:
        result = subprocess.run(
            [
                "az", "acr", "repository", "delete", "--name", args.registry,
                "--image", f"{args.repository}@{manifest['digest']}", "--yes", "--output", "none",
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            failures += 1
            print(f"  failed {manifest['digest'][:19]}: {result.stderr.strip()[-200:]}")
    print(f"registry {args.registry}: {usage_gb(args.registry):.2f} GB used after pruning")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
