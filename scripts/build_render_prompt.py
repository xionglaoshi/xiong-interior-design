#!/usr/bin/env python3
"""Build a model-grounded rendering prompt from approved geometry and style brief."""
import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path
from geometry_utils import FURNITURE_TYPES, furniture_type_label, validate_geometry
from geometry_approval import validate_receipt_snapshot
from atomic_output_bundle import commit_output_bundle, preflight_output_bundle

def furniture_label(item):
    return furniture_type_label(item.get("type", "box"))


def polygon_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] -
                   poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly))) / 2)


def validate_brief(brief, room_ids):
    if brief.get("schema_version") != 1:
        raise ValueError("Render brief must use schema_version=1")
    views = brief.get("views", [])
    if not views:
        raise ValueError("Render brief contains no views")
    seen = set()
    for view in views:
        rid = view.get("room_id")
        if rid not in room_ids:
            raise ValueError(f"View references unknown room: {rid}")
        if rid in seen:
            raise ValueError(f"Only one view per room is allowed in this brief: {rid}")
        seen.add(rid)
        reference_mode = view.get("imagegen_reference_mode", "room_views")
        if reference_mode not in {"room_views", "top_layout_axonometric"}:
            raise ValueError(f"View {rid} has unsupported imagegen_reference_mode: {reference_mode}")
        if reference_mode == "top_layout_axonometric" and not view.get("reference_top_image"):
            raise ValueError(f"View {rid} top_layout_axonometric mode requires reference_top_image")
        for key in ("style", "lighting", "camera", "aspect_ratio"):
            if not isinstance(view.get(key), str) or not view[key].strip():
                raise ValueError(f"View {rid} requires a non-empty {key}")
        if not isinstance(view.get("materials"), list) or not view["materials"]:
            raise ValueError(f"View {rid} requires at least one material direction")
        for key in ("furniture", "keep", "avoid"):
            if not isinstance(view.get(key, []), list):
                raise ValueError(f"View {rid}: {key} must be a list")


def validate_reference_bundle(geometry_hash, brief):
    """Reject missing/stale Blender reference sets when all three views are supplied."""
    reference_map = {
        "reference_image": "reference_image",
        "reference_full_image": "full_geometry_image",
        "reference_top_image": "top_geometry_image",
    }
    for view in brief.get("views", []):
        paths = {}
        for brief_key in reference_map:
            raw_path = view.get(brief_key)
            if raw_path:
                path = Path(raw_path).expanduser()
                if not path.is_absolute() or not path.is_file():
                    raise ValueError(f"View {view['room_id']} reference image missing or not absolute: {raw_path}")
                paths[brief_key] = path.resolve()
        if not paths:
            continue

        metadata_value = view.get("reference_metadata")
        if metadata_value:
            metadata_path = Path(metadata_value).expanduser()
        elif len(paths) == len(reference_map):
            image_path = paths["reference_image"]
            suffix = "-camera-reference.png"
            if not image_path.name.endswith(suffix):
                raise ValueError(f"View {view['room_id']} requires reference_metadata for nonstandard reference filenames")
            metadata_path = image_path.with_name(image_path.name[:-len(suffix)] + "-camera.json")
        elif view.get("imagegen_reference_mode") == "top_layout_axonometric":
            raise ValueError(f"View {view['room_id']} top-layout ImageGen reference requires reference_metadata")
        else:
            continue
        if not metadata_path.is_absolute() or not metadata_path.is_file():
            raise ValueError(f"View {view['room_id']} Blender camera metadata not found: {metadata_path}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"View {view['room_id']} Blender camera metadata cannot be read: {metadata_path}") from exc
        if metadata.get("room_id") != view["room_id"]:
            raise ValueError(f"View {view['room_id']} reference metadata belongs to room {metadata.get('room_id')!r}")
        if metadata.get("geometry_sha256") != geometry_hash:
            raise ValueError(f"View {view['room_id']} Blender references were rendered from a different geometry JSON")
        for brief_key, path in paths.items():
            metadata_key = reference_map[brief_key]
            metadata_image = metadata.get(metadata_key)
            if not metadata_image or Path(metadata_image).expanduser().resolve() != path:
                raise ValueError(f"View {view['room_id']} reference path does not match camera metadata: {brief_key}")
        image_hashes = metadata.get("images_sha256")
        if not isinstance(image_hashes, dict):
            raise ValueError(f"View {view['room_id']} camera metadata lacks reference image hashes; rerender with the current camera script")
        for brief_key, path in paths.items():
            metadata_key = reference_map[brief_key]
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if image_hashes.get(metadata_key) != digest:
                raise ValueError(f"View {view['room_id']} reference image changed since Blender camera metadata was written: {brief_key}")
        view["_camera_metadata"] = metadata


def bullet_list(values, default):
    return "\n".join(f"- {item}" for item in values) if values else f"- {default}"


def build_geometry_audit(room, geometry, reference_mode="room_views"):
    """Create a human acceptance checklist; generated pixels are not geometry authority."""
    room_id = room["id"]
    walls = room.get("walls", [{} for _ in room["polygon"]])
    openings = [(index, opening) for index, wall in enumerate(walls, start=1)
                for opening in wall.get("openings", [])]
    furniture = [item for item in geometry.get("furniture", []) if item.get("room_id") == room_id]
    fixed = [item for item in geometry.get("fixed_elements", []) if item.get("room_id") == room_id]
    checks = [
        (f"- [ ] 房间边界/转角：输入 {len(room['polygon'])} 个顶点；图像为轴测新视角，需与 Blender 顶视图核对平面拓扑，禁止增删或移动边界。"
         if reference_mode == "top_layout_axonometric" else
         f"- [ ] 房间边界/转角：输入 {len(room['polygon'])} 个顶点；与 Blender 同机位参考逐段对照，禁止增删或移动边界."),
        (f"- [ ] 洞口：共 {len(openings)} 处；只核对平面边号、类型、起点与宽度；不从该视角验收高度。"
         if reference_mode == "top_layout_axonometric" else
         f"- [ ] 洞口：共 {len(openings)} 处；逐项核对边号、类型、起点与宽度。"),
    ]
    for edge_index, opening in openings:
        opening_check = (
            f"  - [ ] 边{edge_index} {opening.get('kind', 'opening')}：起点 {opening['start_mm']} mm，宽 {opening['width_mm']} mm。"
            if reference_mode == "top_layout_axonometric" else
            f"  - [ ] 边{edge_index} {opening.get('kind', 'opening')}：起点 {opening['start_mm']} mm，"
            f"宽 {opening['width_mm']} mm，高 {opening['height_mm']} mm。"
        )
        checks.append(opening_check)
    checks.append(
        (f"- [ ] 家具/设备：共 {len(furniture)} 件；只核对 ID、数量、平面中心/朝向与占地；不核验高度。"
         if reference_mode == "top_layout_axonometric" else
         f"- [ ] 家具/设备：共 {len(furniture)} 件；核对 ID、数量、相对位置与占地。")
    )
    for item in furniture:
        size = item["size_mm"][:2] if reference_mode == "top_layout_axonometric" else item["size_mm"]
        checks.append(
            f"  - [ ] {item.get('id', item.get('name', '未命名'))}｜{item.get('name', item.get('type', '家具'))}"
            f"（类别：{furniture_label(item)}）："
            f"中心 {item['center_mm']} mm，外包 {size} mm。"
        )
    checks.append(f"- [ ] 固定构件：共 {len(fixed)} 件；不得缺失、移动或新增。")
    for item in fixed:
        size = item["size_mm"][:2] if reference_mode == "top_layout_axonometric" else item["size_mm"]
        checks.append(
            f"  - [ ] {item['id']}｜{item.get('name', item['type'])}：中心 {item['center_mm']} mm，"
            f"尺寸 {size} mm。"
        )
    checks.extend([
        "- [ ] 没有输入之外的门窗、墙体、大型家具、柱或其他固定构件。",
        ("- [ ] 轴测效果图不是 Blender 同机位图；将可见的平面边界、洞口及家具位置与顶视参考核对。高度、门扇和被遮挡细节不据此验收。"
         if reference_mode == "top_layout_axonometric" else
         "- [ ] 机位与 Blender 确认视图一致；任何透视遮挡导致无法核验的项目标为未通过，不视为默认通过."),
    ])
    intro = (
        "将效果图与 Blender 顶视图及模型清单核对；这是不同机位的视觉草图，不证明尺寸/高度。"
        if reference_mode == "top_layout_axonometric" else
        "将效果图与同机位 Blender 参考并排检查；生成成功不代表几何通过。"
    )
    return intro + "\n\n" + "\n".join(checks)


def split_render_handoff(document, view):
    """Separate the copyable image-model prompt from the human-only audit list."""
    prompt_heading = "## 可直接交给图像模型的提示词"
    audit_heading = "## 效果图几何验收清单（人工逐项核对，不附入图像模型提示词）"
    prompt_start = document.find(prompt_heading)
    audit_start = document.find(audit_heading)
    if prompt_start < 0 or audit_start < 0 or audit_start <= prompt_start:
        raise ValueError("Render document is missing prompt/audit section markers")

    title = document[:document.find("\n")].strip()
    summary = document[:prompt_start].strip()
    prompt_body = document[prompt_start + len(prompt_heading):audit_start].strip()
    if prompt_body.endswith("---"):
        prompt_body = prompt_body[:-3].rstrip()
    audit_body = document[audit_start + len(audit_heading):].strip()

    attachments = []
    image_roles = []
    reference_specs = [("reference_top_image", "Blender全墙顶视布局")] if view.get(
        "imagegen_reference_mode", "room_views") == "top_layout_axonometric" else [
            ("reference_image", "Blender室内剖切透视"),
            ("reference_full_image", "Blender完整墙体透视"),
            ("reference_top_image", "Blender全墙顶视布局"),
        ]
    image_roles = []
    for key, label in reference_specs:
        if view.get(key):
            image_index = len(attachments) + 1
            attachments.append(f"- 图像{image_index}：{label}（请在 Codex imagegen 中作为参考图片单独上传）：{view[key]}")
            image_roles.append(f"- 图像{image_index} = {label}。")
    attachment_text = "\n".join(attachments) or "- 请先选择本房间经确认的 Blender 参考 PNG 并作为 Codex imagegen 参考图单独上传。"
    role_text = "\n".join(image_roles) or "请按上传顺序使用已确认的 Blender 参考图，并以其空间几何和视角为准。"
    prompt_document = (
        f"{title.replace('渲染提示词', 'imagegen 提示词')}\n\n"
        "## 上传参考图\n\n"
        f"{attachment_text}\n\n"
        "## 复制到 Codex imagegen 的提示词\n\n"
        "多房间项目逐房间分别生成：本次只复制一个房间的提示词段，只上传该房间对应的 Blender 参考图；不要将不同房间的提示词或图片放进同一次 imagegen 请求。上面的参考图需在 Codex imagegen 中另行上传，并保持对应顺序。\n\n"
        f"参考图顺序与用途：\n{role_text}\n\n"
        f"{prompt_body}\n"
    )
    audit_document = (
        f"{title.replace('渲染提示词', '效果图人工验收清单')}\n\n"
        "以下内容供人对照，不要粘贴到 Codex imagegen 提示词中。\n\n"
        f"{summary}\n\n"
        f"## 效果图几何验收清单\n\n{audit_body}\n"
    )
    return prompt_document, audit_document


def build_prompt(geometry, brief, view):
    room = next(r for r in geometry["rooms"] if r["id"] == view["room_id"])
    poly = room["polygon"]
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    bounds_w = max(xs) - min(xs)
    bounds_h = max(ys) - min(ys)
    area = polygon_area(poly) / 1_000_000
    walls = room.get("walls", [{} for _ in poly])
    openings = []
    opening_visual_notes = []
    for index, wall in enumerate(walls, start=1):
        for op in wall.get("openings", []):
            openings.append(
                f"边 {index}：{op.get('kind', 'opening')}，沿墙起点 {op['start_mm']} mm，"
                f"宽 {op['width_mm']} mm，窗台高 {op.get('sill_mm', 0)} mm，"
                f"洞口高 {op['height_mm']} mm"
            )
            if op.get("kind") == "door":
                opening_visual_notes.append(f"边 {index} 门洞：门框占位；门扇及开启方向未输入，不能自行补定。")
            elif op.get("kind") == "window":
                opening_visual_notes.append(f"边 {index} 窗洞：模型中的边框/着色玻璃仅为几何占位，实际材质可按已确认风格表现。")
    furniture = [item for item in geometry.get("furniture", []) if item.get("room_id") == room["id"]]
    missing_types = [item.get("id", item.get("name", f"家具[{index}]"))
                     for index, item in enumerate(furniture, start=1)
                     if item.get("type") not in FURNITURE_TYPES]
    if missing_types:
        raise ValueError(
            f"Room {room['id']} has furniture without an explicit supported type: {missing_types}; "
            "set each furniture.type before preparing an ImageGen prompt"
        )
    furniture_lines = [
        f"{item.get('id', '')} {item.get('name', '家具占位')}（类别：{furniture_label(item)}）："
        f"中心 {item['center_mm']} mm，"
        f"体量 {item['size_mm']} mm，平面旋转 {item.get('rotation_deg', 0)}°"
        for item in furniture
    ]
    fixed_elements = [item for item in geometry.get("fixed_elements", []) if item.get("room_id") == room["id"]]
    fixed_lines = [
        f"{item['id']} {item.get('name', item['type'])}：中心 {item['center_mm']} mm，"
        f"平面占地/高度 {item['size_mm']} mm，底标高 {item.get('base_z_mm', 0)} mm，"
        f"旋转 {item.get('rotation_deg', 0)}°"
        for item in fixed_elements
    ]
    view_name = view.get("name", f"{room['id']} {room.get('name', '')}".strip())
    model_ref = view.get("reference_image", "")
    full_model_ref = view.get("reference_full_image", "")
    top_model_ref = view.get("reference_top_image", "")
    reference_mode = view.get("imagegen_reference_mode", "room_views")
    camera_instruction = view["camera"]
    if reference_mode == "top_layout_axonometric":
        camera_instruction = (
            "根据唯一的Blender全墙顶视布局转换为一个高位轴测/开顶空间视角；"
            "输入图只提供平面关系，不据此补造未标示的门扇、窗、隔断或其他构件"
        )
    camera_metadata = {} if reference_mode == "top_layout_axonometric" else view.get("_camera_metadata", {})
    if camera_metadata:
        facts = [
            f"Blender预设={camera_metadata.get('view_profile', '未记录')}",
            f"角位={camera_metadata.get('corner', '未记录')}",
            f"焦段={camera_metadata.get('lens_mm', '未记录')} mm",
        ]
        if camera_metadata.get("camera_location_m"):
            facts.append(f"机位={camera_metadata['camera_location_m']} m")
        if camera_metadata.get("camera_target_m"):
            facts.append(f"目标={camera_metadata['camera_target_m']} m")
        camera_instruction += "；与已校验Blender元数据一致（" + "，".join(facts) + "）"
    if reference_mode == "top_layout_axonometric":
        reference_note = (
            f"本次只附加一张几何参考图：Blender全墙顶视布局 {top_model_ref}。它锁定平面边界、洞口和家具相对位置；"
            "生成图要求转换成高位轴测/开顶空间表现，不沿用顶视相机。图像生成只能作为视觉草图，须回看顶视图逐项核对，不能据生成图确认尺寸。"
        )
    elif model_ref and full_model_ref and top_model_ref:
        reference_note = (
            f"本轮须同时附加三张同一Blender模型图：剖切透视 {model_ref}、完整墙体透视 {full_model_ref}、"
            f"全墙顶视 {top_model_ref}。三图应来自已校验的同一份几何版本元数据。剖切图用于理解室内，完整透视锁定体量，顶视图用于核对房间边界/洞口/平面家具位置；"
            "完整透视可能被近墙遮挡，不能以遮挡区域判定布局通过。"
        )
    elif model_ref and full_model_ref:
        reference_note = (
            f"本轮须同时附加两张同模型图：剖切视图 {model_ref}（仅为看清室内而隐藏相机侧两段墙）和完整墙体视图 {full_model_ref}。"
            "这组图片未自动校验与几何JSON的版本关系，仅作视觉参考。前者用于理解室内占用，后者锁定完整墙体；完整透视可能被近墙遮挡，宜再附完整顶视图核验平面布局。"
        )
    elif model_ref:
        reference_note = (
            f"本轮附加的 Blender 几何/镜头参考图：{model_ref}。渲染时必须将其作为空间、构件位置、比例和镜头构图依据；"
            "该单图未自动校验与几何JSON的版本关系，仅作视觉参考；若它为相机侧剖切图，还须同时提供完整墙体参考图。"
        )
    else:
        reference_note = "生成前必须随提示词附上这一房间经用户确认的 Blender 视图截图；没有该图时不要仅凭文字生成，以免图像模型擅自补造几何。"
    project = brief.get("project", geometry.get("project", "室内设计项目"))
    layout_lock = (
        "仅以提供的 Blender 顶视图锁定该房间平面边界、洞口和家具相对位置；"
        "将视角转换为高位轴测/开顶表现，不沿用顶视相机，且不得改变平面布局。"
        if reference_mode == "top_layout_axonometric" else
        "使用我同时提供的 Blender 模型视图作为该房间空间结构、墙体、门窗洞口、比例关系、现有家具位置和镜头构图的锁定依据；"
        "若提供全墙顶视图，以它核验平面边界、洞口和家具相对位置；只能进行材质、颜色、灯光、软装质感与写实度的视觉表现，不得重新设计空间。"
    )

    audit_checklist = build_geometry_audit(room, geometry, reference_mode)
    camera_guidance = (
        "该图为新生成的轴测视角，不沿用顶视相机；以输入图的平面关系为准"
        if reference_mode == "top_layout_axonometric" else
        "优先保持 Blender 参考图中的机位、视角、透视和构图"
    )
    strict_camera_rule = (
        "不得把顶视图误当作机位参考；不得改变已输入的平面布局"
        if reference_mode == "top_layout_axonometric" else
        "改变参考镜头的透视和机位"
    )

    return f"""# {project}｜{view_name} 渲染提示词

## 依据摘要
- 房间：{room.get('id')}｜{room.get('name', '')}
- 房间多边形面积：{area:.2f} ㎡（由已确认的毫米坐标计算）
- 平面边界包络尺寸：{bounds_w} × {bounds_h} mm（仅为包络尺寸）
- 墙高：{geometry.get('wall_height_mm', '未提供')} mm；墙厚：{room.get('wall_thickness_mm', '未提供')} mm
- 边界坐标（mm）：{json.dumps(poly, ensure_ascii=False)}
- 门窗/洞口：
{bullet_list(openings, '该房间输入中没有单独列出的洞口')}
- 门窗未决细节：
{bullet_list(opening_visual_notes, '输入中没有需要特别说明的门窗代理限制')}
- 家具占位：
{bullet_list(furniture_lines, '该房间输入中没有家具占位')}
- 固定构件（依据已确认模型输入，不作结构判定）：
{bullet_list(fixed_lines, '该房间输入中没有单独列出的固定构件')}
- 参考图：{reference_note}

## 可直接交给图像模型的提示词

请为“{project}”中的“{room.get('name', room['id'])}”制作一张高质量室内效果表现图。{layout_lock}

家具/设备实体严格按上方已确认几何清单逐件对应：不得新增、删除、复制或替换实体，不得改变类别、数量、尺寸、中心位置、朝向或占地；渲染 brief 中的家具文字只用于表达用户确认的外观、饰面或软装，不得覆盖几何清单。允许呈现 brief 指定的抱枕、织物等轻软装，但不得遮挡或伪装家具本体及其布局关系。

房间边界坐标（毫米）：{json.dumps(poly, ensure_ascii=False)}。边界包络约 {bounds_w} × {bounds_h} mm，坐标多边形面积约 {area:.2f} ㎡。墙高 {geometry.get('wall_height_mm', '未提供')} mm，墙厚 {room.get('wall_thickness_mm', '未提供')} mm。

门窗/洞口几何（沿对应房间边界边，从该边起点量取）：
{bullet_list(openings, '没有单独输入的洞口；不得自行增加。')}

家具/设备逐件几何（中心坐标、完整外包尺寸与朝向均须保持）：
{bullet_list(furniture_lines, '该房间未输入家具/设备；不得自行增加。')}

固定构件逐件几何（中心坐标、外包尺寸、底标高与朝向均须保持）：
{bullet_list(fixed_lines, '该房间未输入固定构件；不得自行增加。')}

风格方向：{view['style']}

材质/色彩：
{bullet_list(view['materials'], '严格依用户补充的材质要求')}

家具与设备表达：
{bullet_list(view.get('furniture', []), '只保留模型中已有的家具体量和位置，不自行添加大型设备')}

灯光氛围：{view['lighting']}

镜头要求：{camera_instruction}；{camera_guidance}。画幅比例：{view['aspect_ratio']}。

必须保留：
{bullet_list(view.get('keep', []), '全部房间边界、墙体、洞口和模型中已有布置')}

本房间固定构件：
{bullet_list(fixed_lines, '无已输入固定构件；不要擅自补造')}

严格禁止：改变房间轮廓或尺寸；移动、删除或新增墙体、门窗洞口、柱、楼梯、电梯、固定构件或已列明的家具/设备实体；改变洞口位置和宽度；为未确认门洞补定门扇/开启方向；擅自添加输入中没有的房间或大型设施；{strict_camera_rule}；添加文字、水印、尺寸标注或图纸符号。

避免事项：
{bullet_list(view.get('avoid', []), '不要出现与用户确认风格冲突的装饰或不在模型中的大型物件')}

请只输出一张、一个镜头的室内视觉效果图；禁止拼贴、分屏、接触表或多个视角组合。画面无文字、无水印。若参考模型和文字描述有冲突，以 Blender 几何参考和用户已确认的固定要素为准；不能确定时不要擅自改造，保留原状。

---

## 效果图几何验收清单（人工逐项核对，不附入图像模型提示词）

将效果图与同机位 Blender 参考并排检查；生成成功不代表几何通过。任一项缺失、错位、增删或被遮挡到无法判断，整张效果图退回，不作为方案依据；先用 Blender 几何视图交付，修正参考/提示词后再决定是否重试。

{audit_checklist}
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geometry", required=True, help="confirmed model geometry JSON v1")
    parser.add_argument("--brief", required=True, help="user-confirmed rendering brief JSON v1")
    parser.add_argument("--approval", help="receipt JSON for explicit approval of this exact geometry version")
    parser.add_argument("--output", required=True, help="new Markdown output path")
    parser.add_argument("--audit-output", help="separate human-only geometry checklist path in the same output directory; defaults beside --output")
    parser.add_argument("--execute", action="store_true", help="write the prompt; default is preview only")
    args = parser.parse_args()
    geometry_path = Path(args.geometry).expanduser().resolve()
    geometry_bytes = geometry_path.read_bytes()
    geometry = json.loads(geometry_bytes.decode("utf-8"))
    with open(args.brief, encoding="utf-8") as f:
        brief = json.load(f)
    validate_geometry(geometry)
    validate_brief(brief, {room["id"] for room in geometry["rooms"]})
    validate_reference_bundle(hashlib.sha256(geometry_bytes).hexdigest(), brief)
    documents = [split_render_handoff(build_prompt(geometry, brief, view), view)
                 for view in brief["views"]]
    content = "\n\n---\n\n".join(prompt for prompt, _ in documents)
    audit_content = "\n\n---\n\n".join(audit for _, audit in documents)
    audit_output = args.audit_output or str(Path(args.output).with_name(
        Path(args.output).stem + "-geometry-checklist.md"))
    prompt_path = Path(args.output).expanduser().resolve()
    audit_path = Path(audit_output).expanduser().resolve()
    if prompt_path == audit_path:
        raise ValueError("Prompt and audit outputs must be different files")
    if prompt_path.parent != audit_path.parent:
        raise ValueError("Prompt and geometry checklist must be in the same directory for atomic delivery")
    if not args.execute:
        print(f"DRY_RUN views={len(documents)} project={brief.get('project', geometry.get('project', ''))!r} prompt={args.output} audit={audit_output} approval_required_for_execute=true")
        return
    if not args.approval:
        raise ValueError("--approval is required for writing a render prompt based on geometry")
    validate_receipt_snapshot(geometry_path, args.approval, geometry_bytes)
    names = [prompt_path.name, audit_path.name]
    preflight_output_bundle(prompt_path.parent, names, force=False)
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{prompt_path.stem}.staging-", dir=str(prompt_path.parent)) as staging:
        staging_dir = Path(staging)
        (staging_dir / names[0]).write_text(content, encoding="utf-8")
        (staging_dir / names[1]).write_text(audit_content, encoding="utf-8")
        commit_output_bundle(staging_dir, prompt_path.parent, names, force=False)
    print(f"RENDER_HANDOFF_OK views={len(documents)} prompt={prompt_path} audit={audit_path}")


if __name__ == "__main__":
    main()
