#!/usr/bin/env python3
"""Inspect a FigureSpec and print the deterministic cfizz render request."""

import argparse
import json
from pathlib import Path

from cfizz.agent import CfizzRenderAdapter, DataInspector, FigureSpecValidator


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="Path to a FigureSpec JSON file")
    parser.add_argument("--output-dir", default="agent_output", help="Allowed render output directory")
    parser.add_argument("--render", action="store_true", help="Render instead of only printing the request")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    spec_path = Path(args.spec).expanduser().resolve()
    with spec_path.open("r", encoding="utf-8") as handle:
        spec = json.load(handle)

    inspector = DataInspector(allowed_roots=[str(project_root), str(spec_path.parent)])
    validator = FigureSpecValidator(inspector)
    adapter = CfizzRenderAdapter(validator, args.output_dir)

    if args.render:
        result = adapter.render(spec)
        print(json.dumps({
            "success": result.success,
            "artifacts": result.artifacts,
            "error": result.error,
        }, ensure_ascii=False, indent=2))
        return 0 if result.success else 1

    request = adapter.build_request(spec)
    print(json.dumps(request.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
