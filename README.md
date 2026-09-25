# 熊老湿装修设计（Codex Skill）

面向 Codex 的室内设计辅助技能：按用户确认的小步骤，把 CAD 截图、平面布局讨论、简易 DXF 参考、Blender 空间模型与效果图提示串成可追溯工作流。

## 安装

```sh
git clone https://github.com/xionglaoshi/xiong-interior-design.git "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
cd "${CODEX_HOME:-$HOME/.codex}/skills/xiong-interior-design"
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

安装完成后重启 Codex，在会话中调用 `$xiong-interior-design`。技能自身文件夹即安装目录；示例脚本使用 `python3`，如需隔离依赖，可改用 `.venv/bin/python`。

## 依赖

- 必需：Python 3.10+、Pillow、ezdxf。
- Blender 建模/渲染参考图：Blender 5.2 或兼容版本，并确保 `blender` 命令可用；可用 `BLENDER_BIN` 指向可执行文件。
- 浏览器交互测试：Chrome 或 Chromium、`websocket-client`（安装 `requirements-test.txt`）。
- AI 图像表现：Codex 的 `imagegen` 技能，仅在需要生成效果图时使用。
- 不需要付费 CAD 软件。当前工作流以用户提供的清晰 CAD 截图及用户确认作为依据；不承诺自动解码 DWG、自动判断承重墙或生成施工图。

安装可选测试依赖并运行完整回归：

```sh
python -m pip install -r requirements-test.txt
python3 scripts/run_skill_tests.py --blender /path/to/blender
```

不运行 Blender 测试可加 `--skip-blender`；如 Chrome 不在常见安装位置，可设置 `CHROME_BIN`。安装在 macOS 的 Blender 常见路径为 `/Applications/Blender.app/Contents/MacOS/Blender`。

## 能力与边界

1. 清理 CAD 截图的外围边缘、制作可核验的现状图；图像模型候选必须对照且由用户确认。
2. 从空白模板生成“现状图 / 平面布置图”双图层 HTML 注释器，支持批注导入导出。
3. 把已校准、来源可追溯且用户确认的几何导出为简易 DXF 参考图。
4. 从同一份已确认几何生成 Blender 场景、GLB 浏览页及房间参考图；家具为辨识空间关系的简化代理体。
5. 为 Codex imagegen 准备有几何约束的提示词和验收清单。

本技能不自动识别结构属性，不代替 CAD 原图、设计师审查、结构/消防/机电审查或施工图。截图不能单独证明尺寸和承重属性；未知内容应标记待确认。项目输入和产物应保存在各自对话的 workspace，不应放入技能仓库。

## 仓库结构

- `SKILL.md`：Codex 技能入口和工作规则。
- `scripts/`：可复用处理、导出、校验脚本及自动化测试。
- `assets/drawing-viewer/`：空白双图层注释器模板。
- `assets/vendor/`：离线查看器运行库及第三方许可证。
- `references/`：按工作阶段查阅的流程、数据格式和复用说明。

## 许可

本仓库的许可条款见根目录 `LICENSE`。`assets/vendor/` 中第三方组件保留其各自许可声明。
