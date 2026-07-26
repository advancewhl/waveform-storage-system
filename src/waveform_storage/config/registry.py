"""Single-source device registry and legacy compatibility generation."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import MISSING, asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence
from uuid import uuid4


SCHEMA_VERSION = 1
CANDIDATE_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = frozenset(
    {SCHEMA_VERSION, CANDIDATE_SCHEMA_VERSION}
)
PV_PATTERN = re.compile(
    r"^(?P<area>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-BI:"
    r"(?P<name>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-E:RESULT_CAPTURE$"
)
DEVICE_ID_PATTERN = re.compile(r"^[A-Z0-9_]+$")
CREDENTIAL_PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
DEVICE_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
EPICS_PORT_STATUSES = frozenset(
    {"assumed", "configured", "verified", "failed"}
)
REVIEW_STATUSES = frozenset({"approved", "needs_review", "rejected"})


class RegistryValidationError(ValueError):
    """Raised when the device registry is ambiguous or unsafe to generate."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True)
class Device:
    id: str
    section: str
    display_name: str
    device_type: str
    capture_pv: str
    epics_host: str
    epics_port: int
    ssh_host: str
    ssh_port: int
    credential_profile: str
    remote_data_dir: str
    file_pattern: str
    expected_points: int
    x_unit: str | None
    y_unit: str | None
    scale_factor: float | None
    order: int
    enabled: bool
    acquisition_enabled: bool
    default_selected: bool
    tags: tuple[str, ...]
    epics_port_status: str = "configured"
    review_status: str = "approved"
    source_ref: str | None = None


@dataclass(frozen=True)
class DeviceRegistry:
    schema_version: int
    devices: tuple[Device, ...]
    description: str = ""


def device_id_from_pv(pv: str) -> str:
    match = PV_PATTERN.fullmatch(pv)
    if not match:
        raise ValueError(f"unsupported capture PV format: {pv!r}")
    return _canonical_id(
        f"{match.group('area')}_BI_{match.group('name')}"
    )


def area_id_from_pv(pv: str) -> str:
    match = PV_PATTERN.fullmatch(pv)
    if not match:
        raise ValueError(f"unsupported capture PV format: {pv!r}")
    return _canonical_id(match.group("area"))


def import_candidate_registry(
    candidate_path: str | os.PathLike[str],
) -> DeviceRegistry:
    """Import an audited PV/IP candidate without enabling real acquisition."""

    source = Path(candidate_path)
    rows = _parse_legacy_json_lines(source)
    devices: list[Device] = []
    errors: list[str] = []
    for order, row in enumerate(rows):
        pv = row["pv"]
        match = PV_PATTERN.fullmatch(pv)
        if not match:
            errors.append(f"unsupported capture PV format: {pv!r}")
            continue
        name = match.group("name")
        try:
            device_id = device_id_from_pv(pv)
            area_id = area_id_from_pv(pv)
            device_type = _device_type(name)
        except (ValueError, RegistryValidationError) as exc:
            errors.append(str(exc))
            continue
        devices.append(
            Device(
                id=device_id,
                section=area_id,
                display_name=f"{match.group('area')}/{name}",
                device_type=device_type,
                capture_pv=pv,
                epics_host=row["ip"],
                epics_port=int(row["port"]),
                ssh_host=row["ip"],
                ssh_port=22,
                credential_profile="bpm_device_default",
                remote_data_dir="/home/data",
                file_pattern="*.txt",
                expected_points=1024,
                x_unit=None,
                y_unit=None,
                scale_factor=None,
                order=order,
                enabled=True,
                acquisition_enabled=False,
                default_selected=False,
                tags=(area_id, device_type, "candidate"),
                epics_port_status="assumed",
                review_status="needs_review",
                source_ref=source.name,
            )
        )
    if errors:
        raise RegistryValidationError(errors)
    registry = DeviceRegistry(
        schema_version=CANDIDATE_SCHEMA_VERSION,
        description=(
            "383-device candidate imported from the 2026-07-26 audited "
            "PV/IP list. EPICS ports are assumptions and acquisition is "
            "disabled until Linux field verification."
        ),
        devices=tuple(devices),
    )
    validate_registry(registry)
    return registry


def import_legacy_registry(
    all_devices_path: str | os.PathLike[str],
    acquisition_path: str | os.PathLike[str],
) -> DeviceRegistry:
    """Import the current full and acquisition lists without changing them."""

    all_rows = _parse_legacy_json_lines(Path(all_devices_path))
    acquisition_rows = _parse_legacy_json_lines(Path(acquisition_path))
    acquisition_by_pv = {row["pv"]: row for row in acquisition_rows}
    errors: list[str] = []

    all_pvs = {row["pv"] for row in all_rows}
    unknown_acquisition = sorted(set(acquisition_by_pv) - all_pvs)
    if unknown_acquisition:
        errors.append(
            "acquisition list contains PVs absent from full list: "
            + ", ".join(unknown_acquisition)
        )

    devices: list[Device] = []
    for order, row in enumerate(all_rows):
        pv = row["pv"]
        try:
            device_id = device_id_from_pv(pv)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        match = PV_PATTERN.fullmatch(pv)
        assert match is not None
        section = match.group("area").split("-")[-1]
        name = match.group("name")
        device_type = _device_type(name)
        selected = pv in acquisition_by_pv
        if selected:
            selected_row = acquisition_by_pv[pv]
            if (
                selected_row["ip"] != row["ip"]
                or selected_row["port"] != row["port"]
            ):
                errors.append(
                    f"acquisition fields differ from full list for {pv}"
                )
        devices.append(
            Device(
                id=device_id,
                section=section,
                display_name=f"{section}/{name}",
                device_type=device_type,
                capture_pv=pv,
                epics_host=row["ip"],
                epics_port=int(row["port"]),
                ssh_host=row["ip"],
                ssh_port=22,
                credential_profile="bpm_device_default",
                remote_data_dir="/home/data",
                file_pattern="*.txt",
                expected_points=1024,
                x_unit=None,
                y_unit=None,
                scale_factor=None,
                order=order,
                enabled=True,
                acquisition_enabled=selected,
                default_selected=selected,
                tags=(section, device_type),
            )
        )

    if errors:
        raise RegistryValidationError(errors)
    registry = DeviceRegistry(
        schema_version=SCHEMA_VERSION,
        description=(
            "Imported from the verified 47-device pv_list_ALL.txt and "
            "12-device acquisition_pv_list.txt baseline."
        ),
        devices=tuple(devices),
    )
    validate_registry(registry)
    return registry


def validate_registry(registry: DeviceRegistry) -> None:
    errors: list[str] = []
    if registry.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(
            "schema_version must be one of "
            f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}, got "
            f"{registry.schema_version}"
        )
    if not registry.devices:
        errors.append("registry must contain at least one device")

    _check_unique(errors, "device id", (item.id for item in registry.devices))
    _check_unique(
        errors,
        "capture PV",
        (item.capture_pv for item in registry.devices),
    )
    if registry.schema_version == SCHEMA_VERSION:
        _check_unique(
            errors,
            "EPICS host",
            (item.epics_host for item in registry.devices),
        )
    _check_unique(errors, "order", (item.order for item in registry.devices))

    expected_orders = list(range(len(registry.devices)))
    actual_orders = sorted(item.order for item in registry.devices)
    if actual_orders != expected_orders:
        errors.append(
            f"orders must be contiguous 0..{len(registry.devices) - 1}, "
            f"got {actual_orders}"
        )

    for item in registry.devices:
        prefix = f"device {item.id!r}"
        if not DEVICE_ID_PATTERN.fullmatch(item.id):
            errors.append(f"{prefix}: invalid id")
        try:
            expected_id = device_id_from_pv(item.capture_pv)
            if item.id != expected_id:
                errors.append(
                    f"{prefix}: id does not match capture_pv ({expected_id})"
                )
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        if not item.section:
            errors.append(f"{prefix}: section is empty")
        elif registry.schema_version == CANDIDATE_SCHEMA_VERSION:
            try:
                expected_section = area_id_from_pv(item.capture_pv)
                if item.section != expected_section:
                    errors.append(
                        f"{prefix}: section does not match capture_pv "
                        f"({expected_section})"
                    )
            except ValueError:
                pass
        elif item.section not in item.id.split("_"):
            errors.append(f"{prefix}: section is missing or inconsistent")
        if not item.display_name:
            errors.append(f"{prefix}: display_name is empty")
        if registry.schema_version == SCHEMA_VERSION:
            if item.device_type not in ("SBPM", "CBPM", "COLDBPM"):
                errors.append(f"{prefix}: unsupported device_type")
        elif not DEVICE_TYPE_PATTERN.fullmatch(item.device_type):
            errors.append(f"{prefix}: invalid device_type")
        for field_name, host in (
            ("epics_host", item.epics_host),
            ("ssh_host", item.ssh_host),
        ):
            try:
                ipaddress.ip_address(host)
            except ValueError:
                errors.append(f"{prefix}: invalid {field_name} {host!r}")
        for field_name, port in (
            ("epics_port", item.epics_port),
            ("ssh_port", item.ssh_port),
        ):
            if not isinstance(port, int) or isinstance(port, bool):
                errors.append(f"{prefix}: {field_name} must be an integer")
            elif not 1 <= port <= 65535:
                errors.append(f"{prefix}: {field_name} out of range")
        if not CREDENTIAL_PROFILE_PATTERN.fullmatch(item.credential_profile):
            errors.append(f"{prefix}: invalid credential_profile")
        if not item.remote_data_dir.startswith("/"):
            errors.append(f"{prefix}: remote_data_dir must be absolute")
        if not item.file_pattern:
            errors.append(f"{prefix}: file_pattern is empty")
        if item.expected_points <= 0:
            errors.append(f"{prefix}: expected_points must be positive")
        if item.scale_factor is not None and item.scale_factor <= 0:
            errors.append(f"{prefix}: scale_factor must be positive or null")
        if not item.enabled and (
            item.acquisition_enabled or item.default_selected
        ):
            errors.append(f"{prefix}: disabled device cannot be active")
        if item.default_selected and not item.acquisition_enabled:
            errors.append(
                f"{prefix}: default_selected requires acquisition_enabled"
            )
        if any(not tag for tag in item.tags):
            errors.append(f"{prefix}: tags contains an empty value")
        if item.epics_port_status not in EPICS_PORT_STATUSES:
            errors.append(f"{prefix}: invalid epics_port_status")
        if item.review_status not in REVIEW_STATUSES:
            errors.append(f"{prefix}: invalid review_status")
        if item.source_ref is not None and (
            not isinstance(item.source_ref, str) or not item.source_ref.strip()
        ):
            errors.append(f"{prefix}: source_ref must be non-empty or null")

    if errors:
        raise RegistryValidationError(errors)


def save_registry(
    registry: DeviceRegistry,
    path: str | os.PathLike[str],
) -> Path:
    """Atomically save a validated registry as canonical JSON."""

    validate_registry(registry)
    destination = Path(path)
    payload = {
        "schema_version": registry.schema_version,
        "description": registry.description,
        "devices": [_device_to_dict(item) for item in registry.devices],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def load_registry(path: str | os.PathLike[str]) -> DeviceRegistry:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryValidationError([f"cannot read registry: {exc}"]) from exc
    if not isinstance(payload, dict):
        raise RegistryValidationError(["registry root must be an object"])
    allowed_root = {"schema_version", "description", "devices"}
    unknown_root = sorted(set(payload) - allowed_root)
    if unknown_root:
        raise RegistryValidationError(
            ["unknown root fields: " + ", ".join(unknown_root)]
        )
    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list):
        raise RegistryValidationError(["devices must be an array"])

    devices: list[Device] = []
    errors: list[str] = []
    device_fields = fields(Device)
    expected_fields = {item.name for item in device_fields}
    required_fields = {
        item.name
        for item in device_fields
        if item.default is MISSING and item.default_factory is MISSING
    }
    for index, raw in enumerate(raw_devices):
        if not isinstance(raw, dict):
            errors.append(f"devices[{index}] must be an object")
            continue
        unknown = sorted(set(raw) - expected_fields)
        missing = sorted(required_fields - set(raw))
        if unknown:
            errors.append(
                f"devices[{index}] unknown fields: {', '.join(unknown)}"
            )
        if missing:
            errors.append(
                f"devices[{index}] missing fields: {', '.join(missing)}"
            )
        if unknown or missing:
            continue
        converted = dict(raw)
        tags = converted.get("tags")
        if not isinstance(tags, list) or not all(
            isinstance(tag, str) for tag in tags
        ):
            errors.append(f"devices[{index}].tags must be a string array")
            continue
        converted["tags"] = tuple(tags)
        try:
            devices.append(Device(**converted))
        except TypeError as exc:
            errors.append(f"devices[{index}] invalid fields: {exc}")

    if errors:
        raise RegistryValidationError(errors)
    registry = DeviceRegistry(
        schema_version=payload.get("schema_version"),
        description=payload.get("description", ""),
        devices=tuple(devices),
    )
    validate_registry(registry)
    return registry


def render_legacy_json_lines(
    registry: DeviceRegistry,
    selection: Literal["all", "acquisition"] = "all",
) -> str:
    """Render the historical JSON-lines-with-commas format exactly."""

    validate_registry(registry)
    if selection == "all":
        devices = [item for item in registry.devices if item.enabled]
    elif selection == "acquisition":
        devices = [
            item
            for item in registry.devices
            if item.enabled and item.acquisition_enabled
        ]
    else:
        raise ValueError(f"unsupported selection: {selection}")
    devices.sort(key=lambda item: item.order)
    lines = [
        (
            f'{{"pv":"{item.capture_pv}", '
            f'"ip":"{item.epics_host}", '
            f'"port":"{item.epics_port}"}}'
        )
        for item in devices
    ]
    return "".join(
        line + (",\n" if index < len(lines) - 1 else "\n")
        for index, line in enumerate(lines)
    )


def write_legacy_json_lines(
    registry: DeviceRegistry,
    path: str | os.PathLike[str],
    selection: Literal["all", "acquisition"],
) -> Path:
    destination = Path(path)
    text = render_legacy_json_lines(registry, selection)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _parse_legacy_json_lines(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RegistryValidationError([f"cannot read {path}: {exc}"]) from exc
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(","):
            stripped = stripped[:-1]
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(row, dict) or set(row) != {"pv", "ip", "port"}:
            errors.append(
                f"{path}:{line_number}: expected pv, ip, port fields"
            )
            continue
        if not all(isinstance(row[key], str) for key in ("pv", "ip", "port")):
            errors.append(f"{path}:{line_number}: fields must be strings")
            continue
        rows.append(row)
    if errors:
        raise RegistryValidationError(errors)
    if not rows:
        raise RegistryValidationError([f"{path}: no device rows"])
    return rows


def _device_type(name: str) -> str:
    match = re.match(r"[A-Za-z]+", name)
    if match:
        return match.group(0).upper()
    raise RegistryValidationError([f"unknown device type: {name}"])


def _canonical_id(value: str) -> str:
    canonical = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if not canonical or not DEVICE_ID_PATTERN.fullmatch(canonical):
        raise ValueError(f"cannot derive canonical ID from {value!r}")
    return canonical


def _check_unique(
    errors: list[str],
    label: str,
    values: Iterable[Any],
) -> None:
    seen: set[Any] = set()
    duplicates: set[Any] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        errors.append(
            f"duplicate {label}: "
            + ", ".join(repr(value) for value in sorted(duplicates))
        )


def _device_to_dict(device: Device) -> dict[str, Any]:
    payload = asdict(device)
    payload["tags"] = list(device.tags)
    return payload
