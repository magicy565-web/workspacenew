# Teamily Studio：公开客户端行为观察

记录日期：2026-11-03。范围是成果编辑、评论、交给 Agent 修改，以及版本与来源关联。本文用于指导独立实现，不包含原站源码移植，也不把本项目测试通过当作原站行为已复现。

## 结论与证据边界

观察时最关键的差异是：Teamily 已加载的客户端代码把 **Comments 作为带定位、回复与来源的修改意图**，能够将选中的讨论组织成发给 Agent 的修改任务。本项目在本次观察开始时的“通过审核／要求修改”只记录版本决策，不能替代这个闭环。

**上述差异保留的是观察时的历史状态。** 后续版本已实现成果评论、选中讨论后交给 Agent 修改、冻结原文与评论快照、同一成果的新版本及冲突候选处理。当前契约见 [v0.7 成果协作](workspace-contract-v0.7.md)，实际浏览器和模型执行证据见 [验收记录](workspace-experience.md)。这些实现与验收不改变下文对原站访客资源的证据边界。

本次读取的是访客浏览器实际收到的资源，不是仅从文件名猜测功能。捕获时间为 `2026-11-02T17:50:54.283Z`，清单明确 `authenticated: false`，共 624 条资源记录。三个旅程分别是公开 Agent 目录、公开团队目录、`/chat`；最后一个实际进入 `/login?from=%2Fchat`。捕获记录中没有 Studio 评论、请求、事件或 presence 接口的真实调用响应。

因此，下文采用三个等级：

- **观察到**：清单中的访客导航、资源下载、HTTP 状态及资源内容。
- **静态确认**：已下载模块内确实存在的数据读写、参数组织与调用顺序；不代表浏览器执行过该分支，也不证明服务端按相同语义实现。
- **待验证／实现建议**：需要登录后真实操作或我们自行定义的行为，均明确标注。

没有执行下载的 bundle、调用私有接口或绕过登录。下列 12 份主要证据均核验了实际字节数与完整 SHA-256，和清单一致。清单本身的 SHA-256 为 `a7119c0cdf9f0bed8a8448a31220087696dc9903682fa138c3c52d45318d39aa`。本次仅新增本文档。

## 客户端输入与状态契约

以下是消费者代码实际读取的字段或调用参数，**不是通过服务端响应验证过的完整 API schema**。哪些字段由服务端强制必填、各字段限制以及鉴权规则仍未知。

| 对象 | 静态确认的输入／状态 | 意义与证据 |
| --- | --- | --- |
| 成果身份与预览版本 | `artifactRef`、`artifactType` 与 `previewArtifactRef` 分开；身份解析器可归一化版本后缀，预览引用保留目标版本和文件 | 评论归属的成果身份与本次修改基线不是同一个概念。幻灯片还可指定 `previewSlideFilename`。E02、E06、E07 |
| 评论条目 | 消费 `comment_id`、`block_id`、`body`、`reply_to_id`、`status`、`created_at`、作者字段 | 回复可追溯根评论；未解决的根评论可单独筛选。选中一个讨论条目时，提交帮助函数展开该根讨论的全部回复并去重，回复按创建时间排列。E02、E04 |
| 手工评论来源 | `source: manual`，可带附件、引用文本、地标文本、页面路由、选择器、段落序号与发送时间 | 不是只有一段全局反馈文字；可定位到某页、某段或 DOM 位置。E03 |
| 编辑来源 | `source: edit`，可带 `anchorText`、`editAnchors`、`docxChanges`、`suggestion`、`granular`、`revisionId` | 将块属性变化或文本前后差异表达为可发送的编辑意图。存在 Markdown 差异定位与摘要帮助函数。尚未观察用户点击编辑后如何创建、保存这些评论。E02、E03 |
| 标注来源 | `source: markup`，携带截图 URL，可带 `region`、`canvas` | 标注支持区域／画布坐标；代码可把画笔路径转换为区域定位，包含幻灯片定位。截图转换为消息附件和请求附件。是否成功跨端上传、回显未经实测。E03、E09 |
| 批量修改输入 | 评论数组、选中 ID、`commentMode`、补充说明及附件、当前预览引用、会话和来源 Agent | 提交函数展开讨论，形成可读显示摘要和供 Agent 消费的内容两种表示。其含义是指定修改范围及保留其他内容；不能据此证明模型必然遵守。E02 |
| 来源链 | `threadId`、`projectId`、`agentRunId`、`sourceAgentId`、`sourceAgentNickname`、`conversationId`；可补 `workspaceId`、`workspaceType` | 打开 Studio 时携带原消息来源；发送消息的 `studioRequest` 带对应来源字段。只有前三项完整时，相关发送帮助函数才标记为 Studio 编辑来源。E02、E03、E05、E08 |
| 发出后的本地关联 | `requestId`、`sentAt`、`promptVersion`、`fromVersion`、`artifactType`、`agentId` | 保存在 `sessionStorage`，同时按成果身份和 request ID 查找；其中 `fromVersion` 允许缺省。这是浏览器关联数据，不能当作服务端持久任务账本。E02、E09、E10 |

评论的来源元数据在这里被编码进 body 的结构化前缀，再与可见正文分开解析。这个实现细节说明“普通评论／编辑／标注”可共享评论通路；我们的实现可以使用独立 JSON 列，无需复制它的编码格式或提示词。

## 实际可读到的提交顺序

`web-studio-host` 将 `submitCommentsToAgent` 指向提交模块，说明不是只存在一个孤立的文件名。下面是该函数的静态调用顺序，不是本次访客浏览器完成过的一次真实修改。E11、E02、E05。

1. 根据选中 ID 展开讨论与回复，收集截图和附件，生成修改内容及面向人的摘要。没有匹配评论时抛错；因此空选并不会静默生成请求。
2. 解析预览版本及文件定位。网页引用可补具体入口文件；幻灯片引用可定位到某一页。针对不同成果类型使用不同范围提示，网页应用还强调继续原工作区。
3. 解析当前会话的接收方，并选择修改 Agent。来源 Agent 能用于当前会话时优先使用；在群聊中会核对成员 ID。来源无效或不可达时可回退至默认 Agent。**这里的可达是会话路由判断，不是 Worker 在线、能力匹配或执行成功判断。**
4. 发送带结构化 `studioRequest` 的自定义 IM 消息。它包含展开后的评论 ID、成果引用、预览引用以及可用的 thread／project／run 来源；另携带用于聊天显示的讨论摘要与附件。消息有本地插入和发送后更新路径。
5. IM 发送成功后，再调用 Studio 创建请求。该处实际传入成果引用、类型、`mode: comments`、生成内容、附件；返回值要求成功并包含数据，随后使用 `request_id`。
6. 保存本地 pending 关联并安排超时检查。失败会区分发生在消息阶段还是持久化阶段；当前代码可见“消息已发出，但请求持久化失败”的可能窗口。**没有证据表明服务端一定补偿或去重，也不能仅凭这个窗口断言线上已有重复执行缺陷。**

两个容易误读的点：

- API 包装器确有 `/studio/comments/send-to-agent`，但本次读到的 `submitCommentsToAgent` 主路径走的是“IM 发送 → 创建 request”。不能把包装器的存在当作它就是主提交接口。E01、E02。
- 创建请求帮助函数可接收 `parent_external_ref`，但上述 comments 调用点没有传入它。不能据字段存在宣称已确认服务端版本父链。已确认的是消息里的来源、预览引用以及本地 `fromVersion`。E02。

## 评论、请求与版本不是一个状态

### 评论状态与操作

E01 暴露相对于共享 API base 的评论列表／详情／创建／修改／删除，以及解决和重开操作；列表按成果引用、类型组织，可传账户及额外过滤参数。E04 的消费者将 `resolved` 从未解决根评论列表中排除。

这确认了“讨论是否解决”这一独立维度。它不等同于“版本是否获准”，也不等同于“Agent 是否执行完成”。没有拿到服务端评论 schema，不能补全其他 status 枚举或宣称所有调用都已可用。

### 修改请求与超时

E01 暴露请求列表／详情／创建／取消，以及增量事件读取（含 `since`、`limit`）。E09、E10 的本地 pending 有 30 分钟超时检查、按 request 去重的终态记录，以及重新打开时恢复检查的帮助函数。

当前能具体确认的超时行为是：记录本地 `timed_out` 结果、发出遥测、删除本地 pending。这里没有取消服务端任务的调用。不要将“客户端超时”表述为“Agent 已被取消”。此外，模块内有完成、失败、版本更新的遥测常量，但本次捕获资源中未找到其实际结果消费调用。**常量不证明完成回流已实测或完整可知。**

### 版本与来源

E07 的归一化器区分成果身份、版本和具体文件。E08 从打开成果的原消息提取线程、项目、Agent run 及 Agent 身份；E02 将这些字段继续送入消息，并记录提交时的预览版本。E12 则保存当前成果、会话、模式、选择范围等 drawer 上下文，关闭时可恢复聊天滚动锚点，会话切换时关闭 drawer。

可以据此抽象出“原消息 → 成果身份／所选版本 → 有定位的讨论 → 修改请求”的客户端来源链。下列更强结论目前都没有依据：产出新版本一定回链到某个 request；旧版本标注如何映射到新版本；Agent 完成后是否自动解决评论；是否真正多人同步编辑；审批是否限制发布。

## 和当前实现的关键差异

本项目对比依据：[workspace.js](../studio/static/workspace.js) 的 `openReview`、`saveReview`、`loadArtifact`、`saveArtifact`，以及 [hub.py](../studio/hub.py) 的 `review_artifact`、`edit_artifact`、`complete_task`。这只是当前工程事实，不是对原站内部架构的判断。

| 行为 | 当前项目 | 静态证据支持的目标差异 |
| --- | --- | --- |
| 用户写下反馈 | 每个版本追加 `approved` 或 `changes_requested`，可附一段说明；显示该版本最近审核结果 | 需要独立的评论／回复／定位／解决状态。把审核按钮改名 Comments 不会补齐行为 |
| 从反馈发起修改 | `saveReview` 仅 POST review 并刷新成果，不入队；用户需另发聊天消息 | 选中讨论后可直接交给 Agent；包含所选版本、精确修改目标、回复和附件 |
| 编辑内容 | 全文编辑保存为 human 新版本，`base_version` 冲突时保留草稿 | 还需要“记录修改意图并交给 Agent”的路径；它与人工直接保存应并存，不能混为一键保存 |
| 选择执行者 | 新聊天按用户指定角色或 coordinator 分配 | 成果返工应优先保留原产出任务的 Agent 来源，并明确可用性及回退选择 |
| 版本可追溯 | 版本保留 `task_id`、作者；Agent 回传可使用同一 artifact ID 和 `base_version`；手工编辑保留原任务关联 | 缺少显式 modification request、评论快照、来源版本及结果版本关联；仅有 task_id 不足以解释“这一版回应了哪些反馈” |
| 运行状态 | 已有服务端任务队列、lease、心跳、取消、重试 | 可以复用这些真实状态支撑返工闭环；不能把审核成功、发送成功或 UI pending 当成已修改完成 |

我们已有固定版本下载、分享、冲突草稿保护和安全预览，不需要为补 Comments 重新改写整个 Studio 布局。

## 下一步可独立实现的最小闭环

以下是适合本工程的设计建议，**不是 Teamily 后端协议的还原**。先完成语义，再根据真实操作验证交互细节。

1. **保留“审核”，新增“评论”。** 评论是讨论实体，审核是版本决策。增加评论表，至少包含成果 ID、基线版本、作者、正文、回复关联、定位 JSON、状态、创建／更新时间及并发版本号。服务端验证同空间与相同成果的回复关系。首批支持全文和引用文本定位，再扩展块、路由与截图区域。旧版本评论始终可回到原文，无法可靠重新定位时显示失效定位，不静默指到错误位置。
2. **冻结返工输入。** 新增修改请求，保存选中评论及回复的不可变快照、基线版本、补充说明、来源任务／Agent、请求者和幂等键。创建请求与任务入队在同一服务端事务完成。任务领取上下文显式带此快照及所选版本内容，不让后续轮询、评论编辑或最新成果替换执行基线。
3. **复用现有 Worker。** 有原 Agent 时作为初始选择；角色停用或没有支持该角色的在线执行端时明确显示等待及可改派入口。改派要记录实际执行者。租约领取后才显示执行中。服务端维护 queued／running／completed／failed／cancelled 等既有真实状态，不依赖浏览器 sessionStorage 推断完成。
4. **提交真实结果并连回原成果。** 回传携带修改请求 ID、所基于版本、同一成果 ID 和内容；与现有 `base_version` 校验结合。若用户或另一 Agent 已写入新版本，进入明确冲突，不覆盖；保留该次结果供重做或人工比较。新版本记录此次 request／task、作者和来源版本，点击可查看对应反馈。
5. **验收后再解决评论。** UI 显示“返工已生成新版本”与改动摘要，保留旧版反馈。评论解决与审核通过由用户明确操作；不因任务 completed 自动批准版本。只读成员不能提交评论或请求，API／MCP／CLI 也执行同样权限校验。

建议验收用一条真实旅程：v1 选中文字并评论 → 回复补充 → 选中讨论交给原 Agent → 页面刷新仍可查到请求 → 实际 Worker 领取 → 回传 v2 → v2 能追溯 v1 与评论快照 → 用户解决该讨论。另覆盖发送重试不重复入队、运行中修改评论不改变快照、并发产出冲突、取消后晚到结果拒绝、跨空间访问拒绝。自有 fixture 可验证这些机制；只有接真实模型并检查修改范围后，才能声称模型修改链路已验证。

## 登录后仍需观察的行为

- Studio 内的编辑、评论、标注按钮实际触发哪些保存动作；未发送草稿如何保留；评论和任务状态如何展示。
- 评论创建、请求、事件及版本列表的真实响应 schema、错误码、分页、鉴权与并发冲突规则。
- 修改成功后，哪个事件将 request 与新版本关联；是否自动选中新版；如何保持正在查看的旧版及未保存草稿。
- 旧版标注、线程回复和“已解决”状态在新版本中如何呈现；失败、取消、重试是否改变评论状态。
- 来源 Agent 已离开会话、权限变化或 IM 成功而 request 失败时的实际补偿行为。
- presence ticket 对应的真实在线成员、协同编辑一致性机制。仅有 ticket API 和类型支持声明不足以证明这些能力。

## 可复核证据索引

清单位于 `work/research/teamily/public-runtime/manifest.json`；以下缓存文件相对于同一目录。所有条目均为捕获时 HTTP 200 的 script，URL 为实际清单值。字段名和操作名用于定位语义，未复制原站实现代码。

| ID | 实际模块 URL | 缓存文件／字节数 | SHA-256 |
| --- | --- | --- | --- |
| E01 | [studio-Cjd0_XSz.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/studio-Cjd0_XSz.js) | `6d8bd47598e4eac0fbf0.js`／1983 | `6d8bd47598e4eac0fbf0e1b50b480133aaf335f2726f0901072eb441be5b6cd9` |
| E02 | [submit-studio-agent-action-CxfzkdG6.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/submit-studio-agent-action-CxfzkdG6.js) | `562a311fc4c1048832dc.js`／20135 | `562a311fc4c1048832dc7d3b36006b0bb250b57b1e435b57ec2aeacdbd543e80` |
| E03 | [studio-edit-provenance-CbJj32UC.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/studio-edit-provenance-CbJj32UC.js) | `78bb5f3135fccc64eb0a.js`／3639 | `78bb5f3135fccc64eb0a79e4373782f6e02282c429c8c928a317502babd9a7ac` |
| E04 | [docx-formatting-BUV_Kmsa.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/docx-formatting-BUV_Kmsa.js) | `7486d582c4186aac6999.js`／16663 | `7486d582c4186aac69998fabbddce6eab98809be95859612222bde143e8fa997` |
| E05 | [upload-studio-file-with-fallback-DAFjLToC.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/upload-studio-file-with-fallback-DAFjLToC.js) | `d93b265c2b344ed5b27f.js`／6310 | `d93b265c2b344ed5b27fe1491ebdb808287af619f126890a31588aba347c72d0` |
| E06 | [resolve-studio-agent-preview-ref-DCGJfMgk.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/resolve-studio-agent-preview-ref-DCGJfMgk.js) | `7e49cc5e4e817f6f260a.js`／2229 | `7e49cc5e4e817f6f260a481521a70c6825920301ed4456fd64b27e2ec2d6ca61` |
| E07 | [artifact-identity-QfVedm3v.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/artifact-identity-QfVedm3v.js) | `07e7c04be18bf6199bb2.js`／2817 | `07e7c04be18bf6199bb21addb94daf28fe3410798bcedee2dbc271c1174c1eb9` |
| E08 | [studio-open-provenance-D9c-UTah.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/studio-open-provenance-D9c-UTah.js) | `d82a00202099e80346f5.js`／2173 | `d82a00202099e80346f59d81912205d11afb842834f0982336476d35d2ba02d6` |
| E09 | [upload-markup-screenshot-CcihaSZL.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/upload-markup-screenshot-CcihaSZL.js) | `fa51af5d3d653852787e.js`／12281 | `fa51af5d3d653852787e3cddccd95389997d4bc04fa3aceb3af9d7584d505e87` |
| E10 | [studio-send-pending-watch-yrmIJ4_f.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/studio-send-pending-watch-yrmIJ4_f.js) | `08d8a60d7a07c418b411.js`／1305 | `08d8a60d7a07c418b411025c3370c4f9c671edd0fb1af0b392354c569f90e9b0` |
| E11 | [web-studio-host-BveEc3-1.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/web-studio-host-BveEc3-1.js) | `ede9d835b4e53fe382ef.js`／2488 | `ede9d835b4e53fe382ef8739d962a79e3ae4cec218db0ecf38f49e52e27ba319` |
| E12 | [studio-drawer-context-D1mpbFHl.js](https://teamily-ai.becdn.net/assets/release-20261031-100315-325/studio-drawer-context-D1mpbFHl.js) | `4b6e6c201383764e3318.js`／3040 | `4b6e6c201383764e331844ea43fb04cd877d7ef2e2d9dea94ff53eaabf4d0dfd` |

辅助读取了评论作者标签、mentions、write-access 开关、版本后缀解析和 Studio API base 模块。客户端 write-access 开关不是服务端授权证据；模块存在、类型分支存在和埋点名称存在，都没有被当作端到端功能实测。
