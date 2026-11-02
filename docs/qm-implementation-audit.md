# QM 源码对照审计

审计日期：2026-11-03（Asia/Shanghai）。上游固定版本：[`yc-software/qm@b4f4b300e830bf279274c5cd96caedf84a441678`](https://github.com/yc-software/qm/tree/b4f4b300e830bf279274c5cd96caedf84a441678)。本地研究目录为工作区下 `work/research/qm`，审计结束时 `git status --short` 为空。

**版本说明：以下逐项对照保留 v0.7 审计时的实现状态。** 随后的 v0.8 已独立实现 [持久执行记录](workspace-contract-v0.8.md) 和 [调用方执行器协议](executor-protocol.md)：工具回执、租约隔离、未知调用核对、最终结果恢复及可选原生 Codex 会话续接。因此下文“只接受最终 JSON / 每任务 ephemeral”的描述只适用于旧接口模式。真实 Codex 会话续接已经完成故障注入验证，详情见 [验收记录](workspace-experience.md)。这仍不是 QM PostgreSQL/Pi 内核、持久子会话树、mailbox 或完整工具 harness 的移植，也不保证外部副作用 exactly-once。

**现有产品是参考 QM 部分思想独立编写的 Python 工作区，还不是 QM 核心的移植、分支或兼容实现。** `studio` 的执行链是 SQLite 任务队列 → 外部 Worker → 调用方 Agent → 最终 JSON 回传；没有导入 QM 的 TypeScript 模块，也没有启动 QM 的 PostgreSQL / Pi 运行时。`requirements.txt` 和 `requirements-mcp.txt` 分别声明 PDF、MCP 依赖，没有 QM。命名中出现 session、worker、Agent，不能证明运行机制相同。

当前任务领取、租约、依赖检查、成果版本和账户隔离有实际持久化实现，所以不能把整个项目说成静态界面演示。但这些事实也不足以支持“已经逆向复刻 Teamily / QM 完整底层”或“生产成熟度等同上游”的表述。Teamily 的私有后端没有源码证据，本报告只核对可审查的 QM 固定版本。

本文是针对三个核心机制的实现审计，不是全仓安全认证。当前工作区仍在开发，下列 Python 行号对应本次检查时的文件；QM 链接全部固定到同一个提交。

## 1. Durable sessions、运行队列与租约

| 项目 | QM 源码事实 | 当前实现与差异 |
| --- | --- | --- |
| 运行领取 | [`postgres-run-store.ts:206`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/runs/postgres-run-store.ts#L206) 的 `claimable` 避免同 session 的运行互相越过；`:214` 的领取 SQL 用 `FOR UPDATE SKIP LOCKED`，按 priority、created_at、seq 排序，并生成 lease token。`:254` 的 enqueue 通过数据库唯一键去重。 | [`hub.py:183`](../studio/hub.py) 用 SQLite `BEGIN IMMEDIATE` 串行化写事务；`:627` 按 room 的最早未结束 run 和 DAG 已完成依赖领取任务。它解决单 SQLite 工作空间的独占领取，不是 PostgreSQL 多进程队列算法的移植。没有 QM 的人类 / 委派 / 后台优先级、抢占计数和同等入队去重语义。 |
| 两类租约 | [`postgres-session-store.ts:602`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/postgres-session-store.ts#L602) 在 `session_leases` 领取独立的写租约；`:410` 的 `withLease` 在事务中校验 token 与到期时间，再允许写入。它与 run 队列 lease 分开；holder 包括 turn、compaction、fork、backfill，见 [`session-store.ts:50`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-store.ts#L50)。 | [`hub.py:672`](../studio/hub.py) 校验任务 worker、token、取消状态及到期时间，`:779` 原子保存最终结果和成果。当前没有独立的会话写租约，也没有以该租约保护模型回合、压缩和分支的协议。任务 lease 相似，并不等于 session tape 语义相同。 |
| 持久日志与恢复 | [`postgres-session-store.ts:421`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/postgres-session-store.ts#L421) 分配序号并写 `items`；`:488` 对重复 call ID 抛出 `DuplicateCallError`；`:668` 的 appendMany 必须持有 session lease。当前固定版本的权威记录是 `items`，不应把旧名 `session_tape` 当现存表名。[`orchestrator.ts:3199`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/core/orchestrator.ts#L3199) 把 Agent 的逐步 emit 写入这条日志。 | [`hub.py:110`](../studio/hub.py) 的 messages 保存用户文字和任务最终答复，`:125` 的 events 保存任务状态变化。它们不含完整的工具调用、工具结果与可恢复模型回合。[`worker.py:113`](../studio/worker.py) 启动子进程并只接受最终 JSON；[`codex_executor.py:70`](../studio/codex_executor.py) 明确使用 `codex exec --ephemeral`。服务重启可重新领取任务，不能据此称为从中断的工具步骤继续。 |
| 重试边界 | [`run-store.ts:174`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/runs/run-store.ts#L174) 一带区分 counted claims、error attempts、preemptions；[`turn-resume.ts:108`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/core/turn-resume.ts#L108) 根据已记录调用 / 结果选择 restart、continue、retry 或 settle，只有明确 `retrySafe` 且带重跑参数的单个悬空调用才进入 retry。 | [`hub.py:845`](../studio/hub.py) 重试失败或未开始的任务、保留已完成任务；过期 lease 可重新领取。它保护服务内的任务 / 成果提交，不保证外部工具副作用恰好发生一次，也没有 QM 的逐调用恢复决策。 |

可合理复用的是 **状态机不变量和恢复决策**：公平顺序、去重键、区分抢占 / 崩溃 / 业务失败的计数、写日志时的 token 隔离，以及 `resumeStrategy` 对未知外部副作用的保守处理。小型纯函数适合带着上游测试移植；PostgreSQL SQL 不能直接复制到 SQLite，必须重新证明事务边界和并发语义。

本项目已有的相关行为测试可查看 [`test_hub.py:60`](../tests/test_hub.py)（并发独占）、`:68`（过期与旧写者）、`:121`（版本冲突回滚）、`:166`（取消）、`:193`（同 room 顺序）。这些是本项目的正确性测试，不是与 QM 的行为等价测试；本次审计没有把它们包装成 QM 集成验证。

## 2. Subagent delegation 与作用域

QM 的子 Agent 首先是一个可以继续对话的 **持久子会话**，不是某个角色名称对应的一次性队列任务。

| 源码位置 | QM 实际机制 | 当前实现与差异 |
| --- | --- | --- |
| [`agent-tools/sessions.ts:5`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/agent-tools/sessions.ts#L5)；[`session-syscalls.ts:635`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-syscalls.ts#L635) | `subagents` 工具暴露 open、read、send_message、wait。open 在 session-tree-admission 锁内检查当前调用方和 audience，验证 runtime，用 parent session + run + requestId 构造稳定 ID，用内容 fingerprint 拒绝同键不同工作，限制整棵树同时运行数，保存 parentSessionId / spawnMeta / participants，再入队。 | [`hub.py:506`](../studio/hub.py) 的自动入口先分给 coordinator；`:700` 仅允许 coordinator 提交最多 12 项 DAG，且禁止再次指派 coordinator。子项仍是同 room 的任务，没有持久 child session、父子会话树、定向内部消息或向同一个子 Agent 追问的生命周期。它也没有语义分类模型；“自动”表示由外部 coordinator 决策或使用已保存模板。 |
| [`session-syscalls.ts:735`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-syscalls.ts#L735)；[`:1050`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-syscalls.ts#L1050)；[`:1189`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-syscalls.ts#L1189) | 消息可只留 mailbox 或唤醒接收者，也可中断运行。完成回传重新核对父子 scope / 发起上下文，以 `subagent-mail-{run.id}` 写信，以 `subagent-return:{run.id}` 去重唤醒父会话。内部消息不是新的人类授权。 | 完成任务只向 room 插入最终答复，并让依赖满足的 DAG 任务可领取。当前没有父 Agent mailbox、消息确认、动态 follow-up / wake、父子会话中断传播协议。这些不能由任务列表 UI 替代。 |
| [`resolution-service.ts:15`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/resolution/resolution-service.ts#L15)；[`context-filter.ts:4`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/resolution/context-filter.ts#L4)；[`session-syscalls.ts:42`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-syscalls.ts#L42) | DM 映射 personal scope，频道 / 群组映射相应 scope；挂载和跨 scope 的 handles 由 resolution + ACL 决定。委派授权还检查 actor、audience、原始运行和父子关系，不能凭子 Agent 消息扩大权限。 | [`accounts.py:184`](../studio/accounts.py)、`:225`、`:258` 处理用户 / token、workspace 角色与独立数据目录；[`hub.py:627`](../studio/hub.py) 限制注册 worker 可领取的 agent_ids。当前是 workspace 维度隔离，不是 QM 的个人 / 频道 / 团队 scope、资料挂载、会话参与期限和跨 scope grants。角色提示词也不是安全边界。 |

[`feature-flags.ts:11`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/feature-flags.ts#L11) 将 persistent_subagents / responsive_spine 标为 personalOnly。上述源码存在，不意味着所有 QM 部署或所有 scope 默认启用了这些路径；本次没有验证该部署配置。

可合理移植的是 **持久父子身份 + admission 锁 + 稳定 requestId / fingerprint + mailbox 去重回传**。它们直接解决任务完成后继续追问、重启后找回子 Agent 和重复消息的问题。前提是先有可恢复会话记录，并明确我们自己的 workspace / room 权限模型；不能直接把 QM 的 personal scope 名字贴到现有 workspace 上。

## 3. 上下文组装与工具分发

| 源码位置 | QM 实际机制 | 当前实现与差异 |
| --- | --- | --- |
| [`orchestrator.ts:757`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/core/orchestrator.ts#L757)；[`:2810`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/core/orchestrator.ts#L2810) | 组装参与者可见历史、memory recall、恢复计划、环境和资料；有参与者变更时，会根据可见历史交集重算。 | [`hub.py:567`](../studio/hub.py) 打包 room 文字、角色 instructions、依赖结果、最近消息、资料、成果和绑定技能。用 UTF-8 JSON 大小限制 256 KiB，超出时返回 omissions / references。它是调用方上下文包，不是完整模型会话状态，也没有 QM 的 scope memory recall 与参与期限过滤。 |
| [`orchestrator/compaction.ts:55`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/core/orchestrator/compaction.ts#L55)；[`session-store.ts:442`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/sessions/session-store.ts#L442) | 前者按模型 token budget 和文档预留计算历史预算，写入带 throughSeq 的摘要并保留近期内容，摘要失败有明确降级；后者是按 user 回合边界切割的 transcript 视图。两者不是同一功能。 | 当前只有字节限额与明确省略，没有模型 token 预算、压缩摘要记录或完整工具回合边界。UI 里看到历史消息不等于模型能恢复此前工具上下文。 |
| [`agent-tools.ts:142`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/agent-tools.ts#L142)；[`agent-tools/kit.ts:76`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/agent-tools/kit.ts#L76)；[`agent-tools/record.ts:62`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/agent-tools/record.ts#L62) | 创建实际工具 catalog；按 control / surface / delegate / readOnly 筛选，做 action schema 校验、sandbox / approval / retry 包装；调用前后记录 callId、tool_call、tool_result，并携带来源和 scope。 | 我们的 HTTP / MCP 暴露工作区管理动作，外部 Agent 使用自己的宿主工具。[`worker.py:116`](../studio/worker.py) 的 envelope 没有逐调用工具分发协议。真实 Codex 执行可以完成任务，但其工具循环由 Codex 宿主负责，不能宣称服务实现了 QM 的 catalog、审批和重放。 |
| [`agent-run.ts:910`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/agent-run.ts#L910)；[`:1024`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/agent-run.ts#L1024) | 从持久历史重建消息，再以自定义 tools 创建 Pi session；内存中的 Pi SessionManager 不是耐久存储本身。模型运行依赖 provider / gateway 和 QM 的 runtime wiring。 | 我们选择模型无关，由调用方提供模型；当前 Codex adapter 每任务启动一个 ephemeral CLI。这个选择符合调用方自带模型的需求，但与运行 QM 自有 harness 是不同集成方式。 |

可合理复用的是 **记录有来源的调用 / 结果、保持回合边界、用 throughSeq 表达摘要覆盖范围、按权限构造可用工具集合**。纯窗口与预算算法可以单独移植；Pi 模型循环、sandbox、审批和各 connector 不能只搬一个工具列表就获得同等语义。[`runtime-choice.ts:65`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/src/agent/runtime-choice.ts#L65) 主要解析 model / effort / fast mode 的配置和覆盖，也不应称为我们所说的“按业务任务自动选研究员 / 设计师”。

## 实际运行证据

本次使用本机 **Node v24.22.1**，直接执行固定版本的 TypeScript 模块；没有安装系统数据库，没有修改研究 clone，也没有伪造模型输出。执行时间、完整命令、退出码和日志位置保存在工作区 `work/qa/qm-audit-results.json`。

| 检查 | 真实结果 | 能证明与不能证明的内容 |
| --- | --- | --- |
| 原仓库 `node --test test/turn-origin.test.ts` | 退出 0，**8 / 8 通过** | 实际执行了 QM 的 turn-origin 归一化和 desktop 授权来源边界测试；不证明 UI、数据库或模型链路。 |
| `node --test work/qa/qm-audit-modules.test.mjs`（从工作区运行；完整绝对路径见日志） | 退出 0，**8 / 8 通过** | 本次编写的独立探针直接 import 未修改 QM 源码，执行 run priority / counted claims / lease expiry、session lease 等待、participant tenure、按回合窗口、scope entitlement。租约等待使用内存测试替身，**没有执行 PostgreSQL 并发**。 |
| 原仓库 `node --test test/run-store.test.ts` | 退出 1，加载失败，缺 `pg-connection-string`，由 `src/persistence/pg-pool.ts` 引入 | 尚未进入用例断言；不能据此称 QM 队列算法失败，也不能称队列已经跑通。没有配置 PostgreSQL。 |
| 原仓库 `node --test test/turn-resume.test.ts` | 退出 1，加载失败，缺 `@aws-sdk/client-s3`，由 `src/persistence/blob-transfer.ts` 引入 | 说明恢复模块的传递依赖尚未安装；**没有验证完整 resume、Pi harness 或外部工具执行**。 |

复核入口是工作区 `work/qa/qm-audit-runner.py`。它调用的 Node 路径为：

```text
C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

在工作区根目录可重新执行：

```powershell
& .\work\studio-venv\Scripts\python.exe .\work\qa\qm-audit-runner.py
```

四份原始输出为 `work/qa/qm-audit-upstream-turn-origin.log`、`qm-audit-audit-modules.log`、`qm-audit-upstream-run-store.log`、`qm-audit-upstream-turn-resume.log`。这些 scratch 证据不是产品依赖，打包产品时如不附带，不应把文中的本机验证入口说成发布包内自带测试。

## 最值得先修正的一项机制

**先把一次性任务结果协议升级为可恢复的会话事件账本。** 这是与 QM 最关键的差距，也是持久子 Agent、长任务恢复和可信执行过程共同依赖的基础。增加模板、工具名称或界面卡片无法补上它。

具体应在现有 model-agnostic Worker 协议中加入稳定 session / turn / call ID、带 lease token 与序号的 append-only 事件提交、调用 / 结果配对，以及 executor 的 continuation 能力声明。旧 lease 不能追加事件；相同事件重试必须幂等；重新领取时依据已提交记录选择继续、重启或待核对。宿主不提供可恢复 session 时必须如实报告“重新执行”，不能仅重新拼接 room 消息就宣称原地恢复。

优先参考 `postgres-session-store.withLease / appendMany`、`turn-resume.resumeStrategy` 与 `agent-tools/record` 的不变量，适配到现有 SQLite 与外部 Agent 边界。验收至少覆盖：工具已返回但完成回传丢失、工具副作用可能发生但结果未落库、进程重启 / lease 更换、同事件重复提交、撤销后旧执行器迟到写入。对结果未知且不保证幂等的外部调用，不自动重放；“提交幂等”并不意味着“外部副作用恰好一次”。

如果后续目标改为直接使用 QM 原运行时，应单列 Node / PostgreSQL / Pi / provider / sandbox 的集成与运行验收；不能把 Python 包里的几个同名类当成已经完成该集成。本次仅增加本报告及 scratch 验证文件，未修改生产代码。

上游该提交的 [`LICENSE`](https://github.com/yc-software/qm/blob/b4f4b300e830bf279274c5cd96caedf84a441678/LICENSE) 为 MIT，版权声明为 QM contributors。未来实际复制其代码或实质片段时应保留所要求的版权与许可声明，记录来源文件和固定提交；这不改变原始 Buyer Magazine 包、字体或其他依赖各自的授权。
