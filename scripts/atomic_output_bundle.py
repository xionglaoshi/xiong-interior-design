"""Safely commit a complete set of generated files from a same-volume stage."""
from pathlib import Path
import os


def validate_bundle_names(names):
    names = list(names)
    if not names or len(set(names)) != len(names):
        raise ValueError("Output bundle must contain unique file names")
    for name in names:
        if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"Output bundle entries must be plain file names: {name!r}")
    return names


def preflight_output_bundle(output_dir, names, *, force=False):
    """Check targets before any commit; unrelated files are left untouched."""
    names = validate_bundle_names(names)
    destination = Path(output_dir)
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {destination}")
    targets = [destination / name for name in names]
    existing = [path for path in targets if os.path.lexists(path)]
    if existing and not force:
        raise FileExistsError(f"Refusing to overwrite existing outputs: {existing}")
    if any(path.is_dir() for path in existing):
        raise IsADirectoryError(f"Output bundle target is a directory: {[p for p in existing if p.is_dir()]}")
    return targets


def commit_output_bundle(stage_dir, output_dir, names, *, force=False):
    """Move staged files into place, restoring the previous bundle on failure."""
    names = validate_bundle_names(names)
    stage = Path(stage_dir)
    destination = Path(output_dir)
    sources = [stage / name for name in names]
    missing = [path for path in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Staged output bundle is incomplete: {missing}")

    targets = preflight_output_bundle(destination, names, force=force)
    destination.mkdir(parents=True, exist_ok=True)
    backup_dir = stage / ".previous-output-backup"
    backup_dir.mkdir()
    backed_up = []
    installed = []
    try:
        for source, target, name in zip(sources, targets, names):
            if os.path.lexists(target):
                backup = backup_dir / name
                os.replace(target, backup)
                backed_up.append((backup, target))
            os.replace(source, target)
            installed.append(target)
    except Exception:
        for target in reversed(installed):
            if target.exists() and target.is_file():
                target.unlink()
        for backup, target in reversed(backed_up):
            if backup.exists():
                os.replace(backup, target)
        raise
    return targets
