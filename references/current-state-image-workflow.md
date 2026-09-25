# CAD 截图现状图准备

## 目的与边界

将用户从 CAD 看图软件提供的高清截图，做成清晰、可在平面注释器中使用的现状底图。先做可重复的裁切/色调处理；用户希望清理外围噪声时，可以用 Codex imagegen 生成单独候选图，但不得把它当CAD事实或默认可信底图。原截图和每版中间图只读保留，项目图像及核验材料放当前对话 workspace，不放技能目录。

## 小步操作

1. 核对截图是否完整覆盖装修范围、楼梯/电梯、外墙、门窗、房间和现有设施；分辨率不足或图面被遮挡时先请用户补图。
2. 截图若有 EXIF 方向标签，脚本会先按正常显示方向规范化；裁切框 `left,top,right,bottom` 以方向规范化后的左上角像素坐标为准。先预演查看原始尺寸、显示尺寸与方向；裁切只去图纸外的轴网/尺寸边缘，不得切到任何平面实体。
3. 运行 `prepare_existing_state.py` 预演；确认裁切、方向、色调与新目标路径后再 `--execute --report`。它记录源/输出SHA-256、原始/显示尺寸、方向、裁切映射、色调及未缩放信息。黑底CAD可选 `--tone cad-dark`；默认 `preserve`。这一步只处理外围边界和整体显示，不清除图内线段。
4. 如需AI清理，使用 Codex 内置 imagegen 编辑模式：本地PNG先用 `view_image` 显示到对话上下文，再明确指定它是 edit target；候选另存当前项目 `work/`，不得把生成图只留在默认生成目录或覆盖源图。提示保持画布尺寸、比例、朝向、所有工程线段及坐标位置，仅清理指定且完全处于平面实体外的轴网/尺寸/界面噪声。若噪声触碰或穿过平面实体、目标不确定，提示模型保留，不让模型猜删或补画。
5. 用 `scripts/compare_existing_state_images.py` 对确定性参考图与AI候选生成三栏核验图（参考、候选、50%叠加）及哈希记录。若模型只改变输出分辨率，只有长宽比相对差不超过0.1%时，才可显式传 `--normalize-uniform-scale --normalized-cleaned-output <新PNG>` 等比例规范到参考画布；记录缩放比例，不裁切、不补边、不配准、不非等比拉伸。长宽比超限即拒绝候选。无论是否缩放，核验图只提供目视辅助，不自动证明线条未移动。检查外墙、内墙、门窗、楼梯、电梯、卫生间、柱、房间轮廓/编号和细线；任何增删、位移、重构、模糊或无法判读都拒绝候选，改用参考图或请用户重截。
6. 只有助手逐项检查后且用户明确接受该版本，才运行 `scripts/record_cleaned_state_acceptance.py`，将最终候选（若规范化则使用新PNG）、原始AI候选、基准图、对照图、缩放变换和确认文字哈希绑定成注释器可用的现状图记录。脚本本身不能验证对话授权或几何，只记录；候选/对照/来源任一文件后续变化都会导致哈希核验失败。
7. 用最终PNG与配套记录生成双图层注释器；生成器再次核对哈希、尺寸与裁切映射。批注表达空间/功能意图；CAD和用户确认仍是结构及精确几何依据，不能以AI清洗图替代CAD。

## 命令

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 scripts/prepare_existing_state.py \
  --input /绝对路径/用户截图.png \
  --crop 120,80,1800,1200 \
  --tone cad-dark \
  --output /绝对路径/当前对话workspace/outputs/现状图-v1.png \
  --report /绝对路径/当前对话workspace/work/现状图-v1-校验记录.json
```

上面省略 `--execute` 时只预演；确认参数后追加 `--execute` 才写文件。不裁切时省略 `--crop`；白底/彩色图省略 `--tone` 或使用 `preserve`。脚本拒绝覆盖源图、既有目标或既有校验记录。HTML注释器配置还会独立绑定已清洗PNG的哈希及画布宽高，须核对其值与校验记录一致。

## 验收限制

脚本回读验证输出尺寸，并保证不缩放平面像素；这不等于几何准确性验收。像素裁切和灰阶反相不能清除图纸内部的杂线，也不会自动分离图层。遇到无法可靠区分的边缘轴网，不做自动连通域删除，以免误删墙线/门窗；保留并请用户确认裁切边界或另给局部截图。

AI清洗候选可能以相同尺寸重绘或移动线条；尺寸相同、哈希正确和50%叠加都不是几何正确证明。只有当前用户明确接受且逐项目检未见关键要素变化的候选，才可用于讨论；像素转CAD坐标仍需用CAD尺寸独立校准。AI不能用来判断承重、可拆改或结构属性。

等比例缩放只能统一画布尺寸，不能证明几何已配准。若叠加出现双边线、整体偏移或结构细节差异，拒绝候选并退回确定性参考图；不得以自动对齐掩盖差异。

## AI清洗后的对照与绑定

先生成对照图预演，确认后追加 `--execute`。对照图和记录建议放在项目 `work/`：

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 scripts/compare_existing_state_images.py \
  --reference /绝对路径/work/现状图-确定性参考.png \
  --cleaned /绝对路径/work/现状图-AI候选.png \
  --output /绝对路径/work/现状图-对照.png \
  --report /绝对路径/work/现状图-对照.json
```

若 imagegen 输出分辨率与参考不同但长宽比吻合，可追加 `--normalize-uniform-scale --normalized-cleaned-output /绝对路径/work/现状图-AI候选-规范尺寸.png`；对照报告/图片和规范尺寸候选会作为一个输出包写入。规范尺寸候选而非原始高分辨率模型输出用于后续绑定和注释器。

用户明确接受精确候选版本后，先预演并检查引用路径/哈希，再 `--execute` 写绑定记录。它的 `--confirmation` 应简短记载当前对话的用户确认，不可由模型自行代填：

```sh
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 scripts/record_cleaned_state_acceptance.py \
  --reference-report /绝对路径/outputs/现状图-确定性记录.json \
  --reference /绝对路径/outputs/现状图-确定性参考.png \
  --cleaned /绝对路径/work/现状图-AI候选.png \
  --comparison-report /绝对路径/work/现状图-对照.json \
  --confirmation "用户在当前对话明确接受现状图-AI候选.png" \
  --output /绝对路径/work/现状图-AI候选-校验记录.json
```

然后将 `--cleaned` PNG 与该JSON传给注释器生成器。两项新脚本默认只预演；拒绝覆盖；引用哈希或尺寸不一致即停止。输出记录会嵌入注释器配置和批注JSON来源摘要中。
