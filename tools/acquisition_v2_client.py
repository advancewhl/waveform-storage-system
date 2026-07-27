"""Operational health, status, and explicit cancellation client for v2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from waveform_storage.transport.zmq_transport import ZmqTransport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        default=os.environ.get(
            "WAVEFORM_CANDIDATE_ZMQ_ENDPOINT",
            "tcp://127.0.0.1:5001",
        ),
    )
    parser.add_argument("--timeout-ms", type=int, default=3000)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health")
    status = subparsers.add_parser("status")
    status.add_argument("request_id", type=_canonical_uuid)
    cancel = subparsers.add_parser("cancel")
    cancel.add_argument("request_id", type=_canonical_uuid)
    cancel.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="confirm cancellation of an active capture task",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    transport = ZmqTransport(
        args.endpoint,
        timeout_ms=args.timeout_ms,
    )
    if args.command == "health":
        response = transport.health(str(uuid4()))
    elif args.command == "status":
        response = transport.get_status(args.request_id)
    else:
        response = transport.cancel(args.request_id)
    print(
        json.dumps(
            asdict(response),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if response.accepted else 1


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a UUID") from exc
    if str(parsed) != value.lower():
        raise argparse.ArgumentTypeError("must use canonical UUID form")
    return str(parsed)


if __name__ == "__main__":
    raise SystemExit(main())
