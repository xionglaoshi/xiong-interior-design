"""Create and validate a user-confirmation receipt bound to exact geometry bytes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_geometry_sources import audit


def geometry_sha256(path):
    return hashlib.sha256(Path(path).expanduser().resolve().read_bytes()).hexdigest()


def require_complete_source_audit(geometry_file):
    try:
        data = json.loads(geometry_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("geometry JSON root must be an object")
        report = audit(data, geometry_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Cannot complete geometry source audit: {exc}") from exc
    if report["status"] != "complete":
        details = "; ".join((report["errors"] + report["warnings"])[:6])
        raise ValueError(f"Geometry source audit is {report['status']}; complete source_refs before confirmation: {details}")
    return report


def create_receipt(geometry_path, confirmation):
    geometry_file = Path(geometry_path).expanduser().resolve()
    text = str(confirmation).strip()
    if not geometry_file.is_file():
        raise FileNotFoundError(f"Geometry JSON not found: {geometry_file}")
    if not text:
        raise ValueError("A concise record of the user's explicit confirmation is required")
    require_complete_source_audit(geometry_file)
    return {
        "schema_version": 1,
        "status": "user_confirmed",
        "geometry_file": str(geometry_file),
        "geometry_sha256": geometry_sha256(geometry_file),
        "confirmation_text": text,
        "recorded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "note": "Audit receipt only; not a digital signature, construction approval, or authorization for unrequested downstream stages.",
    }


def validate_receipt(geometry_path, approval_path):
    geometry_file = Path(geometry_path).expanduser().resolve()
    approval_file = Path(approval_path).expanduser().resolve()
    if not approval_file.is_file():
        raise FileNotFoundError(f"Geometry approval receipt not found: {approval_file}")
    try:
        receipt = json.loads(approval_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read geometry approval receipt: {approval_file}") from exc
    if (type(receipt.get("schema_version")) is not int or receipt.get("schema_version") != 1 or
            receipt.get("status") != "user_confirmed"):
        raise ValueError("Approval receipt is not a supported user-confirmation record")
    try:
        recorded_geometry = Path(receipt.get("geometry_file", "")).expanduser().resolve()
    except (TypeError, OSError, RuntimeError) as exc:
        raise ValueError("Approval receipt has an invalid geometry_file") from exc
    if recorded_geometry != geometry_file:
        raise ValueError("Approval receipt belongs to a different geometry file path")
    if not isinstance(receipt.get("confirmation_text"), str) or not receipt["confirmation_text"].strip():
        raise ValueError("Approval receipt lacks a confirmation record")
    current_hash = geometry_sha256(geometry_file)
    if receipt.get("geometry_sha256") != current_hash:
        raise ValueError("Geometry JSON changed after user confirmation; get confirmation for the new version")
    require_complete_source_audit(geometry_file)
    return receipt


def validate_receipt_snapshot(geometry_path, approval_path, geometry_bytes):
    """Ensure the approved bytes are the exact snapshot already loaded by a producer."""
    receipt = validate_receipt(geometry_path, approval_path)
    snapshot_hash = hashlib.sha256(geometry_bytes).hexdigest()
    if receipt.get("geometry_sha256") != snapshot_hash:
        raise ValueError("Geometry file changed after it was loaded; reload it and obtain approval for that exact version")
    return receipt
