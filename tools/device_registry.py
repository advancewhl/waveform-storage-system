"""CLI for importing, validating, and generating device configuration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from waveform_storage.config.registry import (
    RegistryValidationError,
    import_legacy_registry,
    load_registry,
    render_legacy_json_lines,
    save_registry,
    write_legacy_json_lines,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import-legacy")
    import_parser.add_argument("--all", required=True, dest="all_path")
    import_parser.add_argument("--acquisition", required=True)
    import_parser.add_argument("--output", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--registry", required=True)

    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--registry", required=True)
    generate_parser.add_argument("--all-output", required=True)
    generate_parser.add_argument("--acquisition-output", required=True)

    check_parser = subparsers.add_parser("check-legacy")
    check_parser.add_argument("--registry", required=True)
    check_parser.add_argument("--all", required=True, dest="all_path")
    check_parser.add_argument("--acquisition", required=True)

    args = parser.parse_args()
    try:
        if args.command == "import-legacy":
            registry = import_legacy_registry(
                args.all_path,
                args.acquisition,
            )
            save_registry(registry, args.output)
            print(
                f"wrote {len(registry.devices)} devices to {args.output}"
            )
        elif args.command == "validate":
            registry = load_registry(args.registry)
            acquisition_count = sum(
                item.acquisition_enabled for item in registry.devices
            )
            print(
                f"valid registry: {len(registry.devices)} devices, "
                f"{acquisition_count} acquisition-enabled"
            )
        elif args.command == "generate":
            registry = load_registry(args.registry)
            write_legacy_json_lines(
                registry,
                args.all_output,
                "all",
            )
            write_legacy_json_lines(
                registry,
                args.acquisition_output,
                "acquisition",
            )
            print("generated legacy compatibility files")
        elif args.command == "check-legacy":
            registry = load_registry(args.registry)
            expected_all = render_legacy_json_lines(registry, "all")
            expected_acquisition = render_legacy_json_lines(
                registry,
                "acquisition",
            )
            actual_all = Path(args.all_path).read_text(encoding="utf-8")
            actual_acquisition = Path(args.acquisition).read_text(
                encoding="utf-8"
            )
            mismatches = []
            if expected_all != _normalize_trailing_newlines(actual_all):
                mismatches.append(args.all_path)
            if expected_acquisition != _normalize_trailing_newlines(
                actual_acquisition
            ):
                mismatches.append(args.acquisition)
            if mismatches:
                print(
                    "legacy files differ from registry: "
                    + ", ".join(mismatches),
                    file=sys.stderr,
                )
                return 1
            print("legacy files match registry exactly")
    except (OSError, RegistryValidationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _normalize_trailing_newlines(text: str) -> str:
    return text.rstrip("\r\n") + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
