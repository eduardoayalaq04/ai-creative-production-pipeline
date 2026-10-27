"""Run with python -m creative_pipeline; installation is unnecessary."""
import argparse
import json
import sys

from .core import PipelineError, _read, approve, plan, run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local approval-gated reference pipeline; mock JSON only, no media generation.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("campaign")
        command.add_argument("--out", required=True)
        if name == "run":
            command.add_argument("--provider", choices=("mock",), default="mock")
    approval = commands.add_parser("approve")
    approval.add_argument("run_directory")
    approval.add_argument("asset")
    approval.add_argument("--reviewer", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(_read(args.campaign), args.out)
        elif args.command == "run":
            result = run(_read(args.campaign), args.out, args.provider)
        else:
            result = approve(args.run_directory, args.asset, args.reviewer)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (PipelineError, OSError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
