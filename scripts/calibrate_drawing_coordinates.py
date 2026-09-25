#!/usr/bin/env python3
"""Map traced drawing-image pixels to CAD millimeters using verified anchors.

This is a coordinate-calibration utility, not a CAD/image recognizer. Input
anchors must be measured/confirmed against the source CAD. At least four
well-spread anchors are required for an overdetermined fit and residual check;
the operator must also independently verify another dimension. Default
behavior is dry-run; output is a coordinate table, not geometry.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import hashlib
from pathlib import Path

from PIL import Image

MIN_ANCHOR_SPREAD_RATIO = 1e-4


def _point(value, label):
    if (not isinstance(value, list) or len(value) != 2 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in value)):
        raise ValueError(f"{label} must be a pair of finite numbers")
    return float(value[0]), float(value[1])


def _solve(matrix, rhs):
    """Solve a small square system with partial pivoting."""
    rows = [list(map(float, row)) + [float(value)] for row, value in zip(matrix, rhs)]
    n = len(rows)
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise ValueError("Calibration anchors are collinear or numerically unstable")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        factor = rows[col][col]
        rows[col] = [value / factor for value in rows[col]]
        for row in range(n):
            if row == col:
                continue
            factor = rows[row][col]
            rows[row] = [a - factor * b for a, b in zip(rows[row], rows[col])]
    return [rows[i][-1] for i in range(n)]


def fit_affine(anchors):
    """Fit CAD-mm = A * pixel + t by least squares; return matrix and errors."""
    parsed = []
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            raise ValueError(f"Anchor {index + 1} must be an object")
        parsed.append((_point(anchor.get("pixel"), f"anchor {index + 1} pixel"),
                       _point(anchor.get("cad_mm"), f"anchor {index + 1} cad_mm")))
    if len(parsed) < 4:
        raise ValueError("At least 4 verified, spatially distributed anchors are required")

    mean_x = sum(p[0][0] for p in parsed) / len(parsed)
    mean_y = sum(p[0][1] for p in parsed) / len(parsed)
    scale = max(max(abs(p[0][0] - mean_x), abs(p[0][1] - mean_y)) for p in parsed)
    if scale < 1e-9:
        raise ValueError("Calibration anchors must span a non-zero pixel area")
    sxx = sum((point[0][0] - mean_x) ** 2 for point in parsed)
    syy = sum((point[0][1] - mean_y) ** 2 for point in parsed)
    sxy = sum((point[0][0] - mean_x) * (point[0][1] - mean_y) for point in parsed)
    trace = sxx + syy
    discriminant = math.hypot(sxx - syy, 2 * sxy)
    major_spread = (trace + discriminant) / 2
    minor_spread = max(0.0, (trace - discriminant) / 2)
    spread_ratio = minor_spread / major_spread if major_spread > 0 else 0.0
    if spread_ratio < MIN_ANCHOR_SPREAD_RATIO:
        raise ValueError(
            "Calibration anchors are too narrowly distributed or nearly collinear; "
            "select points spanning both drawing directions"
        )
    design = [((px - mean_x) / scale, (py - mean_y) / scale, 1.0) for (px, py), _ in parsed]
    normal = [[sum(row[i] * row[j] for row in design) for j in range(3)] for i in range(3)]
    coefficients = []
    for axis in range(2):
        rhs = [sum(row[i] * cad[axis] for row, (_, cad) in zip(design, parsed)) for i in range(3)]
        coefficients.append(_solve(normal, rhs))

    # Store normalized-domain coefficients internally, then convert to pixels.
    ax, bx, tx = coefficients[0]
    ay, by, ty = coefficients[1]
    matrix = [[ax / scale, bx / scale], [ay / scale, by / scale]]
    offset = [tx - matrix[0][0] * mean_x - matrix[0][1] * mean_y,
              ty - matrix[1][0] * mean_x - matrix[1][1] * mean_y]

    def transform(pixel):
        x, y = pixel
        return (matrix[0][0] * x + matrix[0][1] * y + offset[0],
                matrix[1][0] * x + matrix[1][1] * y + offset[1])

    residuals = [math.dist(transform(pixel), cad) for pixel, cad in parsed]
    rms = math.sqrt(sum(error * error for error in residuals) / len(residuals))
    return transform, matrix, offset, rms, max(residuals), spread_ratio


def calibrate(data, max_residual_mm):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Input must use schema_version=1")
    source_image = data.get("source_image")
    source_hash = data.get("source_image_sha256")
    canvas_px = data.get("source_image_px")
    if not isinstance(source_image, str) or not source_image.strip():
        raise ValueError("source_image must name the exact calibrated raster image")
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("source_image_sha256 must be the 64-character lowercase SHA-256 from the annotation export")
    if (not isinstance(canvas_px, list) or len(canvas_px) != 2 or
            any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in canvas_px)):
        raise ValueError("source_image_px must contain two positive integer canvas dimensions")

    def check_pixel(pixel, label):
        x, y = _point(pixel, label)
        if not (0 <= x <= canvas_px[0] and 0 <= y <= canvas_px[1]):
            raise ValueError(f"{label} falls outside source image canvas {canvas_px}")
        return x, y

    if not isinstance(data.get("anchors"), list):
        raise ValueError("Input anchors must be a list")
    if not isinstance(data.get("points"), list):
        raise ValueError("Input points must be a list")
    verification_points = data.get("verification_points")
    if not isinstance(verification_points, list) or not verification_points:
        raise ValueError("At least 1 independent verification_point measured from CAD is required")
    for index, anchor in enumerate(data["anchors"]):
        if not isinstance(anchor, dict):
            raise ValueError(f"Anchor {index + 1} must be an object")
        check_pixel(anchor.get("pixel"), f"anchor {index + 1} pixel")
    anchor_pixels = [_point(anchor.get("pixel"), f"anchor {index + 1} pixel")
                     for index, anchor in enumerate(data["anchors"])]
    anchor_cad_points = [_point(anchor.get("cad_mm"), f"anchor {index + 1} cad_mm")
                         for index, anchor in enumerate(data["anchors"])]
    transform, matrix, offset, rms, maximum, spread_ratio = fit_affine(data["anchors"])
    if maximum > max_residual_mm:
        raise ValueError(
            f"Calibration residual {maximum:.3f} mm exceeds limit {max_residual_mm:.3f} mm; "
            "check source image, CAD anchors, and crop/version"
        )
    verified = []
    verification_ids = set()
    for index, item in enumerate(verification_points):
        if not isinstance(item, dict):
            raise ValueError(f"Verification point {index + 1} must be an object")
        point_id = item.get("id")
        if not isinstance(point_id, str) or not point_id.strip() or point_id in verification_ids:
            raise ValueError(f"Verification point {index + 1} needs a unique non-empty id")
        verification_ids.add(point_id)
        pixel = check_pixel(item.get("pixel"), f"verification point {point_id} pixel")
        cad_mm = _point(item.get("cad_mm"), f"verification point {point_id} cad_mm")
        if any(math.dist(pixel, anchor_pixel) <= 1e-6 for anchor_pixel in anchor_pixels):
            raise ValueError(f"Verification point {point_id!r} reuses a fitted anchor pixel; choose an independent point")
        if any(math.dist(cad_mm, anchor_point) <= 1e-6 for anchor_point in anchor_cad_points):
            raise ValueError(f"Verification point {point_id!r} reuses a fitted anchor CAD coordinate; choose an independent point")
        predicted = transform(pixel)
        error = math.dist(predicted, cad_mm)
        if error > max_residual_mm:
            raise ValueError(
                f"Independent CAD check {point_id!r} error {error:.3f} mm exceeds limit "
                f"{max_residual_mm:.3f} mm; check screenshot scale, crop, anchors, and CAD measurement"
            )
        verified.append({"id": point_id, "pixel": list(pixel),
                         "cad_mm": [round(value, 3) for value in cad_mm],
                         "predicted_mm": [round(value, 3) for value in predicted],
                         "error_mm": round(error, 3)})
    result_points = []
    seen = set()
    for index, item in enumerate(data["points"]):
        if not isinstance(item, dict):
            raise ValueError(f"Point {index + 1} must be an object")
        point_id = item.get("id")
        if not isinstance(point_id, str) or not point_id.strip() or point_id in seen:
            raise ValueError(f"Point {index + 1} needs a unique non-empty id")
        seen.add(point_id)
        pixel = check_pixel(item.get("pixel"), f"point {point_id} pixel")
        x_mm, y_mm = transform(pixel)
        result_points.append({"id": point_id, "pixel": list(pixel),
                              "point_mm": [round(x_mm, 3), round(y_mm, 3)]})
    return {
        "schema_version": 1,
        "units": "mm",
        "source_image": data.get("source_image"),
        "source_image_sha256": source_hash,
        "source_image_px": canvas_px,
        "calibration": {
            "method": "2D affine least-squares",
            "anchor_count": len(data["anchors"]),
            "matrix_mm_per_px": [[round(v, 12) for v in row] for row in matrix],
            "offset_mm": [round(v, 6) for v in offset],
            "rms_residual_mm": round(rms, 3),
            "max_residual_mm": round(maximum, 3),
            "anchor_spread_ratio": round(spread_ratio, 8),
            "accepted_max_residual_mm": max_residual_mm,
            "anchors_are_user_or_CAD_verified": True,
            "independent_verification_count": len(verified),
            "max_independent_error_mm": round(max(item["error_mm"] for item in verified), 3),
            "independent_verification_points": verified,
        },
        "points": result_points,
        "warning": "坐标表不是房间几何；墙/房间/开口/可拆改属性仍须依据CAD及用户确认。",
    }


def main():
    parser = argparse.ArgumentParser(description="以CAD核实控制点校准图像坐标到毫米；不识别房间或墙体。")
    parser.add_argument("--input", required=True, type=Path, help="anchors与points JSON")
    parser.add_argument("--output", required=True, type=Path, help="新的坐标表 JSON")
    parser.add_argument("--max-residual-mm", required=True, type=float,
                        help="接受的最大控制点拟合残差；需按CAD来源精度设置")
    parser.add_argument("--execute", action="store_true", help="写入输出；默认只预演")
    args = parser.parse_args()
    if not math.isfinite(args.max_residual_mm) or args.max_residual_mm <= 0:
        parser.error("--max-residual-mm must be a finite positive number")
    source, target = args.input.expanduser().resolve(), args.output.expanduser().resolve()
    if not source.is_file():
        parser.error(f"Input not found: {source}")
    if source == target or target.exists():
        parser.error("Output must be a new path and must not overwrite input or existing files")
    with source.open(encoding="utf-8") as stream:
        data = json.load(stream)
    image_path = Path(data.get("source_image", "")).expanduser()
    if not image_path.is_absolute():
        image_path = source.parent / image_path
    image_path = image_path.resolve()
    if not image_path.is_file():
        parser.error(f"Source image not found: {image_path}")
    image_bytes = image_path.read_bytes()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    try:
        with Image.open(image_path) as image:
            image_px = list(image.size)
    except Exception as exc:
        parser.error(f"Cannot read source image dimensions: {exc}")
    if data.get("source_image_sha256") != image_hash:
        parser.error("Source image SHA-256 does not match the annotation export; do not reuse these pixel coordinates")
    if data.get("source_image_px") != image_px:
        parser.error(f"Source image dimensions do not match annotation canvas: actual={image_px}")
    data["source_image"] = str(image_path)
    result = calibrate(data, args.max_residual_mm)
    report = {"input": str(source), "output": str(target), "execute": args.execute,
              "anchor_count": result["calibration"]["anchor_count"],
              "rms_residual_mm": result["calibration"]["rms_residual_mm"],
              "max_residual_mm": result["calibration"]["max_residual_mm"],
              "anchor_spread_ratio": result["calibration"]["anchor_spread_ratio"],
              "max_independent_error_mm": result["calibration"]["max_independent_error_mm"],
              "source_image_sha256": result["source_image_sha256"],
              "source_image_px": result["source_image_px"],
              "points": result["points"]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.execute:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(f"COORDINATE_CALIBRATION_OK {target}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"错误：{exc}")
