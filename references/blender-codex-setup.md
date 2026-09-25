# Blender 与 Codex 连接及素材配置

本指南配置可选的 Blender 交互控制，并准备轻量、可追溯的本地素材库。它不修改项目几何或自动进行室内设计。技能中的 Blender 后台脚本不依赖 MCP；仅当 Codex 需要直接查看/操作当前打开的 Blender 场景时才配置 MCP。

## 架构与前置条件

推荐使用 Blender 官方实验性 Blender Lab MCP（不是 Blender 自带的常驻功能，也不是第三方 `blender-mcp-bridge`）。由三部分组成：Codex 通过 stdio 启动 `blender-mcp`；该 MCP 进程经本机 TCP 连接 Blender 中启用的 MCP 扩展；扩展在 Blender 进程内处理请求。官方扩展当前要求 Blender 5.1 或更新版本；安装前应在官方页面确认当前兼容要求。

官方资料：

- [Blender Lab MCP 介绍、安全说明与安装](https://www.blender.org/lab/mcp-server/)
- [Blender Lab MCP 源码与安装说明](https://projects.blender.org/lab/blender_mcp)
- [Codex MCP 配置文档](https://developers.openai.com/learn/docs-mcp)
- [Codex 配置参考](https://github.com/openai/codex/blob/main/codex-rs/config.md)

## 安装与配置

### 1. 安装 Blender 扩展

1. 打开 Blender，进入 **编辑 → 偏好设置 → 扩展**（不同版本的菜单名称可能略有差异）。
2. 在扩展仓库中添加 Blender Lab 官方仓库：`https://lab.blender.org/`。
3. 搜索 **MCP**，安装并启用官方 Blender Lab 扩展。
4. 在扩展偏好设置中启动 MCP 服务。保持默认 `localhost:9876`，不要绑定到局域网或公网；如启用自动启动，先确认只在本机监听。

如果当前 Blender 版本的界面不同，按 Blender Lab MCP 官方安装页操作；不要用名称相似的第三方扩展替代官方组件。

### 2. 安装独立 MCP 服务

需要 Python 3.10 或更新版本。建议为 MCP 服务单独建虚拟环境，避免污染技能依赖。以下路径应替换为当前用户的绝对路径：

```sh
python3 -m venv /绝对路径/codex-mcp/blender/.venv
/绝对路径/codex-mcp/blender/.venv/bin/python -m pip install \
  'git+https://projects.blender.org/lab/blender_mcp.git#subdirectory=mcp'
```

安装后应存在 `/绝对路径/codex-mcp/blender/.venv/bin/blender-mcp` 可执行文件（Windows 使用虚拟环境的 `Scripts` 目录）。官方包以 stdio 方式运行，通常由 Codex 启动和管理，不要另行把它作为网络服务暴露。

### 3. 注册到 Codex

在终端将下面命令中的路径换成上一步的绝对路径：

```sh
codex mcp add blender -- /绝对路径/codex-mcp/blender/.venv/bin/blender-mcp
```

该命令会修改当前用户的 Codex MCP 配置。也可在 `~/.codex/config.toml` 手动添加：

```toml
[mcp_servers.blender]
command = "/绝对路径/codex-mcp/blender/.venv/bin/blender-mcp"
args = []
```

修改配置后重启 Codex 或按客户端提供的方式重载 MCP。Codex Desktop 和 Codex CLI 应使用同一用户配置；若在不同系统账户或不同 `CODEX_HOME` 下运行，需在实际使用的配置位置登记。

## 验证与排障

必须分三层确认，不能只看配置命令成功：

1. `codex mcp list` 中有 `blender`：仅证明 Codex 已登记该服务。
2. Blender 已打开、扩展已启用且服务状态为运行中：证明 Blender 端已启动。
3. 在 Codex 中要求执行只读场景查询，例如“列出当前 Blender 场景中的对象名称”，并确认返回内容与 Blender 界面一致：这才证明端到端连通。

若第 3 步失败，依次检查 Blender 扩展是否运行、MCP 主机/端口是否一致、端口 `9876` 是否被占用、Codex 是否已重载配置，以及 Codex 启动的虚拟环境中 `blender-mcp` 是否可执行。默认服务使用本机回环地址；不要通过关闭系统防火墙或改成 `0.0.0.0` 来排障。

## 安全边界

官方 MCP 可在 Blender 内执行由模型生成的 Python 代码；官方说明指出，它没有防止删除数据或向远端发送数据的完整保护。将其视为高权限本地自动化：仅处理可恢复的工作副本，重要操作先保存备份；删除对象、覆盖文件、安装插件、联网下载或运行不透明脚本前先检查影响并取得用户指令。不得向公网开放 Blender TCP 服务，也不要把含隐私或凭证的文件作为测试场景。

## Blender 素材：必装与可选

**必装项：** Blender 本体即可。技能生成房间、直墙和桌椅沙发代理体时使用程序化几何；不要求安装外部家具包、材质包、灯光包或插件。Blender 自带的 Essentials 资产库可用于通用起步资产。

**可选轻量资产：**

- 材质与环境贴图：优先从 [Poly Haven](https://polyhaven.com/) 挑选少量木材、石材/瓷砖、涂料、织物、金属 PBR 材质，以及少量室内日光和中性棚拍 HDRI。其素材采用 CC0；仍建议在项目素材清单中记录来源与下载日期。
- 其他纹理：可从 [ambientCG](https://ambientcg.com/) 选用 CC0 材质/纹理；下载前核对该站当前许可和每个素材的元数据。
- 家具：先用技能内置的低多边形代理体表达沙发、桌、椅和柜体。只有用户要求更具体的外形时，再挑选少量许可清晰、比例合适的通用模型；不能让高精度模型拖慢平面关系验证。
- 办公与工业产品：使用 Blender Essentials、基本几何和少量 CC0 材质即可起步；按具体项目再补办公椅/桌、产品展示用中性棚灯或合适 HDRI，不预先下载大型素材合集。

素材来源：[Poly Haven 许可](https://polyhaven.com/license)、[Blender Asset Libraries 手册](https://docs.blender.org/manual/en/5.2/editors/preferences/asset_libraries.html)。第三方素材许可可能变化，下载时复核原始许可，不将“免费可下载”误当作可再分发或无条件商用。

## 素材库组织

将下载资产放在项目 workspace 或用户明确指定的共享素材目录，不放进技能仓库，也不混入 Blender 应用安装目录。可按用途分层：

```text
<workspace>/assets/blender/
├── hdri/
├── materials/
├── furniture/
└── product/
```

每个下载资产保留原文件名，并记录来源 URL、许可、下载日期；只下载当前任务确实需要的内容。Blender 的 **偏好设置 → 文件路径 → 资产库** 可把本地素材目录登记为 Asset Library，方便通过资产浏览器复用。交付 `.blend` 前检查纹理路径；可使用相对路径或在确认文件体积后打包纹理，避免把项目交付绑定到临时下载目录。

轻量预览优先用 1K–2K 贴图与低/中分辨率 HDRI；只在最终效果需要时增加分辨率。HDRI 是可选环境光来源，室内空间草模通常用简单区域灯更容易控制，不要求安装整套棚灯预设。
