---
name: xiong-interior-design
description: 熊翔装修设计：按小步确认协助CAD/高清图纸核对、平面布局批注、简易DXF参考、Blender三维模型与渲染提示词；CAD和用户确认决定几何，不用于未经核验的施工图认证。
---

# 熊翔装修设计

以真实工程底图和用户逐步确认作为设计依据。现有可复用能力包括截图现状图处理、双图层批注器生成、简易参考DXF，以及从人工确认的房间几何生成 Blender 场景/GLB 和交互浏览页；这些是概念方案工具，不是自动读图、自动设计或施工级交付流水线。

## 工作方式

- 每轮完成用户指定的一个小步骤，交付后等反馈；不把改查看器变成重做布局，不把平面布局批准当作效果图开工指令。
- geometry JSON 草稿与用户确认分开保存。注释器 JSON 可用 `scripts/summarize_floorplan_annotations.py` 生成逐项像素级批注来源索引及 `source_ledger`，该工具不解释意图、不换算CAD尺寸、不生成几何。先使 `scripts/audit_geometry_sources.py` 返回完整，再由用户明确确认当前精确版本，才用 `scripts/record_geometry_approval.py` 记录 SHA-256 凭据；正式写 DXF、Blender、相机参考或渲染提示词均传 `--approval`，消费凭据时也会复核来源链。几何字节改动会使凭据失效；凭据仅审计版本与对话确认记录，不构成数字签名、施工许可或下一阶段授权；每阶段仍须用户单独指令。
- 判断既有固定/结构条件只以用户提供的CAD资料及其确认标注为依据；截图仅用于呈现图面，不能单独证明承重或可拆改。CAD信息看不清时标记待确认，不猜测。
- 先核对装修范围、入口与到达区、楼梯、柱墙、门窗、竖井和现有设施。已知不可变要素保留；墙体性质不明时待核实，不凭颜色、线宽或图层名认定可拆。
- CAD转换成功不等于要素齐全。用原生CAD显示或可靠完整原图交叉核对，尤其楼梯、块参照和原始隔墙；缺项先补底图，不在简化缺项图上推断可行性。
- 尺寸、面积和动线必须注明依据；截图不天然具备测量精度。概念布局不等于施工图，几何碰撞检查不等于结构、消防或机电合规。
- 保留原图和版本；项目要求、房间数量与未经验证的判断留在项目中，不直接升格为技能规则。

开始或继续实际室内设计任务时，按 [小步交互演练](references/dialogue-playbook.md) 判定当前阶段；新项目先使用其中的“首轮最小输入”，之后每次只交付当前单步并在确认闸口停下。该指南不限制方案创意。

## 工具库

安装步骤和依赖清单见仓库根目录 [README.md](README.md)。

### CAD 截图现状图

按 [截图现状图流程](references/current-state-image-workflow.md) 处理CAD截图。`scripts/prepare_existing_state.py` 只做显式裁切、灰阶/反相等确定性操作；若需用 imagegen 清理指定的外围图框噪声，必须另存候选图、生成同画布对照并由用户目检确认，再用 `scripts/compare_existing_state_images.py` 和 `scripts/record_cleaned_state_acceptance.py` 留下可追溯记录。图像模型不能判定或改造工程几何；无法确认的区域保留原样。注释器生成器核验最终现状图、画布尺寸和来源记录。

### 图纸对照与标注 HTML

需要图纸切换与用户批注时，读取 [复用说明](references/drawing-viewer.md)。优先用 `scripts/build_floorplan_annotator.py` 将经校验记录绑定的现状图和布置图注入空白模板，生成自包含HTML；若只有现状图，布置图层会从现状图副本开始。

两层必须使用相同尺寸与坐标的图像画布，生成器拒绝自动拉伸不匹配图纸；不包含自动配准、差异识别、测距或自动生成布局。生成后切换对应的是同一画布位置，不等于CAD尺寸已核实。

注释器交付前可用 `scripts/test_floorplan_annotator_browser.py --html <生成的注释器HTML>` 在临时Chrome配置中验收鼠标矩形绘制、双层隔离、撤销、清空确认、刷新持久化和 JSON 导入/导出往返；并将真实浏览器下载的批注JSON交给来源索引器，核验标注几何与批注文件SHA-256被完整保留。该测试需本机 Chrome 与 Python `websocket-client`，对用户项目仅浏览读取；首次运行会清除临时浏览器配置内该页面的 localStorage。

像素批注要换算至CAD坐标时，按 [批注转几何流程](references/annotation-to-geometry-workflow.md) 操作。校准输入必须绑定本图层的SHA-256与画布尺寸；脚本核验源图片哈希/尺寸、锚点残差和画布边界。至少4个分布良好的CAD核实锚点，加1个独立尺寸复核；校准只产坐标，不推断房间或墙体属性。

建立或修改几何 JSON 时按输入规范记录逐项来源；运行 `scripts/audit_geometry_sources.py` 检查来源引用和证据文件哈希。它只审计可追溯性，不判断 CAD 解释或批注理解是否正确。

### Blender 基础建模

门扇默认不补；只有几何JSON明确包含经CAD/用户确认的 `door_swing` 才生成门扇代理。

输入规范见 [模型几何 JSON v1](references/model-input-format.md)，批注到几何的确认闸口见 [批注转几何流程](references/annotation-to-geometry-workflow.md)，脚本为 `scripts/build_blender_scene.py`。它根据明确输入生成简化房间地面、直线墙、矩形门窗洞口、门框与着色玻璃代理、家具占位体，以及 CAD/用户确认后录入的柱/竖井/梁等矩形固定构件，并输出 `.blend`、`.glb`、浏览器查看页及 `使用说明.md`；说明文件列出配套文件、浏览器启动方式、房间和几何版本哈希，并明确非施工图边界。模型与离线依赖先在输出目录同盘的暂存区完整生成，再提交到目标目录；提交失败时尝试回滚已替换文件。查看页可整体总览、按房间聚焦、切换顶视布局/斜视空间，并自由旋转缩放。`.blend` 按地面/墙体/家具/固定构件/门窗及房间建立 Collection，独立对象附来源属性；GLB节点 extras 也保留角色、房间及家具/构件来源ID，便于后续程序核验；模型仍不含完整材质资产、灯光和渲染机位。`scripts/geometry_utils.py` 为 Blender、DXF 和渲染提示词提供统一几何校验，并负责共墙去重；自交/重叠房间、越界洞口/家具/固定构件或共墙定义冲突都会阻止生成。顶视布局用于读平面家具关系，斜视空间展示房间体量但完整墙体可能遮挡家具；二者都是观察模式，不替代完整 `.blend` 几何。家具为参数化代理体，不是精细产品模型。查看页使用随产物一并复制的官方 `<model-viewer>` 4.3.1 Web Component；运行时加载本地脚本，不要求外网。

查看页另提供“隐藏墙体 / 显示墙体”观察开关，仅在 GLB 含独立 `Walls - soft white` 材质时启用；通过材质透明度切换，不删除或改写 `.glb` / `.blend` 几何，刷新页面即回到完整墙体。该开关不是结构判断或拆墙工具。改动查看器模板后，可用 `scripts/test_model_viewer_template_browser.py --source-directory <合成模型目录>` 在临时目录及隔离无头浏览器中验证墙体显隐画面变化、恢复原材质、房间聚焦、视角切换、拖拽旋转、滚轮缩放及无外网请求；测试依赖本机 Chrome、`websocket-client` 与 Pillow。

从终端调用 Blender 时使用后台隔离启动；默认预演不写文件，执行时每版指定新输出目录，且默认拒绝覆盖已有模型和查看器资源。只有用户明确要求替换同名输出时才传 `--force`；脚本仅替换本次生成清单里的文件，输出目录内其他文件保留：

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
blender --background --factory-startup \
  --python scripts/build_blender_scene.py -- \
  --input /绝对路径/项目模型.json \
  --approval /绝对路径/项目workspace/geometry-approval.json \
  --output-dir /绝对路径/当前对话workspace/outputs/模型版本 \
  --execute
```

浏览器预览页、GLB、固定版 `model-viewer` 脚本及第三方许可文件同目录；查看器不依赖运行时外网。因浏览器本地文件跨域限制，按需从该输出目录启动只绑定本机的静态服务：

```sh
python3 -m http.server 8890 --bind 127.0.0.1 --directory /绝对路径/模型版本
```

再打开 `http://127.0.0.1:8890/`。查看页基于随输出一并复制的官方 `model-viewer` 4.3.1 Web Component，页面启用 `camera-controls` 交互；对应 Apache-2.0、Lit BSD-3-Clause、Three.js MIT 许可文本均随页交付。每个新生成页面仍须验证运行时加载、交互和失败提示。

可用 `scripts/test_model_viewer_browser.py --directory <模型版本目录>` 在隔离无头 Chrome 中回归验收GLB加载、按房间聚焦、顶视/斜视/总览、鼠标旋转、滚轮缩放和外网请求；`scripts/test_model_viewer_failure_state.py` 还会注入损坏GLB，确认错误信息可见且不访问外网。需要本机 Chrome 与 Python `websocket-client`；这些测试可在GUI不可用时验证控件交互，但不能代替用户人工目检模型质量。

Blender场景单元冒烟测试运行方式：`blender -b --python scripts/test_blender_scene_geometry.py`。它只在临时后台进程内创建对象、不写文件，检查材质节点连线、门扇四种开向、未确认时不补门扇、窗代理，以及坐便器/冰箱/灶台代理零件是否留在声明包络内。

给图像模型准备房间几何参考图时，按 [渲染提示词流程](references/render-prompt-workflow.md) 用 `scripts/render_blender_room_reference.py` 从确认模型生成相机侧剖切、完整墙体透视、全墙顶视三张参考图及独立 `.blend` 相机副本；文件在同盘暂存目录成套生成并核验后，以新目录一次提交，失败清理暂存，不留可误认的半套参考图。默认 `interior` 优先完整纳入地面边界与家具/固定构件，`elevated` 另纳入墙顶范围。相机会按房间尺寸、对象外包框和成图比例自动取景，预设只改变镜头，不改模型；剖切只改变参考 PNG 的显示，不修改模型几何。顶视参考图渲染时临时以高对比分类色区分地面、桌、椅、沙发和固定构件，渲染后恢复材质；不会改变或保存进正式模型。修改相机/顶视表现流程后用 `scripts/test_camera_framing.py` 在Blender中回归不同房间长宽比、横竖画幅与分类色恢复。脚本以几何JSON SHA-256阻止模型/JSON版本错配。逐图目检后再写入渲染 brief，不凭空描述镜头。

需要整层空间关系参考时，使用 `scripts/render_blender_global_reference.py` 从同版已确认 `.blend` 与 geometry JSON 输出全局轴测图和正交顶视布局图。该工具核对几何确认凭据、模型内嵌 geometry SHA-256、房间及家具/固定构件 ID，默认只预演；执行写入新目录且不保存/覆盖源 `.blend`。轴测图只在渲染时隐藏朝向观察者的外墙/共墙边段以露出室内家具，顶视图保留完整墙线并使用同一组临时分类色；这两种视图都不改变源模型。全局图用于表达房间邻接、通道及家具大致关系，不能证明施工尺寸精度；生成后应目检遮挡，再交给 imagegen 作表现参考。用 `scripts/test_global_reference_camera.py` 回归双房间镜头适配及轴测/顶视PNG真实渲染；默认写入系统临时目录并自动清理，可选 `--artifact-dir <workspace/work/新目录>` 留存供目检，不使用确认凭据或真实项目数据。

坐标、尺寸、洞口与固定构件须来自用户确认或可靠 CAD 数据；截图像素不直接充当精确尺寸。脚本不识别承重墙，不自动布局，不判断结构、法规、消防、机电或施工可行性。固定构件只支持房间内的矩形柱/竖井/梁代理体；跨房间或非矩形构件须拆分并人工核对。门框不含门扇/开启方向，窗玻璃为不透明色块代理；最终门窗做法须另行确认。运行前按规范逐步确认房间边界和开口；`.glb` 生成成功也只代表文件可导出，必须回读并检查几何、尺度、房间连通与浏览器展示后才能称为可交付。家具及固定构件均为参数化占位体，不是精细产品模型。房间聚焦使用多边形质心估算相机目标，非矩形或复杂边界需目视校正。浏览器预览必须实际试用“顶视布局”和“斜视空间”；完整墙体及高位梁可能遮挡家具，截图作为渲染参考前应人工旋到清楚且不改变房间边界与家具关系的角度。

### 简易 DXF 参考图

脚本 `scripts/export_reference_dxf.py` 消费同一份模型几何 JSON，以模型空间 1:1 毫米单位输出简化房间墙线、洞口标记、家具占位、已确认固定构件占地和“非施工图”注记，并与 Blender 共用墙段去重/冲突校验。文件显式设置公制单位、初始化模型空间/文件范围，打开线宽显示，便于 CAD 中按“范围缩放”找到图形并辨认线宽；不预设纸空间/打印比例，打印配置由设计师按用途决定。家具轮廓按旋转后的真实占地生成，标注放在旋转外包框下方并带 ID、名称和类别。若输入元素有 `source_refs`，相应 DXF 实体会写入 `XIONG_SOURCE` 扩展数据；CAD 中可选实体查看其来源ID，转换或另存后仍应抽查是否保留。概念注记另写入 `XIONG_META` 扩展数据，记录生成所依据的几何 JSON 精确 SHA-256；导出器回读时核验该值与已审批输入快照一致。默认只做预演；用户确认几何后生成匹配SHA-256的凭据，实际写出时需传 `--approval`，核对输入与输出路径后加 `--execute`。脚本把DXF先写入目标同盘临时目录，重新读取并通过DXF audit与毫米单位核验后再提交；默认拒绝覆盖既有文件，只有明确要求替换时才用 `--force`。测试回读显示设置/范围、来源元数据和实体坐标/图层。它不含开门方向、结构分类、尺寸标注、机电/消防或施工细节，交设计师参考前仍需按 CAD 原图和用户确认校核。

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 scripts/export_reference_dxf.py \
  --input /绝对路径/项目模型.json \
  --approval /绝对路径/项目workspace/geometry-approval.json \
  --output /绝对路径/当前对话workspace/outputs/模型版本/方案参考.dxf \
  --execute
```

DXF与Blender模型出自同一份确认几何后，可用 `scripts/audit_dxf_blender_overlay.py` 生成同毫米坐标的三栏核验图：DXF线稿、Blender网格俯投、两者叠加（重影提示错位）。叠加前会核对DXF注记与`.blend`场景中记录的 geometry JSON SHA-256；缺失或版本不一致即停止，避免比较不同版文件。默认预演，执行仅写新PNG，拒绝覆盖；它检查导出几何是否对齐，不判断原CAD或设计是否正确。

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 scripts/audit_dxf_blender_overlay.py \
  --dxf /绝对路径/方案参考.dxf \
  --blend /绝对路径/interior-scene.blend \
  --blender /Applications/Blender.app/Contents/MacOS/Blender \
  --output /绝对路径/当前对话workspace/work/dxf-blender-overlay.png \
  --execute
```

### 定稿模型的渲染提示词

仅在用户确认平面和3D模型后，读取 [渲染提示词流程](references/render-prompt-workflow.md)。效果图由 Codex `imagegen` 技能生成；用户明确要求效果图时应实际调用该技能，并将 Blender 参考图与提示词作为约束，不以仅交提示词替代生成。`scripts/build_render_prompt.py` 将确认的几何 JSON 与用户确认的风格 brief 分别生成可复制提示词 Markdown，以及独立的人工作图验收清单 Markdown；提示词和清单均明示/核对家具类型、数量、尺寸、位置与朝向并锁定逐件实体，brief 仅定义外观和软装；默认预演，`--execute` 后写入 workspace，拒绝覆盖。默认多视图模式外，还支持 `top_layout_axonometric`：只使用已核验版本的 Blender 全墙顶视图，生成高位轴测/开顶视觉草图；它只用于表现平面关系，不验收高度、门扇或遮挡细节。该脚本只准备提示词，不自动调用图像模型；生成效果图仍须有明确任务指令。图像模型只作表现，不是几何渲染器：生成后必须对照对应核验图与清单；任何关键边界、洞口、固定构件或家具缺失/错位，或因遮挡无法核验，均不得验收，可退回使用 Blender 的几何保真参考图。

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 scripts/build_render_prompt.py \
  --geometry /绝对路径/项目模型.json \
  --approval /绝对路径/项目workspace/geometry-approval.json \
  --brief /绝对路径/用户确认的渲染要求.json \
  --output /绝对路径/当前对话workspace/outputs/方案渲染提示词.md \
  --execute
```

### 后续能力的增补

随实际项目逐步完善本技能：优先完成用户当前小步骤；只记录已验证、可复现且可跨项目复用的方法。技能目录仅保存规范、脚本和空白模板，项目源图、中间文件与交付物存放在当前对话 workspace。涉及新软件、系统配置、外部上传或明显扩展范围仍单独征求同意。材质、灯光和图像模型提示的实际效果需按用户确认逐步验收，避免预先堆砌规则。

修改几何或DXF导出规则后运行 `scripts/test_geometry_utils.py` 和 `scripts/test_export_reference_dxf.py`；修改渲染提示词/参考图绑定后运行 `scripts/test_build_render_prompt.py`，并用 Blender 5.2.2 为合成房间重新输出相机图与元数据、实际生成提示词以核验版本链。发布前还需用相邻房间样例分别回读 DXF、重开 `.blend` 并在浏览器加载 GLB。完整回归入口 `scripts/run_skill_tests.py` 同时运行全局参考图的多房间相机测试。

需要完整技能回归时，从技能根目录运行 `python3 scripts/run_skill_tests.py`；若未安装 Blender，可用 `--skip-blender` 运行 Python 部分测试。浏览器集成测试还需 Chrome/Chromium 与 `websocket-client`。使用和排障说明见 [使用指南](references/usage-guide.md)。项目产物应放在当前项目自己的 workspace，不要提交到技能仓库。
