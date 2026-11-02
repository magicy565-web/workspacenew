# 开发状态与接续

本仓库已按用户要求公开保存完整工程。当前优先完成 M1 Workspace 与 M2 Agent Team；平台仍在开发，尚未完成全部业务验收。

- `main`：已验证的 v0.9。源码检查点为 [93b495f](https://github.com/magicy565-web/workspacenew/commit/93b495f418001dc2a4ab0ddc44a64a49cb33abfa)，110 个源文件均已核对 GitHub blob SHA；本文件是随后补充的交接入口。
- [feature/agent-sessions-v0.10](https://github.com/magicy565-web/workspacenew/tree/feature/agent-sessions-v0.10)：完整保存的未发布开发分支，共 117 个文件，检查点 [36ba962](https://github.com/magicy565-web/workspacenew/commit/36ba9622765ba0b653a7c8c4fd484c999cf9e7fe)。Agent 会话核心、宿主适配器、HTTP 读取、界面和测试已合入；实际模型跨任务续接仍待验收。
- [完整开发交接与明日验收顺序](https://github.com/magicy565-web/workspacenew/blob/feature/agent-sessions-v0.10/docs/development-handoff-2026-11-03.md)：先验证真实连续两轮，再完善执行前审批，接着完成买手开发业务闭环。UI 与运行状态一同验收。

v0.9 已完成真实调用方 Codex 协作及文件交付验证。开发分支合并后的 162 项 Python 测试通过；新增会话界面 5 项浏览器检查通过，使用合成 HTTP 数据，没有替代真实模型验证。Docker/HTTPS 部署、完整 Teamily 对等及商业 Worker Network 尚未验收。

运行先选择 `main`，Windows 双击 `start-windows.cmd`，浏览器打开 http://127.0.0.1:8765/，再通过“连接 Agent”接入调用方自己的模型宿主。详细安装和其他系统运行方法见 README。

本机工作数据、凭据、浏览器登录状态及私有备份保留在本机，没有发布到仓库。开发分支测试使用独立的 `STUDIO_DATA_DIR`；不要直接对已有业务数据试验未验收的迁移。
