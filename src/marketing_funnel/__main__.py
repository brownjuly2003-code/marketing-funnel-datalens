"""Generate or analyze local JSON, writing a new reproducible artifact directory."""

import argparse
import hashlib
import json
from pathlib import Path

from .core import analyze
from .sample import generate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--size", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20261031)
    args = parser.parse_args()
    if not 1 <= args.size <= 10000:
        parser.error("--size must be between 1 and 10000")
    if args.out.exists():
        parser.error("--out must be a new directory; existing evidence is preserved")
    try:
        data = (
            json.loads(args.input.read_text(encoding="utf-8"))
            if args.input
            else generate(args.size, args.seed)
        )
        report = analyze(data["events"], data["spend"])
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    payloads = {"inputs.json": data, "report.json": report}
    encoded = {
        name: (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
            "utf-8"
        )
        for name, value in payloads.items()
    }
    manifest = {
        "schema_version": 1,
        "mode": "input" if args.input else "synthetic",
        "seed": None if args.input else args.seed,
        "size": None if args.input else args.size,
        "sha256": {name: hashlib.sha256(value).hexdigest() for name, value in encoded.items()},
    }
    args.out.mkdir(parents=True)
    for name, value in encoded.items():
        (args.out / name).write_bytes(value)
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps({"out": str(args.out), "files": sorted(encoded)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
