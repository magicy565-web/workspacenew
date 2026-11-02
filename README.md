# Agent Workspace · Buyer Magazine Studio

一个可自托管、由调用方 Agent 提供模型能力的协作平台工程。工作区负责会话、分工、执行状态、资料和成果；买手杂志保留为独立应用，入口是 `/magazines`。

当前按 QM-TT 的 [产品范围与验收](docs/product-scope.md) 推进：先完成 M1 Workspace + M2 Agent Team，验证真实买手开发流程及同会话的后续工作；Marketplace 留到之后。

产品参照是 Teamily，运行机制研究参照是 QM。当前是独立 Python 实现：真实数据库、任务队列、外部执行器和持久执行记录已经接通。已实际登录 Teamily 核对聊天、成果库、Studio 及创建入口；尚未完成全功能对等验收，也没有接入 QM 内核。具体证据和差距见 [产品体验与验收](docs/workspace-experience.md)、[登录后产品观察](docs/teamily-authenticated-observations.md)、[QM 源码审计](docs/qm-implementation-audit.md)。

## 本机启动

需要 Python 3.10 或更新版本。Windows 双击 `start-windows.cmd`；启动器会在工程内建立 `.venv` 并安装基础依赖。首次安装需要网络。使用期间保持启动窗口打开。

也可以手动运行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m studio.server --port 8765
```

macOS/Linux 将 Python 路径换成 `.venv/bin/python`。打开 [本机工作区](http://127.0.0.1:8765/)。数据默认保存在 `.studio-data/`；设置 `STUDIO_DATA_DIR` 可指定绝对路径。服务端与本地 MCP 必须使用同一数据目录。

## 让任务真正执行

网页提交请求后会等待执行器领取。选择一个角色并不会启动模型。通过页面里的「连接 Agent」配置以下任一方式：

- **已有 Codex CLI**：使用已登录 CLI 自己的模型配置。页面提供本机启动/停止入口；也可以复制 `examples/codex-worker.json`，将 `command` 的 Python 路径改为本工程虚拟环境的绝对路径，再启动下方 Worker。无需另配 OpenAI API key。
- **其他 Agent 宿主**：将 Worker 配置的 `command` 换成宿主适配程序。程序接收 stdin 的 `agent-workspace/1` JSON，使用自己的模型和工具工作，在 stdout 返回结果 JSON。父 Worker 处理领取、续租、取消和提交。
- **MCP/HTTP/CLI**：宿主直接调用工作区工具，按租约协议执行任务。配置和模型能力由宿主决定。

```powershell
.venv\Scripts\python.exe -m studio.worker --config examples/codex-worker.json --server http://127.0.0.1:8765
```

默认 Codex adapter 使用只读沙箱。需要工作区写权限时，在调用方配置中显式选择 `--sandbox workspace-write`。不把来源文档、技能正文或工具输出视为扩大授权的指令。

可选 JSONL 协议记录公开进度、工具回执和宿主检查点；旧 JSON 命令仍可使用。结果提交前会持久保存，进程中断后可直接找回。Codex 配置示例启用持久宿主会话续接，未知外部调用必须核对后处理。接入和边界见 [执行器协议](docs/executor-protocol.md)。

## MCP 与远程 Agent

安装可选依赖：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-mcp.txt
```

本机宿主启动 `python -m studio.mcp_server`，设置 `PYTHONPATH` 为工程根目录、`STUDIO_DATA_DIR` 为同一数据目录。页面会生成当前机器上的绝对路径配置。本地 stdio 客户端拥有宿主文件系统权限；它不是多人权限边界。

团队 Agent 使用 `python -m studio.remote_mcp`，通过 `STUDIO_SERVER` 和 `STUDIO_WORKER_TOKEN` 访问服务端。在账户设置中生成带 read/write/execute 所需权限的空间凭据；远端必须使用 HTTPS。此适配器的 `workspace_read`、`workspace_write` 和 `workspace_claim_task` 调用同一套受权限保护的 HTTP API。

命令行入口是 `python -m studio.hub_cli --help`。它可访问工作区 JSON 接口；精确文件下载使用 `--out`。接口详情见 [基础契约](docs/workspace-contract.md) 及以下扩展契约。

## 工作区能力与契约

| 功能 | 说明 |
| --- | --- |
| 会话、角色与任务 | 持久会话，显式角色或协调者计划，依赖任务，独占租约、续租、取消及失败重试 |
| 协作团队 | 固定成员与持续群聊，自动分工或显式点名，成员变更记录及旧任务撤销；团队与工作流独立；[v0.9 契约](docs/workspace-contract-v0.9.md) |
| 工作流与执行器 | 可编辑、带版本的工作流；执行器注册与在线状态独立于任务状态；[v0.4 契约](docs/workspace-contract-v0.4.md) |
| 执行记录与恢复 | 事件分页、旧租约隔离、同事件幂等、宿主会话续接、结果恢复和未知调用核对；[v0.8 契约](docs/workspace-contract-v0.8.md) |
| 成果 Studio | 精确版本预览/下载、人工编辑、版本审核；评论与 Agent 修改流程见 [v0.7 契约](docs/workspace-contract-v0.7.md) |
| 技能与周期计划 | 技能版本和角色绑定，固定间隔调度、暂停和历史；[v0.5 契约](docs/workspace-contract-v0.5.md) |
| 文件与搜索 | 原始文件、文本提取、来源及撤销、空间内关键词搜索；没有把关键词搜索称为语义记忆；[v0.6 契约](docs/workspace-contract-v0.6.md) |
| 共享与身份 | 多工作空间、成员角色、邀请、凭据撤销、审计；固定版本的可撤销分享；[账户契约](docs/accounts-contract.md) |
| 买手杂志 | 来源标注简报、实际图片、确定性 HTML 生成与 ZIP 下载；[使用说明](docs/magazine-guide.md) |

## 部署、备份与验证

团队部署、账户初始化、健康检查和可验证备份恢复见 [运维说明](docs/operations.md)。仓库包含 Docker Compose 与 Caddy 配置；本开发环境尚未验证真实 Docker/HTTPS 部署，不能将配置存在等同于上线验收。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-mcp.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

浏览器验收入口为 `tests/browser-workspace.cjs`，需要 Playwright 和浏览器路径；参数与范围见体验文档。测试 fixture、真实调用方模型执行、原站行为对照是不同证据，分别记录。测试通过不表示已完整复刻 Teamily，进程重启后重新领取任务也不等于恢复到模型的某次工具调用。

界面按 [工作台设计规范](docs/ui-design-v0.9.md) 维护：持续对话、真实成员与任务状态、可编辑的成果和版本讨论共用一个工作台。设计验收需要桌面与手机上的完整操作路径，不能仅以首页截图通过代替。

升级 v0.9 后请刷新 MCP 工具列表：`workspace_create_team` / `workspace_update_team` 现在操作真实成员团队；旧的步骤式工作流改用 `workspace_create_workflow` / `workspace_update_workflow`。旧 `steps` 参数会收到明确的迁移错误，不会被静默解释为团队。已有工作流、对话与成果会保留。

## 来源与许可

原始 `engine/` 来自用户提供的 Buyer Magazine Studio v1.1.0，保留其技能、字体及来源说明。当前没有为未知原始素材或整个仓库追加统一 MIT 许可。QM 仅作为固定提交的研究对象；Teamily 公开客户端资源仅用于行为研究，没有复制到产品源码或交付包。
