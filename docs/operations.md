# Agent Workspace 运维与自托管

此交付包含部署配置和经过本机测试的管理、备份、恢复命令。当前开发环境没有 Docker 可执行程序，因此没有进行镜像构建、Compose 启动或真实 HTTPS 验证；这些步骤须在目标主机验收。它不代表安全审计或认证，也没有自动发布到公网或 GitHub。

## 本机与团队部署边界

个人使用可以保持默认 loopback 服务：`python -m studio.server --port 8765`。启用账户后，HTTP 请求按当前成员和工作空间隔离。团队部署使用固定 HTTPS origin、账户与 scoped API token，通过反向代理访问内部服务；不要直接把默认本地服务映射到公网。

stdio MCP、CLI、容器 exec 和能读取数据目录的进程属于操作系统级可信客户端，拥有文件系统权限。团队 Agent 应使用 HTTP/远程 MCP 的 workspace-scoped token；不要分发宿主机目录或容器 exec 权限。外部 Worker 使用自己的模型与授权工具，应用不会凭空执行模型任务。

## 管理命令

在项目根目录运行，先设置 `STUDIO_DATA_DIR` 为需要管理的绝对目录。Windows PowerShell 示例：

```powershell
$env:STUDIO_DATA_DIR = 'D:\AgentWorkspace\data'
python -m studio.admin init --email owner@example.com --name '管理员' --workspace '团队空间'
python -m studio.admin check
python -m studio.admin reset-password --email owner@example.com
```

`init` 和 `reset-password` 都通过 `getpass` 两次交互读取密码，没有密码命令行参数。初始化只允许第一个所有者；`init` 可在设置了公网 origin 的主机上预初始化账户。重置密码会撤销该用户在所有空间的既有登录会话和 API token，并清理该邮箱的登录限流记录。其他用户不受影响。

`check` 和别名 `health` 使用只读连接检查所有 `.sqlite3` 数据库的 `PRAGMA integrity_check` 和表结构可读性。输出相对数据库路径、检查状态和表数量，不输出密码、token、邮箱或文档正文。退出码 0 表示检查通过，1 表示失败。HTTP `/health` 只是进程存活检查，不能替代数据库检查。

## Docker 与 HTTPS

以下命令在 Linux Docker 主机、项目根目录执行。先将域名 DNS 指向主机，确保 80/443 可达，并安装 Docker Engine 与 Compose plugin。

```sh
cp deploy/environment.example deploy/.env
# 编辑 deploy/.env：把 STUDIO_PUBLIC_ORIGIN 改为自己的 https://域名，不能留 example.com。
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml build app
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps app \
  python -m studio.admin init --email owner@example.com --name '管理员' --workspace '团队空间'
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
docker compose --env-file deploy/.env -f deploy/compose.yaml exec app python -m studio.admin check
```

Compose 对缺失的 `STUDIO_PUBLIC_ORIGIN` 使用必填变量校验。App 仅通过容器网络暴露 8000，没有宿主端口映射；Caddy 发布 80/443 并代理到 App。应用启动时要求已初始化所有者。部署账户初始化必须先于 `up`，不会靠公开网页抢占第一个账户。[Docker Compose 变量说明](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)

Caddyfile 使用环境变量中的 HTTPS 地址，并保留传入 Host 供应用校验；证书及配置使用独立持久卷。Caddy 会按其自动 HTTPS 机制申请和续期证书，实际是否成功取决于域名、网络和证书服务。[Caddy 配置说明](https://caddyserver.com/docs/caddyfile/concepts)、[自动 HTTPS](https://caddyserver.com/docs/automatic-https)

App 以 UID/GID 10001 运行，根文件系统只读，持久数据位于 named volume `/data`，临时文件在 `/tmp`。默认安装 `requirements.txt` 中的 PDF 解析依赖；如需在镜像内使用 MCP，再将 `INSTALL_MCP=1` 后重新构建。此配置运行的是完整 `python -m studio.server` 生命周期，包括调度器的启动与关闭。

升级先备份，再重新构建镜像并重启。不要执行 `docker compose down -v`，它会删除持久卷。当前 Python/Caddy 标签跟随相应发行系列；生产变更时记录实际镜像 digest，并在测试实例验证后再更新。

## 一致备份

SQLite backup API 能生成数据库自身的一致副本，但多个数据库与杂志文件不共享一个事务。因此备份前必须停掉服务器、内置调度器和所有会写入数据目录的 MCP、CLI、Worker。`--writers-stopped` 是操作者对此条件的声明，程序不能阻止其他进程继续写文件；本版没有跨进程维护锁。[Python SQLite backup API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)

```powershell
# 先停止所有写入进程。
python -m studio.backup create --data-dir 'D:\AgentWorkspace\data' --output 'D:\Backups\workspace.zip' --writers-stopped
python -m studio.backup verify --archive 'D:\Backups\workspace.zip'
```

Docker 示例（`backups` 目录需对 UID 10001 可写）：

```sh
mkdir -p backups
sudo chown 10001:10001 backups
docker compose --env-file deploy/.env -f deploy/compose.yaml stop app
# 也停止外部直接访问同一数据卷的写入进程。
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  -v "$PWD/backups:/backups" app python -m studio.backup create \
  --data-dir /data --output /backups/workspace.zip --writers-stopped
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d app
```

如果 `STUDIO_DATA_DIR` 已改为 `/data/restored`，备份命令的 `--data-dir` 也使用该路径。输出 ZIP 必须在数据目录之外且不能覆盖既有文件。每个 SQLite 文件经 backup API 导出，已合并 WAL 内容，不把原始 WAL/SHM 当成恢复数据；其余文件保持字节内容和空目录。清单记录相对路径、长度和 SHA256，归档创建后会再次完整校验。

备份使用标准 ZIP 的 stored 模式，优先保持可验证的大小边界。限制为 50,000 个文件/目录、单文件 1 GiB、总内容 5 GiB、清单 16 MiB、ZIP 中央目录 32 MiB；超过限制应分批迁移或调整经过评审的限制。验证拒绝未登记条目、重复/大小写冲突路径、路径穿越、符号链接、特殊文件、加密条目及异常压缩比例。备份含账户数据库和业务内容，应按原数据的权限保存；ZIP 本身不加密，SHA256 校验也不等于来源签名。

## 恢复到新的目录或数据卷

恢复先校验全部 ZIP、CRC、长度和 SHA256，再在目标同级的临时目录展开并检查数据库，最后才放入目标。目标必须是显式指定的空目录或不存在的目录；不会覆盖、合并或删除原来的工作空间。需要目标所在文件系统有足够空间容纳解包副本。验证失败时目标保持不变。

```powershell
python -m studio.backup restore --archive 'D:\Backups\workspace.zip' --destination 'D:\AgentWorkspace\restored'
$env:STUDIO_DATA_DIR = 'D:\AgentWorkspace\restored'
python -m studio.admin check
python -m studio.server --port 8765
```

恢复后账户、API token 和任务状态来自备份时间点。旧实例保持停止，避免两个实例同时接受同一批任务。需要撤销旧凭据时使用交互式 `reset-password`，再给该用户创建新的 token。

Docker 恢复使用新 named volume 和其内部的 `/data/restored` 子目录，保留原卷。先停止现有 App 和写入客户端：

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml stop app
STUDIO_DATA_VOLUME=agent-workspace-restored STUDIO_DATA_DIR=/data/restored \
  docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  -v "$PWD/backups:/backups:ro" app python -m studio.backup restore \
  --archive /backups/workspace.zip --destination /data/restored
```

成功后将 `deploy/.env` 中 `STUDIO_DATA_VOLUME` 改为 `agent-workspace-restored`、`STUDIO_DATA_DIR` 改为 `/data/restored`，再运行 `admin check` 和 `up -d`。不要以 `/data` 这个挂载点本身作为恢复目标；使用上面的新子目录，才能在同一数据卷内完成最终目录切换。

## 授权与验收记录

部署镜像保留原 `engine` 目录及其中的来源、字体和许可说明。本批没有给未知原始技能包或整个工程添加 MIT 许可。模型服务、来源素材和第三方依赖继续适用各自条款。

本机测试覆盖交互式初始化/改密及会话撤销、数据库检查、账户加两个工作空间加杂志文件的备份恢复、WAL 内容保存、哈希损坏拒绝、越界路径/链接/压缩炸弹拒绝以及不覆盖已有目标。容器构建、域名证书、真实远程并发、灾难恢复演练和上线容量仍需要在部署主机执行验收。
