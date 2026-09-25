# Blender 定稿模型 → 渲染提示词

只有在平面方案和对应 3D 模型已获用户确认后，才进入效果图阶段。Blender 是空间关系草模：房间/通道关系清楚，桌、椅、沙发等大件家具一眼可辨即可，不追求写实；Codex `imagegen` 技能负责接近真实的材质、灯光与氛围表现。渲染提示词改变视觉表现，不得借视觉创作重新设计平面或建筑几何。

## 两个输入

1. **模型几何 JSON v1**：`references/model-input-format.md`，提供房间边界、墙体、开口、家具占位及 CAD/用户确认固定构件的尺寸依据。
2. **渲染 brief JSON v1**：用户确认后逐视角填写，例如：

```json
{
  "schema_version": 1,
  "project": "项目名称",
  "views": [
    {
      "room_id": "R01",
      "imagegen_reference_mode": "room_views",
      "style": "用户确认的风格方向",
      "materials": ["用户确认的主要材质"],
      "furniture": ["用户确认的软装/设备表达"],
      "lighting": "用户确认的灯光氛围",
      "camera": "要复现的Blender镜头位置与视角",
      "aspect_ratio": "16:9",
      "keep": ["不得改变的设计要素"],
      "avoid": ["不希望出现的内容"],
      "reference_image": "/绝对路径/经确认的Blender剖切视图.png",
      "reference_full_image": "/绝对路径/经确认的Blender完整墙体透视图.png",
      "reference_top_image": "/绝对路径/经确认的Blender全墙顶视图.png",
      "reference_metadata": "/绝对路径/与三张图同批生成的相机JSON"
    }
  ]
}
```

`reference_image`、`reference_full_image`、`reference_top_image` 可选；如果缺失，产物会提醒传递提示词时附上已确认 Blender 截图。`room_views` 的三图齐全时必须同时提供 `reference_metadata`，或使用相机脚本默认的 `<room-id>-camera.json` 文件名；提示词生成器会校验元数据房间ID、geometry JSON SHA-256、三张图路径与各自文件SHA-256。任一版本/路径/内容不一致即停止，不能把旧版参考图配给新版几何。只提供部分参考图时只作视觉参考，不声称已自动锁定同一几何版本。例外：`top_layout_axonometric` 只需同版 `reference_top_image` 与 `reference_metadata`，并验证房间、几何版本、路径和图片哈希。不要只用文本让图像模型猜建筑几何。浏览器预览时先用“顶视布局”确认家具/开口关系，再用“斜视空间”选渲染角度。

默认 `imagegen_reference_mode: "room_views"` 使用该房间的三张成套参考图并锁定Blender镜头。若多视图拼贴或透视遮挡导致效果图难以核对，可改为 `"top_layout_axonometric"`：必须指定同版 `reference_top_image` 和 `reference_metadata`；生成器验证这张图的房间、几何版本及SHA-256，只将顶视图作为唯一图像附件，提示 imagegen 转换成高位轴测/开顶视角，不沿用顶视相机。此模式只测试平面拓扑、洞口及家具相对位置的视觉表达；不能验收高度、门扇或被遮挡细节，也不能证明尺寸精确。必须与顶视图和人工清单核对，逐一核对房间外轮廓和洞口数量/位置；单图轴测实测曾把一处门洞画成两个并改变房间比例，不能因家具可辨或画面美观而放行。未显示/无法确认的项目标为未通过。

要可复现地生成房间视角，可用 `scripts/render_blender_room_reference.py` 从已确认的 `.blend` 和同版 geometry JSON 出发，按房间 ID 与 `sw/se/ne/nw` 方位生成相机侧剖切 PNG、完整墙体 PNG、全墙顶视图、相机参数 JSON 和带相机/灯光的独立 `.blend` 副本。默认 `--view-profile interior` 使用较低机位与广角，使家具更易辨认；`--view-profile elevated` 保留高位总览，适合显示墙体高度与房间边界；可用 `--lens-mm` 覆盖焦段。相机在初始位置后会按输出画幅比例、房间边界和家具/固定构件实际包围框自动后退取景，`interior`优先完整包含地面边界和室内物件，`elevated`还纳入墙顶锚点；元数据记录`frame_fit_steps`及8%边缘留白。自动取景只改变镜头，不改变房间几何。剖切图只为可视化暂时隐藏相机侧两段边界墙，不删除/修改模型几何；完整墙体图与 `.blend` 保留所有墙。模型会保存源 geometry JSON 的 SHA-256；相机脚本严格核对哈希、房间边界元数据、家具ID及固定构件ID，版本不一致或旧模型缺少边界来源信息就拒绝渲染。脚本默认只预演；执行必须使用不存在的新输出目录，不改源 `.blend`；所有输出先在同盘临时目录生成，核验完整后再以目录重命名提交。视图显示所选房间的地面、家具、固定构件和边界，配简单软光；不是最终写实渲染。三张 PNG 必须配对使用：将剖切图路径填入 `reference_image`，完整墙体图填入 `reference_full_image`，顶视布局填入 `reference_top_image`，并把 metadata 的焦段/角位/预设填入 `camera`。不要把剖切误读为拆墙方案；梁等构件仍可能遮挡房间，应人工检查或旋转 `.blend` 相机，不得在确认模型中移除它们。

```sh
blender --background --factory-startup \
  --python scripts/render_blender_room_reference.py -- \
  --blend /绝对路径/已确认模型/interior-scene.blend \
  --geometry /绝对路径/已确认模型/geometry.json \
  --approval /绝对路径/项目workspace/geometry-approval.json \
  --room-id R01 --corner sw --view-profile interior \
  --output-dir /绝对路径/当前对话workspace/outputs/R01-camera-v1 \
  --execute
```

## 生成与使用

需要整层构图而非单间时，使用 `scripts/render_blender_global_reference.py`：它从同一份确认模型生成 `global-axonometric.png`（空间轴测关系）和 `global-top-layout.png`（正交顶视布局），并写出绑定 geometry/Blend 哈希与房间/家具/固定构件 ID 的 `global-reference.json`。输入模型与geometry JSON必须是同版，所有输出须在实际用户确认之后生成；执行时提供精确 `--approval`，默认只预演，拒绝覆盖已有目录，源 `.blend` 永不保存。顶视图临时隐藏立体墙/门窗代理并按确认几何绘制墙带/门垛/窗符号；轴测图仅在渲染时隐藏朝观察方向的边界墙段以改善室内可见性，元数据记录隐藏边段数，源模型保持完整。两图是给人和 imagegen 理解整体关系的草图，不是精确测量、施工图或结构判断。`scripts/test_global_reference_camera.py` 使用临时合成几何真实渲染两张PNG并检查尺寸/非空像素；默认自动清理，`--artifact-dir` 可将测试图留在workspace供目检。该测试不使用确认凭据或真实项目数据。

`render_blender_room_reference.py` 与 `build_render_prompt.py` 的实际写出模式都必须带 `--approval`；该凭据必须匹配当前 geometry JSON 的 SHA-256。默认预演仍可不带凭据；几何JSON有任何修改都要重新展示并取得用户确认。该凭据只记录几何确认，不等于用户已批准3D模型或已授权生成渲染提示词/效果图；这些阶段仍须分别取得用户确认和明确任务指令。

相机脚本除剖切透视和完整墙体透视外，同时输出全墙正交顶视 PNG（`<room>-camera-top-layout.png`）。该图在渲染期间暂时隐藏立体墙段/门窗代理，以用户确认的墙厚和开口尺寸生成平面符号；地面、桌椅沙发和固定构件临时切换为分类色，渲染后恢复材质，最终 `.blend` 不保存这些顶视表现覆盖。相机JSON同时记录输入几何哈希及三张PNG哈希。完整墙体透视图可能被近墙遮挡；顶视图用于核对完整房间边界、门窗空位和家具平面位置，但不是第三份模型几何或施工图。三图分别填入 `reference_image`、`reference_full_image`、`reference_top_image`，元数据路径填入 `reference_metadata`；无法核验的项目仍标未验收。

运行 `scripts/build_render_prompt.py`。默认仅预演；检查选中的房间、几何摘要和风格要求后，加 `--execute` 才在当前项目 workspace 写出两份 Markdown：`--output` 指向可复制给图像模型的提示词，默认另生成同目录 `<文件名>-geometry-checklist.md`；也可用 `--audit-output` 指定同一目录内的清单文件名。两份作为一个交付包先在同目录暂存、齐备后提交，任一已存在目标都会使整包拒绝写入。提示词文件单列 Blender PNG 参考图，人工验收清单独立保存，不混入提示词。该脚本只准备提示词和清单，不生成效果图。用户明确要求生成效果图且模型/风格/视角已确认后，必须实际调用 Codex `imagegen` 技能，把 Blender 视图作为图像参考并提交提示词；不要把“生成了提示词文件”表述成“效果图已生成”。生成结果需对照同版 Blender 模型检查；ImageGen 负责视觉表现，不是几何渲染器，可能改变细节。视觉相近不代表尺寸/拓扑一致；关键墙线、开口、固定构件、家具数量/类型/相对位置冲突时拒收并回到提示词或模型，不把生成图反向当几何依据。

多张参考图有时会诱发多视图拼贴，而不是单张效果图；多房间提示也可能被合成同一张图。因此多房间项目必须按房间分别生成：一次 ImageGen 请求只附一个房间的提示词段和该房间的 Blender 参考图，不得混入其他房间的提示词或图片。单间提示词须明确“仅一张、单一镜头、禁止拼贴/分屏/插图”；若仍输出拼图，可按 `top_layout_axonometric` 用顶视图单独生成轴测空间表现，作为一种视角替代，不作为严格几何渲染。最终验收仍须回看顶视布局与几何清单；任何被遮挡或无法逐项确认的墙线、洞口、家具位置均记为未通过，不以模型自述或一张美图替代几何核验。即使提示词要求只改材质，模型仍可能擅加地板、踢脚线、门套等输入中没有的细节；先视作未确认增项，不能把它们倒灌到已确认的 Blender 几何。

几何输入中每件家具的 `type` 会作为明确类别写入提示词与人工验收清单（如普通无扶手椅与带扶手椅），不能只依赖自由文本名称；名称、类型或实际模型不一致时，先修正并重新确认几何，再生成渲染提示词。新建几何时应为每件家具显式填写正确 `type`；省略时为兼容旧数据可能退化为通用方块代理，ImageGen 也可能将其画成方块，不能期待它仅凭名称恢复形状。`build_render_prompt.py` 对当前房间内缺失/不支持的家具类型会硬性拒绝生成提示词；旧数据可继续用于兼容性读取，但须明确补齐类型并重新确认新几何版本后才能进入 ImageGen 阶段。

生成提示词会将几何清单中的家具/设备实体作为逐件锁定对象，锁定类别、数量、尺寸、中心位置、朝向和占地；brief 的 `furniture` 描述只约束外观或软装，不得新增、删除、复制、替换或移动实体。可按用户指定表现抱枕、织物等轻软装，但不能以其遮盖家具本体或布局关系。

独立的“效果图几何验收清单”供人工对照，不要发给图像模型，也不以清单代替目检。`room_views` 清单列出洞口宽高、家具输入尺寸与固定构件；逐项对照同机位 Blender 参考。`top_layout_axonometric` 只核对图中可见的平面边界、开口位置/宽度、家具与固定构件的平面位置/占地；不以该图验收高度、门扇或遮挡细节。漏项、错位、擅自增项或无法确认的项目标为未通过，不作为设计依据。若图像模型持续改变几何，保留其为视觉变体或弃用，转交几何保真的 Blender 参考渲染，不得反向修改已确认模型迎合生成图。

输出至少要包含：几何依据摘要、必须锁定的墙线/边界/开口/构件/镜头，用户确认的风格/材质/家具/灯光、画幅比例、禁止的增删改造。若生成图与确认模型冲突，应退回模型或提示词，不将图像模型的自由补全当作设计决策。

渲染图是表现图，不是施工图。它不证明结构、尺寸、材料规格或规范符合性。
