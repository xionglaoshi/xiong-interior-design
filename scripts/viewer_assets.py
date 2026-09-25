"""Paths and safe copying for the bundled browser model viewer runtime."""
from pathlib import Path
import html
import json
import shutil


ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "vendor"
VIEWER_FILES = (
    "model-viewer-4.3.1.min.js",
    "model-viewer-4.3.1-LICENSE.txt",
    "lit-BSD-3-Clause-LICENSE.txt",
    "threejs-MIT-LICENSE.txt",
)
TEMPLATE_PATH = ASSET_DIR.parent / "model-viewer-template.html"


def render_viewer_page(project_title, model_filename, room_options, wall_material_name):
    """Fill the offline viewer template with trusted generated scene metadata."""
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (template.replace("{{PROJECT_TITLE}}", html.escape(str(project_title)))
            .replace("{{MODEL_FILE}}", html.escape(Path(model_filename).name, quote=True))
            .replace("{{ROOM_OPTIONS}}", "\n      ".join(room_options))
            .replace("{{WALL_MATERIAL_NAME}}", json.dumps(str(wall_material_name), ensure_ascii=False)))


def viewer_asset_targets(output_dir):
    """Return generated-page sidecars that must be included in overwrite checks."""
    return [Path(output_dir) / name for name in VIEWER_FILES]


def copy_viewer_assets(output_dir, *, force=False):
    """Copy the pinned runtime and its notices next to a generated viewer page."""
    destination = Path(output_dir)
    missing = [name for name in VIEWER_FILES if not (ASSET_DIR / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing bundled model viewer assets: {missing}")
    targets = viewer_asset_targets(destination)
    existing = [str(path) for path in targets if path.exists()]
    if existing and not force:
        raise FileExistsError(f"Refusing to overwrite viewer runtime assets: {existing}")
    destination.mkdir(parents=True, exist_ok=True)
    for name, target in zip(VIEWER_FILES, targets):
        shutil.copyfile(ASSET_DIR / name, target)
    return targets
