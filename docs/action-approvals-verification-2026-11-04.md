# PR #1：CI 调查、兼容性修复与执行边界

日期：2026-11-04（北京时间）。基线：`035414832219cb7744755432bcf775542fa3eb3c`；实际修复提交：`b15e73c6aa34ebc529d41b9746cf602af2c96192`。沿用 `feature/action-approval-ledger-20261103`，不改写历史，不合并 main。

## CI 根因：作业未启动

[失败作业](https://github.com/magicy565-web/workspacenew/actions/runs/41544852034/job/125617820699) 的公开页面有明确错误注释：

> The job was not started because your account is locked due to a billing issue.

完整步骤接口返回空数组；日志下载返回 BlobNotFound。这与未启动相符。该次运行没有执行 checkout、Python 安装、依赖安装或测试，因此不能用它证明任何 Python 代码失败。另有 ubuntu-latest 迁移 Ubuntu 26 的 notice，它不是这次的错误。账单锁定须由账户所有者处理，修改源码无法解除；本次没有修改账户或付款。

`.github/workflows/tests.yml` 的既有 Python 3.10/3.11/3.12 矩阵和依赖文件有效。本次关闭矩阵 fail-fast，避免一个版本失败后取消其他版本；新增具名步骤、运行环境、依赖清单、一致性检查和测试日志 artifact。显式 Bash 启用 pipefail，tee 不会吞掉安装/测试失败。保留 ubuntu-latest，并记录实际 OS；不把日志改进称为账单修复。没有刻意反复重跑仍被锁定的旧作业。

## 独立发现的真实源码问题

基线在本环境完整回归：3.10、3.11 各 206 项通过；3.12 运行 206 项，有两条失败断言，均在 `test_image_has_no_invented_ocr_and_invalid_inputs_leave_no_files`：3000 层 JSON 被接收，随后文件数量断言也失败。该问题不在审批模块。

`studio/files.py::_extract` 只依赖 `json.loads` 的解释器递归上限；3.12 的解码器行为不同。本次明确 JSON 容器嵌套最多 32 层，并用迭代器栈验证，遍历栈空间随深度增长，不随宽度增长。超过深度时仍在写入附件/来源前拒绝。原件保真、非有限数值拒绝及现有失败断言均保留。新增数组/对象的 32、33、3000 层及字符串内括号/转义字符测试，没有放宽旧测试。

## 实际验证

以下均在干净虚拟环境、隔离临时 SQLite/文件目录执行；3.10/3.11 由 uv 安装 CPython 并按原 requirements 解析安装依赖，3.12 使用现有 CPython 和 pip。`uv pip check` 三套均无依赖冲突。固定解析结果见对应 dependencies 文件；没有为了通过测试更改 requirements 或降级依赖。

| CPython | 修复前完整回归 | 修复后审批专项 | 修复后完整回归 |
| --- | --- | --- | --- |
| 3.10.22 | 206 通过 | 39 通过 | 207 通过，63.135 秒 |
| 3.11.17 | 206 通过 | 39 通过 | 207 通过，62.436 秒 |
| 3.12.14 | 206 项，两条失败断言 | 39 通过 | 207 通过，62.230 秒 |

207 = 原有 167 + 审批 39 + 本次新增深度边界 1。专项与完整套件有重叠，不相加当作不同测试数量。完整回归包含真实本地 HTTP/MCP 协议及文件生成测试，但其中账户、Worker、授权回调和外部结果仍有协议 fixture；没有调用真实模型、发送邮件、联系客户、报价或付款。未改 UI，没有重跑已验收的模型/浏览器旅程，也没有把历史验收当作本次证据。Linux 本地结果不代替 GitHub 托管 runner 结果或 Windows 验证。

原始测试输出、修复前失败输出、环境和依赖清单在 [verification/2026-11-04](verification/2026-11-04/)。修复提交只改 files.py、相应测试和 CI；审批账本及其 39 项测试与基线一致。

## 审批接入的具体边界

**本批是 CI/兼容性修复及执行边界审计，尚未交付外部副作用的强制审批闭环。未经批准的外部宿主工具调用，目前仍不能由此平台保证阻断。**

| 现有位置 | 已有能力 / 缺口 | 正确接入边界 |
| --- | --- | --- |
| `accounts.identify/authorize/_refresh_principal` | 会话、角色、token scope 与实时撤销；普通 write/execute 不等于人工批准 | 复用账户身份。人工决定必须来自当前有效人工 session；禁止将 owner 的 bearer token、Worker owner 身份或请求 JSON 当人工身份 |
| `server.Handler._allowed` 与 `core.REQUEST_ACTOR` | 服务端选 workspace 数据目录；actor 只带 user/workspace/role/scopes，缺少足够的认证通道证明 | 增加可信的 session/token 通道上下文并实时刷新，不能从 body 填 `kind=human`；账本路径固定为当前 `core.data_root()/studio.sqlite3` |
| `hub._owned`、`hub_execution._active`、团队撤销逻辑 | 校验任务、租约、Worker、运行状态及取消 | 审批申请和消费绑定真实 task/execution/attempt/Worker；租约、团队、来源权限和预算策略变化应撤权或改变授权版本。不能只哈希角色字符串；还需防止撤销再恢复后的旧审批复活 |
| `ApprovalLedger` | 原子单次消费、摘要绑定、过期/撤销、实时回调；39 项测试通过 | 继续复用该账本。当前没有宿主 `Authority` 实现，不新增第二套角色/权限系统；跨账户 DB 与业务 DB 的撤权竞争须由接入层明确处理 |
| `hub_http.dispatch`、`mcp_server.workspace_write`、`remote_mcp.workspace_write` | 普通 JSON API / 转发；未注册审批路由 | HTTP 增加申请、读取、人工决定、撤销；MCP Worker 不开放人工决定。仅添加路由或 `consume` 令牌仍不能约束外部工具 |
| `worker._execute` 的 `subprocess.Popen` | 启动调用方命令，过滤工作区 token，租约续期/取消；宿主仍拥有自己的模型与工具 | 不能在任意任务启动前只批一次，就当作批准了任务中未知的每一次发信。必须在每个确切副作用发生前同步调用受控入口 |
| `codex_executor.build_command`、`CodexEvents.consume` | 只读沙箱默认值；CLI 工具事件为观察回执，`approval_policy=never` 不是本平台审批桥 | 不能在收到 `tool_call` 后才阻断。现有 CLI adapter 没有同步审批 hook；宿主自带 MCP、网络与业务凭据仍属于信任边界，不能声称 read-only 沙箱覆盖这些渠道 |
| `hub_execution.append_event/recovery/reconcile` | 调用记录、未知结果与事后核对 | 工具执行入口应先验证当前权限/预算、持久记录确切调用、消费审批，再仅按账本保存的参数调用适配器；未知结果保持 review_required，禁止换 key 自动再发。需验证各提交/崩溃间隙，不宣称跨外部系统 exactly-once |

仓库没有内置邮件、报价发送、客户联系、支付供应商适配器；当前外部动作由调用方宿主执行。不能通过给动作名称加黑名单、提示词或伪造成功回执来填补这条边界。

后续最小接入顺序：可信账户/租约 Authority → 人工 HTTP 决定入口 → 持有业务凭据的唯一工具执行网关 → 四类动作适配器逐项启用 → 右侧审批 UI。网关未就绪的动作默认不可用；未知动作拒绝，不能透传给通用 shell/HTTP 工具。业务凭据不能同时留在可绕过网关的 Worker 中。真实预算预留和供应商幂等需在适配器落实。

发布前必须补齐：未经批准时四类适配器调用计数为零；金额/收件人/正文修改、过期、拒绝、撤销、角色/来源/租约变化均阻断；只读/Worker token 无法批准；并发只调用一次；消费后崩溃不自动重发；真实账户/API/数据库及手机审批旅程通过。上述集成验收本次尚未执行，PR 应继续保持 Draft。
