#!/usr/bin/env python3
"""Regression tests for human-verifiable render geometry checklists."""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from build_render_prompt import (build_geometry_audit, build_prompt,
                                 split_render_handoff, validate_reference_bundle)
from geometry_approval import create_receipt

class GeometryAuditChecklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = {
            "id": "R01", "polygon": [[0, 0], [5600, 0], [5600, 4600], [0, 4600]],
            "walls": [
                {"openings": [{"kind": "window", "start_mm": 900, "width_mm": 2100,
                               "sill_mm": 850, "height_mm": 1250}]},
                {"openings": [{"kind": "door", "start_mm": 600, "width_mm": 900,
                               "sill_mm": 0, "height_mm": 2100}]},
                {}, {},
            ],
        }
        cls.geometry = {
            "rooms": [cls.room],
            "furniture": [
                {"id": "S01", "name": "三人位沙发", "type": "sofa", "room_id": "R01",
                 "center_mm": [2800, 1250], "size_mm": [2400, 950, 850]},
                {"id": "T01", "name": "茶几", "type": "table", "room_id": "R01",
                 "center_mm": [2800, 2600], "size_mm": [1100, 600, 430]},
                {"id": "C01", "name": "单椅", "room_id": "R01",
                 "type": "armchair", "center_mm": [1300, 3000], "size_mm": [700, 700, 850]},
                {"id": "K01", "name": "边柜", "type": "cabinet", "room_id": "R01",
                 "center_mm": [4700, 3800], "size_mm": [700, 450, 850]},
            ],
            "fixed_elements": [],
        }
        cls.checklist = build_geometry_audit(cls.room, cls.geometry)

    def test_checklist_covers_boundary_openings_and_all_furniture(self):
        self.assertIn("输入 4 个顶点", self.checklist)
        self.assertIn("共 2 处", self.checklist)
        for opening_type in ("window", "door"):
            self.assertIn(opening_type, self.checklist)
        for furniture_id in ("S01", "T01", "C01", "K01"):
            self.assertIn(furniture_id, self.checklist)
        self.assertIn("类别：带扶手椅", self.checklist)
        self.assertIn("共 4 件", self.checklist)

    def test_checklist_requires_rejection_on_unverifiable_mismatch(self):
        self.assertIn("透视遮挡导致无法核验", self.checklist)
        self.assertIn("不视为默认通过", self.checklist)

    def test_three_blender_references_assign_top_view_to_plan_check(self):
        brief = {"project": "test"}
        view = {
            "room_id": "R01", "style": "neutral", "materials": ["wood"],
            "lighting": "soft", "camera": "match model", "aspect_ratio": "16:9",
            "reference_image": "cutaway.png", "reference_full_image": "full.png",
            "reference_top_image": "top.png",
        }
        prompt = build_prompt(self.geometry, brief, view)
        self.assertIn("cutaway.png", prompt)
        self.assertIn("full.png", prompt)
        self.assertIn("top.png", prompt)
        self.assertIn("以它核验平面边界", prompt)
        self.assertIn("类别：带扶手椅", prompt)
        self.assertIn("不得新增、删除、复制或替换实体", prompt)
        self.assertIn("类别、数量、尺寸、中心位置、朝向或占地", prompt)
        self.assertIn("不得覆盖几何清单", prompt)
        self.assertIn("只输出一张、一个镜头", prompt)
        self.assertIn("禁止拼贴、分屏", prompt)

    def test_top_layout_axonometric_mode_uses_only_plan_reference_and_new_camera(self):
        view = {
            "room_id": "R01", "style": "neutral", "materials": ["wood"],
            "lighting": "soft", "camera": "old perspective camera", "aspect_ratio": "16:9",
            "imagegen_reference_mode": "top_layout_axonometric",
            "reference_image": "/tmp/cutaway.png",
            "reference_full_image": "/tmp/full.png",
            "reference_top_image": "/tmp/top.png",
        }
        document = build_prompt(self.geometry, {"project": "test"}, view)
        prompt_document, _ = split_render_handoff(document, view)
        self.assertIn("唯一的Blender全墙顶视布局", document)
        self.assertIn("转换为一个高位轴测/开顶空间视角", document)
        self.assertIn("图像为轴测新视角", document)
        self.assertIn("不沿用顶视相机", document)
        self.assertNotIn("优先保持 Blender 参考图中的机位", document)
        self.assertNotIn("改变参考镜头的透视和机位", document)
        self.assertIn("与 Blender 顶视图及模型清单核对", document)
        self.assertNotIn("old perspective camera", document)
        self.assertIn("图像1 = Blender全墙顶视布局", prompt_document)
        self.assertIn("/tmp/top.png", prompt_document)
        self.assertNotIn("/tmp/cutaway.png", prompt_document)
        self.assertNotIn("/tmp/full.png", prompt_document)

    def test_top_layout_audit_checks_plan_only_not_unseen_heights(self):
        geometry = {
            **self.geometry,
            "fixed_elements": [{"id": "F01", "name": "立柱", "type": "column", "room_id": "R01",
                                "center_mm": [500, 500], "size_mm": [300, 300, 2800]}],
        }
        audit = build_geometry_audit(self.room, geometry, "top_layout_axonometric")
        self.assertIn("起点 900 mm，宽 2100 mm", audit)
        self.assertIn("边2 door：起点 600 mm，宽 900 mm", audit)
        self.assertNotIn("高 1250 mm", audit)
        self.assertNotIn("高 2100 mm", audit)
        self.assertIn("S01", audit)
        self.assertIn("中心 [2800, 1250] mm，外包 [2400, 950] mm", audit)
        self.assertNotIn("外包 [2400, 950, 850] mm", audit)
        self.assertIn("F01", audit)
        self.assertIn("尺寸 [300, 300] mm", audit)
        self.assertNotIn("尺寸 [300, 300, 2800] mm", audit)

    def test_image_model_prompt_is_separate_from_human_geometry_checklist(self):
        view = {
            "room_id": "R01", "style": "neutral", "materials": ["wood"],
            "lighting": "soft", "camera": "match model", "aspect_ratio": "16:9",
            "reference_image": "/tmp/R01-cutaway.png",
            "reference_full_image": "/tmp/R01-full.png",
            "reference_top_image": "/tmp/R01-top.png",
        }
        document = build_prompt(self.geometry, {"project": "test"}, view)
        prompt_document, audit_document = split_render_handoff(document, view)
        self.assertIn("/tmp/R01-cutaway.png", prompt_document)
        self.assertIn("/tmp/R01-full.png", prompt_document)
        self.assertIn("/tmp/R01-top.png", prompt_document)
        self.assertIn("图像1 = Blender室内剖切透视", prompt_document)
        self.assertIn("图像2 = Blender完整墙体透视", prompt_document)
        self.assertIn("图像3 = Blender全墙顶视布局", prompt_document)
        self.assertIn("多房间项目逐房间分别生成", prompt_document)
        self.assertIn("不要将不同房间的提示词或图片放进同一次 imagegen 请求", prompt_document)
        self.assertIn("禁止拼贴、分屏", prompt_document)
        self.assertIn("不得新增、删除、复制或替换实体", prompt_document)
        self.assertIn("边 1：window，沿墙起点 900 mm，宽 2100 mm", prompt_document)
        self.assertIn("S01 三人位沙发（类别：沙发）：中心 [2800, 1250] mm，体量 [2400, 950, 850] mm", prompt_document)
        self.assertIn("C01 单椅（类别：带扶手椅）：中心 [1300, 3000] mm", prompt_document)
        self.assertNotIn("效果图几何验收清单", prompt_document)
        self.assertIn("效果图几何验收清单", audit_document)
        for furniture_id in ("S01", "T01", "C01", "K01"):
            self.assertIn(furniture_id, audit_document)

    def test_selected_room_prompt_excludes_neighbor_room_geometry(self):
        neighbor = {
            "id": "R02", "name": "相邻房间",
            "polygon": [[5600, 0], [9600, 0], [9600, 4600], [5600, 4600]],
            "walls": [{}, {"openings": [{"kind": "door", "start_mm": 300, "width_mm": 900,
                                            "sill_mm": 0, "height_mm": 2100}]}, {}, {}],
        }
        geometry = {
            **self.geometry,
            "rooms": [self.room, neighbor],
            "furniture": [
                *self.geometry["furniture"],
                {"id": "S02", "name": "相邻房间沙发", "type": "sofa", "room_id": "R02",
                 "center_mm": [7600, 2200], "size_mm": [1800, 800, 850]},
            ],
        }
        prompt = build_prompt(geometry, {"project": "test"}, {
            "room_id": "R01", "style": "neutral", "materials": ["wood"],
            "lighting": "soft", "camera": "match model", "aspect_ratio": "16:9",
        })
        self.assertIn("S01 三人位沙发", prompt)
        self.assertNotIn("S02", prompt)
        self.assertNotIn("相邻房间沙发", prompt)
        self.assertNotIn("9600", prompt)

    def test_imagegen_prompt_rejects_legacy_furniture_without_explicit_type(self):
        geometry = json.loads(json.dumps(self.geometry))
        del geometry["furniture"][0]["type"]
        with self.assertRaisesRegex(ValueError, "explicit supported type.*S01"):
            build_prompt(geometry, {"project": "test"}, {
                "room_id": "R01", "style": "neutral", "materials": ["wood"],
                "lighting": "soft", "camera": "match model", "aspect_ratio": "16:9",
            })


class ReferenceBundleTests(unittest.TestCase):
    def make_bundle(self, directory, geometry_bytes=b"geometry-v1"):
        root = Path(directory)
        names = {
            "reference_image": "R01-camera-reference.png",
            "reference_full_image": "R01-camera-full-geometry.png",
            "reference_top_image": "R01-camera-top-layout.png",
        }
        brief_to_metadata = {
            "reference_image": "reference_image",
            "reference_full_image": "full_geometry_image",
            "reference_top_image": "top_geometry_image",
        }
        digests = {}
        for key, name in names.items():
            content = (name + "-pixels").encode()
            (root / name).write_bytes(content)
            digests[brief_to_metadata[key]] = hashlib.sha256(content).hexdigest()
        metadata = {
            "room_id": "R01",
            "geometry_sha256": hashlib.sha256(geometry_bytes).hexdigest(),
            "images_sha256": digests,
            "view_profile": "interior",
            "corner": "sw",
            "lens_mm": 35.0,
            "camera_location_m": [-1.0, -1.0, 3.3],
            "camera_target_m": [2.0, 1.5, 0.65],
        }
        metadata.update({target: str(root / names[key]) for key, target in brief_to_metadata.items()})
        (root / "R01-camera.json").write_text(json.dumps(metadata), encoding="utf-8")
        view = {key: str(root / name) for key, name in names.items()}
        return view, root / "R01-camera.json", metadata

    def test_accepts_current_room_images_when_geometry_paths_and_hashes_match(self):
        geometry_bytes = b"geometry-v1"
        with tempfile.TemporaryDirectory(prefix="render-reference-test-") as temp:
            view, _, _ = self.make_bundle(temp, geometry_bytes)
            checked_view = {"room_id": "R01", **view}
            validate_reference_bundle(hashlib.sha256(geometry_bytes).hexdigest(), {"views": [checked_view]})
            self.assertEqual(checked_view["_camera_metadata"]["view_profile"], "interior")

            geometry = {"rooms": [{"id": "R01", "name": "测试", "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]], "walls": [{}, {}, {}, {}]}], "furniture": [], "fixed_elements": []}
            brief = {"project": "test"}
            prompt = build_prompt(geometry, brief, {**checked_view, "style": "neutral", "materials": ["wood"], "lighting": "soft", "camera": "same as reference", "aspect_ratio": "16:9"})
            self.assertIn("Blender预设=interior", prompt)
            self.assertIn("焦段=35.0 mm", prompt)
            self.assertIn("机位=[-1.0, -1.0, 3.3] m", prompt)

    def test_top_layout_only_reference_requires_and_validates_matching_metadata(self):
        geometry_bytes = b"geometry-v1"
        with tempfile.TemporaryDirectory(prefix="render-reference-test-") as temp:
            full_view, metadata_path, _ = self.make_bundle(temp, geometry_bytes)
            view = {
                "room_id": "R01", "imagegen_reference_mode": "top_layout_axonometric",
                "reference_top_image": full_view["reference_top_image"],
                "reference_metadata": str(metadata_path),
            }
            validate_reference_bundle(hashlib.sha256(geometry_bytes).hexdigest(), {"views": [view]})
            self.assertEqual(view["_camera_metadata"]["room_id"], "R01")
            with self.assertRaisesRegex(ValueError, "requires reference_metadata"):
                validate_reference_bundle(hashlib.sha256(geometry_bytes).hexdigest(), {"views": [{
                    "room_id": "R01", "imagegen_reference_mode": "top_layout_axonometric",
                    "reference_top_image": full_view["reference_top_image"],
                }]})

    def test_rejects_reference_from_another_geometry_version(self):
        with tempfile.TemporaryDirectory(prefix="render-reference-test-") as temp:
            view, _, _ = self.make_bundle(temp)
            with self.assertRaisesRegex(ValueError, "different geometry JSON"):
                validate_reference_bundle(hashlib.sha256(b"geometry-v2").hexdigest(),
                                           {"views": [{"room_id": "R01", **view}]})

    def test_rejects_image_changed_after_render(self):
        with tempfile.TemporaryDirectory(prefix="render-reference-test-") as temp:
            view, _, _ = self.make_bundle(temp)
            Path(view["reference_top_image"]).write_bytes(b"changed-pixels")
            with self.assertRaisesRegex(ValueError, "image changed"):
                validate_reference_bundle(hashlib.sha256(b"geometry-v1").hexdigest(),
                                           {"views": [{"room_id": "R01", **view}]})

    def test_rejects_inconsistent_room_metadata(self):
        with tempfile.TemporaryDirectory(prefix="render-reference-test-") as temp:
            view, metadata_path, metadata = self.make_bundle(temp)
            metadata["room_id"] = "R02"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "belongs to room"):
                validate_reference_bundle(hashlib.sha256(b"geometry-v1").hexdigest(),
                                           {"views": [{"room_id": "R01", **view}]})


class RenderHandoffCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="render-handoff-cli-")
        self.root = Path(self.temp.name)
        self.geometry = self.root / "geometry.json"
        self.geometry.write_text(json.dumps({
            "schema_version": 1, "units": "mm", "wall_height_mm": 2800,
            "source_ledger": [{"id": "fixture", "kind": "synthetic_fixture", "locator": "render prompt CLI test fixture"}],
            "rooms": [{"id": "R01", "name": "测试房间", "source_refs": ["fixture"],
                       "polygon": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                       "walls": [{"source_refs": ["fixture"]} for _ in range(4)]}],
            "furniture": [], "fixed_elements": [],
        }), encoding="utf-8")
        self.brief = self.root / "brief.json"
        self.brief.write_text(json.dumps({
            "schema_version": 1, "project": "合成验收",
            "views": [{"room_id": "R01", "style": "简洁", "materials": ["木饰面"],
                       "lighting": "柔和", "camera": "跟随Blender参考",
                       "aspect_ratio": "16:9", "furniture": [], "keep": [], "avoid": []}],
        }), encoding="utf-8")
        self.approval = self.root / "approval.json"
        self.approval.write_text(json.dumps(create_receipt(self.geometry, "合成测试确认")), encoding="utf-8")
        self.script = Path(__file__).with_name("build_render_prompt.py")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, output, *extra):
        return subprocess.run(
            [sys.executable, str(self.script), "--geometry", str(self.geometry),
             "--brief", str(self.brief), "--approval", str(self.approval),
             "--output", str(output), *extra, "--execute"],
            capture_output=True, text=True,
        )

    def test_cli_writes_separate_prompt_and_default_checklist(self):
        prompt = self.root / "image2.md"
        result = self.run_cli(prompt)
        self.assertEqual(result.returncode, 0, result.stderr)
        checklist = self.root / "image2-geometry-checklist.md"
        self.assertTrue(prompt.is_file())
        self.assertTrue(checklist.is_file())
        self.assertIn("复制到 Codex imagegen 的提示词", prompt.read_text(encoding="utf-8"))
        self.assertNotIn("效果图几何验收清单", prompt.read_text(encoding="utf-8"))
        self.assertIn("效果图几何验收清单", checklist.read_text(encoding="utf-8"))

    def test_cli_does_not_write_either_file_if_checklist_would_be_overwritten(self):
        prompt = self.root / "image2.md"
        checklist = self.root / "image2-geometry-checklist.md"
        checklist.write_text("keep", encoding="utf-8")
        result = self.run_cli(prompt)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(prompt.exists())
        self.assertEqual(checklist.read_text(encoding="utf-8"), "keep")

    def test_cli_requires_prompt_and_checklist_to_share_atomic_output_directory(self):
        prompt = self.root / "image2.md"
        checklist = self.root / "separate" / "checklist.md"
        result = self.run_cli(prompt, "--audit-output", str(checklist))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("same directory for atomic delivery", result.stderr)
        self.assertFalse(prompt.exists())
        self.assertFalse(checklist.exists())

if __name__ == "__main__":
    unittest.main()
