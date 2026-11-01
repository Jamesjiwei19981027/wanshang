# Linux 部署基线

本文适用于当前 `axle` 代码分支，核对日期为 2026-10-31。先在隔离环境验证，再按现有部署架构发布；修改配置文件和构建镜像不会自动更新运行中的服务。

仓库的 `docker-compose.yml` 仍只提供 PostgreSQL、Redis 和 Celery worker。Django API、Next.js 前端及 HTTPS 反向代理沿用现有进程托管方案；不要将开发用的 `manage.py runserver` 或 `npm run dev` 用作生产服务。数据库和 Redis 端口保持仅绑定本机，禁止直接公开。

## 前端运行时与构建

生产构建和运行 Next.js 的 Node.js 均使用 **24.22.1 或同一受支持系列的更新安全版本**。2026-10-21 的 [Node.js 安全发布](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases) 修复了 DNS、TLS 和 Brotli 等问题；仅更新 `package-lock.json` 或在 CI 使用新 Node，不会更新服务器已运行进程的 Node。发布目录先运行：

```bash
cd frontend
node --version
npm ci
npm audit --audit-level=moderate --registry=https://registry.npmjs.org
npm run typecheck
npm run lint
npm run build
```

构建前配置真实 HTTPS 公开地址及 `NEXT_PUBLIC_ENABLE_STARTER_FALLBACK=false`，再由进程管理器以 `npm run start` 启动并持久设置 `NODE_ENV=production`。`NEXT_PUBLIC_*` 在构建时写入浏览器资源，改运行环境后需要重新构建；其中不得放入秘密。`npm audit` 必须访问支持审计 API 的 registry；npmmirror 的审计 404 不能算零漏洞。

正式发布前核对进程管理器或容器实际命令与 Node 版本。当前本机 `wanshang-axle-preview` 是预览环境，API 使用 `manage.py runserver`，前端使用 `next dev`；健康检查成功只能证明预览可用，不能用作生产启动验收。独立 Django API 必须使用已安装并纳入版本管理的生产 WSGI/ASGI 服务，且满足下文 ASGI 应用入口及代理限制。

## 先执行隔离验证

在 Linux 的仓库根目录，使用 Python 3.10+、运行中的 Docker Engine 和 Docker Compose 2.24.4+ 插件执行：

```bash
python3 scripts/check_linux_stack.py --output-dir output/linux-check
```

脚本会构建当前 worker 镜像，使用独立 Compose 项目、临时凭据及专用数据卷，执行数据库迁移、PostgreSQL 后端完整测试（测试项数以实际日志为准）以及 Redis、异步通知和附件隔离联调。结果与日志保存在指定输出目录，退出码非零时先检查失败日志。脚本会清理它创建的临时容器和卷，保留新建镜像供检查和扫描；镜像标签记录在 `results.json`。

这是部署前验证命令，不是线上部署命令；它不会将业务数据迁入新库，也不代表生产域名、SMTP、Turnstile、扫描引擎或反向代理已验证。不要将正式数据库地址或凭据注入该隔离测试。

## Python 与原生运行时

默认后端基线已改为固定 digest 的官方 **Python 3.13.18 / Alpine 3.24** 镜像，使用 uv 0.13.5；不再沿用先前 Debian 容器。当前 Dockerfile 将构建阶段与运行阶段分离：编译器、链接器和开发头文件只在 builder 中安装，运行阶段仅复制本项目虚拟环境及所需的发行版原生库。

容器依赖通过 `uv sync --locked --no-dev --extra container` 安装。`container` extra 将 psycopg 3.3.6 的 C 扩展从源码编译并链接系统 `libpq=18.6-r0` 和 `libssl3/libcrypto3=3.5.9-r0`，运行时采用 `PSYCOPG_IMPL=c`；不会安装带私有 libpq/OpenSSL 的 `psycopg-binary`。精简 musl 环境中纯 Python 实现依赖的 ctypes 库发现可能需要额外编译/链接工具，因此不能把旧的 `PSYCOPG_IMPL=python` 配置复制到这个镜像。

Alpine 的此版本 libpq 不带 GSS/Kerberos 或 LDAP 客户端依赖。当前基线面向密码/SCRAM 及按部署要求配置的 TLS 数据库连接，**不提供 GSS/Kerberos 客户端认证或 LDAP 连接服务发现**；原部署依赖这些客户端功能时，先制定并验证替代连接方案，不能直接切换。PostgreSQL 服务端的 LDAP 密码认证使用普通客户端密码协议，不要求客户端安装 libldap。libpq 客户端升级也不等于升级数据库服务器，目标 PostgreSQL 仍需单独维护与验收。

Pillow 13 使用锁定 sdist 在 builder 中编译，仅保留业务需要的原生 **PNG、JPEG、WebP** 能力；安装 `libjpeg-turbo`、`libwebp`、`libwebpdemux`、`libwebpmux` 和系统 zlib。WebP 的 demux/mux 分包也必需，不能仅保留 libwebp。TIFF、JPEG2000、AVIF、LCMS、字体/RAQM、XCB 等可选原生功能被关闭，避免带入对应 wheel 私有库及 C++ 依赖。PDF/CAD 附件仍按业务签名校验并交给独立扫描器，不由 Pillow 解码。

`zlib=1.3.2-r1` 包含发行版的 CVE-2026-85091 修复。Python 基础镜像 digest、builder 的开发包版本与 runtime 对应库版本必须一起维护；不要只替换某一个 so 文件或混用其他发行版软件源。`config/runtime_security.py` 在最终镜像构建阶段检查实际 APK 版本、驱动实现、codec 往返和加载的库，`scripts/check_linux_stack.py` 在隔离联调中再次调用它。更新 Python、Alpine、libpq/OpenSSL/zlib 的 pin 时必须同步审查和更新该断言策略，随后重新构建、扫描并执行隔离验证；不能仅删除断言来使构建通过。当前验证结果以本次日志为准，本文不替代最终测试或扫描结果。

## 独立 API 与主机部署

独立 Django API 建议复用同一套已验证的后端原生基线，再由受版本管理的生产 WSGI/ASGI 服务托管；镜像默认命令仍是 Celery worker，不会自动启动 API 或反向代理。

若必须在主机上单独安装 API，Python 至少为 3.13.18 且保持在 3.13 系列，uv 至少为 0.13.5；使用主机发行版维护的 libpq、OpenSSL、zlib 与编译依赖，并在发布目录重新创建虚拟环境。采用 `--no-dev --extra container` 构建 psycopg C 扩展，并按 Dockerfile 的 `--no-binary-package pillow` 与逐包构建参数重建同范围 Pillow。只安装默认 Pillow wheel，不能获得本容器移除可选原生依赖的结果。构建依赖、ABI、权限和主机系统组件需按该部署方式独立管理与评估，不能套用本 Alpine 镜像的结论。

**不要复制 Windows、glibc、musl 或不同架构之间的 `.venv`/原生扩展。** 更换发行版、架构或 Python ABI 后必须在目标环境重新从锁定来源构建。Windows 的 psycopg binary 开发依赖仅用于本机开发，Linux 生产始终使用 `--no-dev`。更新依赖时提交匹配的 `pyproject.toml` 与 `uv.lock`，不要在生产安装过程中重新解析或随意升级依赖。把 `PSYCOPG_IMPL=c` 持久配置到真实 API 的进程管理器；当前终端临时导出不会更新已运行服务。

在目标运行环境核验实际版本：

```bash
uv run --frozen --no-sync --no-dev python -c 'import sys, ssl, importlib.util; from importlib.metadata import version; from psycopg import pq; print(sys.version); print({n: version(n) for n in ("Django", "djangorestframework", "psycopg", "Pillow")}); print("psycopg implementation:", pq.__impl__); print("libpq:", pq.version()); print("Python SSL:", ssl.OPENSSL_VERSION); print("binary wheel present:", importlib.util.find_spec("psycopg_binary") is not None)'
```

默认容器预期 psycopg implementation 为 `c`、libpq 为 `180006`（18.6）、binary wheel present 为 `False`。其他主机需核验自身发行版的完整安全包版本和功能边界，不要求盲目复用 Alpine 的 APK 版本号。数据库密集接口的延迟、CPU、连接数及容量仍需按真实负载验收。

## 数据库分支必须一致

本次审查发现已有本机服务的数据迁移历史来自 `drone`，而当前代码为 `axle`：已有库出现 `0019_sitesetting_social_links`、后续至 `0023_application_search_metadata`，当前仓库对应的是 `0019_alter_banner_button_link_and_more`。它们不是可以直接混用的同一条迁移链。

部署前先核对目标数据库属于哪个项目、应用版本和迁移分支，并制作可恢复的数据库及媒体备份。在备份副本上确认迁移计划和恢复流程。新的 `axle` 部署应使用对应的新数据库，或先制定并验证明确的数据迁移方案。不要把当前分支直接连接已有 `drone` 数据库运行迁移，也不要用 `--fake` 隐藏分叉。本文和隔离验证脚本均不授权修改现有业务库。

官方 PostgreSQL 镜像的 `POSTGRES_USER` 是初始化管理员，通常拥有超级用户权限。生产 API 和 worker 应使用另建的业务角色，只授予目标数据库和应用 schema 所需权限，不授予 `SUPERUSER`、`CREATEROLE`、`CREATEDB` 或其他数据库访问权限。迁移需要的 DDL 权限应由发布流程管理。宿主机 API 可配置业务角色的 `DATABASE_URL`，容器 worker 用对应、正确 URL 编码的 `CELERY_DATABASE_URL`；不要把初始化管理员凭据作为默认生产业务凭据。角色及权限变更先在数据库副本验证，再由已授权的数据库管理流程实施。

## 文件目录与扫描引擎

worker 使用 UID/GID `10001:10001`。Linux 绑定挂载会保留宿主机权限；Dockerfile 中的目录所有权不会覆盖宿主机已有目录。发布前确认 worker 和独立 API 进程都能读取、写入并遍历它们共享的媒体目录。

不要对已有媒体树无条件执行递归 `chown` 或设置 `777`。先确认真实 API 用户、目录归属及备份状态，再为 API 与 worker 分配适当共享组或 ACL，并确保新建文件的权限也允许另一方访问。新目录可以在确定用户和组后单独初始化；已有文件的权限调整应在备份副本中验证。

- `backend/media` 是允许公开的 CMS 媒体目录，只对外提供经过业务校验的公开内容。
- `backend/private_media` 必须持久保存并保持私有，不得配置为 Nginx 静态目录、CDN 源站目录或公开对象存储。RFQ 附件只通过授权的 Admin 下载接口访问。
- API 与 worker 必须挂载同一份私有文件存储，否则异步扫描会找不到 API 上传的文件。

`ATTACHMENT_SCANNER_COMMAND` 指向的程序必须实际安装在执行扫描的 worker 镜像中，签名数据库也需要可用并保持更新。只在 Linux 宿主机安装 ClamAV，或只填一个宿主机可执行路径，不会让容器自动获得扫描能力。若使用派生镜像安装扫描程序，应重新构建、扫描并重新执行隔离验证；扫描器若引入 C++ 等库，必须对新的原生依赖和运行断言制定有证据的专用策略，不能继续套用最小镜像的缺组件结论或无条件放宽禁止项。不要将宿主机整个程序目录直接挂入容器。

扫描程序应遵守 ClamAV 退出码：`0` 为干净、`1` 为感染，其余状态为扫描失败。生产模式禁止跳过扫描；没有可用扫描器时，文件保留在隔离区，Admin 下载保持 `423`。部署验收应使用实际扫描程序验证干净文件及安全测试样本，不能把隔离测试中确认“扫描失败时保持私有”当成扫描引擎已经正常工作。

## 容器权限与运行时验真

Compose 中的 worker 使用 `cap_drop: [ALL]` 和 `security_opt: [no-new-privileges:true]`，配合镜像的非 root 用户，限制进程通过 SUID 程序或文件 capabilities 获得额外权限。它们适用于当前 worker 的普通任务，不代表发行版内残留 CVE 已修复，也不要直接复制到需要初始化数据目录和切换用户的数据库容器。

下面命令只应在已核对项目、数据库和部署目录的目标 Linux 环境中执行。它们读取已部署 worker 的实际状态：

```bash
docker compose exec -T celery sh -c 'id; grep -E "^(CapEff|CapBnd|CapPrm|NoNewPrivs):" /proc/self/status'
docker compose exec -T celery uv run --frozen --no-sync python -c 'import sys, rest_framework; from psycopg import pq; print(sys.version); print("DRF:", rest_framework.VERSION); print("psycopg:", pq.__impl__, pq.version())'
docker compose ps
```

预期 UID 为 `10001`，`CapEff`、`CapBnd`、`CapPrm` 为全零，`NoNewPrivs` 为 `1`，psycopg 为 `c`、libpq 为 `180006`。还可运行 `docker compose exec -T celery uv run --frozen --no-sync python config/runtime_security.py` 复核最终容器实际的原生包和 codec 策略。这些输出必须来自重建镜像并重新创建容器后的服务，单看仓库文件无法证明它们已生效。对独立托管的 API 也应核验其实际 Python、DRF、psycopg 和系统库版本。

## 发布验收与风险边界

API 的所有请求（含 Admin 表单批量上传）现在限制请求体总量为 38 MiB；
Nginx 可在对应站点或 API location 配置 `client_max_body_size 38m;`，
其他代理也应设置相同或更小的硬上限。较大的 Admin 上传应分批提交。
RFQ 解析还会按实际文件字节限制单个 10 MiB、合计 25 MiB、最多五个，
所有文件字段都计数，超限返回 HTTP 413 并清理临时文件。
ASGI 部署必须使用 `config.asgi:application`；其外层保护在 Django 缓冲
请求体之前按实际接收字节执行 38 MiB 上限，不依赖 Content-Length。
ASGI 的单文件检查在后续 multipart 解析时执行，因此 38 MiB 内的请求体
仍可能先被缓冲。部署后重启 API，核验超限请求及正常上传；仅重建
Celery worker 不会更新独立运行的 API。

沿用 [README 的生产检查清单](../README.md#production-checklist) 配置公开 HTTPS 域名、可信代理数量、Redis 共享限流、SMTP、Turnstile 及私有文件存储。API 和 worker 的数据库、Redis、公共媒体地址与扫描配置必须一致；宿主机 API 的 `localhost` 地址不能直接当作容器地址使用。明确配置 `DJANGO_ENV=production`、`DJANGO_DEBUG=False` 和独立强密钥，不要直接使用示例值。

生产 SMTP 必须启用经过证书验证的加密传输，`EMAIL_USE_TLS`（STARTTLS，通常端口 587）和 `EMAIL_USE_SSL`（隐式 TLS，通常端口 465）恰好一项为 `True`，并按邮件服务商要求设置端口。生产设置拒绝两项同时关闭或同时开启，避免 SMTP 凭据与邮件明文传输；不要通过关闭证书验证绕过连接错误。API 与异步发送邮件的 worker 都需要相同的有效设置。

公开接口各 IP/邮箱配额采用 Redis 原子滑动窗口。自定义 Redis ACL 除常规缓存命令及后台计数所需的 `EVAL`、`EXISTS`、`INCRBY` 外，还必须允许 `TIME`、`ZREMRANGEBYSCORE`、`ZCARD`、`ZRANGE`、`ZADD`、`PEXPIRE` 访问应用命名空间；Redis 不可用或脚本被拒绝时接口返回 503，不会绕过配额继续写库。发布后先验证正常提交、超额 429 与缓存故障 503。新窗口使用独立版本键，发布时额度重新起算，旧键自然到期；网关仍需限制连接数、请求速率、超时与请求体。

Turnstile 通过真实 Siteverify、表单 action 和正式 hostname 校验后，应用还会在共享缓存中原子登记该令牌的 HMAC 指纹，保留 300 秒。即使上游再次返回成功，同一凭证也只能被一个请求接受；缓存故障时拒绝提交。缓存不保存原始令牌。浏览器在失败后重置验证码，下一次提交需使用新令牌。2026-10-25 的本机实测已验证真实验证码成功、服务端校验和应用侧重复令牌拒绝；该结果不能代替正式域名部署后的验收。

发布流程应包含备份验证、在副本上的迁移演练、前端类型检查/lint/生产构建，以及目标环境中的健康检查、表单提交、异步通知和附件访问检查。SMTP 与 Turnstile 的真实外部集成需要在已配置的目标环境验收。发生故障时按已验证的版本和数据恢复方案回退，数据库迁移不能简单等同于回退镜像。

历史 Debian 镜像的扫描记录只代表对应旧制品，不能当作新 Alpine 镜像的扫描结果。当前是否仍有残余告警，以真正发布镜像的扫描、原生包核验和分诊证据为准；非 root、禁用权限提升和运行断言也不能替代漏洞更新。持续跟踪官方 Python 镜像、Alpine 3.24 安全更新及锁定依赖，升级后重新构建、扫描并运行隔离回归；不要为使扫描数字归零而删除必需库、混装发行版或忽略未经核验的条目。

## 完整生产栈与运维脚本

2026-10-25 起，`deploy/compose.production.yml` 提供一套独立生产方案。根目录的开发 Compose 和既有预览保持各自的数据与配置。生产方案使用 Nginx、Next.js standalone 正式服务、Uvicorn ASGI、Celery、PostgreSQL、Redis 和独立 ClamAV；仅 Nginx 对宿主机开放 HTTP/HTTPS。API 使用 `config.asgi:application`，保留请求体总量限制。所有构建只使用公开的前端参数，SMTP 密码和 Turnstile 私密密钥不进入前端镜像。

前端使用固定 digest 的官方 Node 24.22.1 / Alpine 3.24 构建和运行，最终镜像移除运行服务不需要的 npm、Corepack 和 Yarn；原生 Sharp 的 musl 版本已单独验证。Nginx 使用固定官方基础镜像构建本地发布镜像，移除没有被 Nginx 链接或使用的 curl。`BACKEND_IMAGE`、`FRONTEND_IMAGE` 和 `NGINX_IMAGE` 应为每次发布使用独立标签；基础镜像更新后仍须重新构建、扫描并验收，旧扫描结果不适用于新镜像。

应用媒体、静态文件和前端缓存卷显式禁用 Docker 的镜像目录复制，由 `volume-init` 只初始化卷根目录。私有附件目录为 `10001:10001 / 0700`，重复挂载空卷也不会从镜像复制回较宽权限；不会递归修改已有文件。

这套固定镜像基线要求 **Linux x86_64/amd64、Docker Engine、Compose 2.24.4+、Python 3.10+、Bash 4+ 和 flock**。当前固定的官方 ClamAV 镜像没有 arm64 manifest，预检会拒绝 ARM 主机，避免在正式环境静默使用模拟执行。已选的 8 核、32 GB 方案为扫描器预留 4 GiB 上限；最终仍需核对实际主机架构、可用磁盘和峰值资源。ClamAV 的资源要求见[官方容器文档](https://docs.clamav.net/manual/Installing/Docker.html)。

### 一次性准备配置和证书

首次运行下列命令会生成新的 Django、数据库初始化角色、业务角色和 Redis 密钥。只有显式指定 `--import-existing` 才会沿用本地已配置的邮件/Turnstile 信息，不会复制开发库密码或示例 Django 密钥。文件已存在时拒绝覆盖；更新版本时应保留密钥并调整发布镜像标签，而非再次初始化。

```bash
python3 deploy/configure.py init --import-existing
chmod 600 deploy/.env.production
python3 deploy/configure.py check --skip-tls
```

`deploy/env.production.example` 是无秘密的参数清单；真正的完整配置是被 Git 忽略的 `deploy/.env.production`。根目录 `.env` 用于开发，根目录 `.env.production` 只是此前的邮件/验证码覆盖层，不能替代完整部署文件。若只通过 Git 传输源码，需要另行安全传输私有部署配置，或在服务器重新生成并填入服务凭据。配置和 TLS 私钥应独立妥善保存。

在 `TLS_CERT_DIR` 指向的目录放置正式域名的 `fullchain.pem` 和 `privkey.pem`。可以使用受信任 CA 的证书或供 Cloudflare Full (strict) 使用的 Origin CA 证书；证书必须覆盖 `axle.wan-shang.com`，且私钥与证书匹配。私钥不得对其他用户可读或被组成员写入。Nginx 主进程仅保留读取私钥和降权所需能力，证书目录只读挂载，不会递归修改宿主机权限。完整预检会验证文件、密钥配对、域名和有效期；`--skip-tls` 仅供准备阶段，不会由启动脚本使用。

```bash
python3 deploy/configure.py check
```

上述检查只读取配置、证书和 Docker/Compose 信息，不启动任何服务，也不打印秘密。TLS 和 DNS 的真实外部验收仍需在服务器上执行。HSTS 只针对车桥域名及其子域；配置中的 preload 声明不会向浏览器的预加载列表自动提交域名。

使用 Let's Encrypt HTTP-01 时，`ACME_WEBROOT` 指向宿主机验证目录；Nginx 会将其中的 `/.well-known/acme-challenge/` 文件通过 HTTP 提供，其余 HTTP 请求仍跳转到 HTTPS。首次签发可使用临时入口，签发后移除临时容器，再启动正式入口。Certbot 的状态目录应单独保存 `letsencrypt/`、`letsencrypt-work/`、`letsencrypt-logs/`，并在 `certbot-image` 文件中记录实际拉取的官方 `certbot/certbot@sha256:...` 引用。

`deploy/bin/renew_tls.py --env-file /absolute/path/production.env --state-dir /absolute/path/certbot-state` 使用当前用户身份运行 Certbot 容器，验证续签证书后复制为生产所需的普通文件，再执行 `nginx -t` 和平滑重载。脚本与发布操作共用项目锁；失败时保留重载标记供下次重试。先加 `--dry-run` 验证续期流程，再通过宿主机用户的 cron 定期执行。Certbot 状态、证书私钥和生产配置均不得纳入源码归档。

### 启动、更新和管理

以下命令应在确定的正式 Linux 主机上运行，而不是现有业务库的任意目录。脚本固定使用仓库内 `deploy/compose.production.yml`，项目名读取配置文件的 `DEPLOY_PROJECT_NAME`（生成配置默认为 `axle-production`）。可显式传入 `--env-file` 和 `--project axle-NAME`，但常规启动、更新和备份的项目名必须与该配置一致；隔离恢复会使用独立的新项目。不要复用原开发/预览项目名或数据卷。

```bash
bash deploy/bin/start.sh
bash deploy/bin/status.sh

# 后续版本：先在配置中设置本次发布的独立镜像标签。
bash deploy/bin/update.sh

# 停止应用，保留数据库和所有持久卷。
bash deploy/bin/stop.sh
```

启动/更新会先构建 API、前端和 Nginx 镜像，再进入维护窗口、完成备份，并在更新基础服务前核对业务角色登录和迁移链；通过后才更新基础服务、初始化卷权限、执行迁移、收集静态文件和生产系统检查。未知迁移或历史分叉会阻断操作，不会执行 `--fake` 或播种演示数据。基础服务更新或迁移开始后的发布失败会保持应用停机，避免旧应用使用已变化的基础服务配置，或新旧代码与数据库状态混用；此前的失败会尝试恢复原先运行的应用服务。必须根据备份、发布记录和实际迁移情况制定恢复方案。回退前端/后端镜像不等于回退数据库。

PostgreSQL 使用独立初始化管理员和非超级用户的业务角色。新集群初始化脚本仅授予业务数据库连接及 schema 建表权限，不授予创建数据库、创建角色、复制或绕过行级安全权限。初始化密码更改不会自动修改既有数据库角色，应通过受控数据库操作进行轮换。管理员账户也不会被脚本自动创建或设置默认密码；需要时通过 ops profile 的 `manage createsuperuser` 交互创建。

### 附件扫描、更新与故障恢复

Celery 的 `ATTACHMENT_SCANNER_COMMAND` 指向 `python /app/config/clamav_scan.py`。客户端通过私有网络把单个附件字节流发送到独立 ClamD，不共享私有文件目录，也不向公网发布 3310。它保留 10 MiB 文件上限、全程期限、严格回复解析和 ClamAV 的 0/1/2 退出码语义；断连、超时、坏回复、文件变化等都不能产生 clean 状态。业务仍先验证扩展名和内容签名。

ClamAV 以非 root 身份运行，根文件系统只读，只有病毒库卷及受限临时/日志目录可写。FreshClam 保留官方签名校验和自动更新；主机须能解析并访问官方更新服务。每次扫描及就绪探针都会检查病毒库版本和 UTC 时间，默认超过 7 天的库会拒绝扫描；不会因更新失败永久沿用旧库并继续放行。病毒库/引擎版本应在发布与日常运维中监控。

Compose 显式设置 `FRESHCLAM_CHECKS=12`，保持每天检查 12 次；官方入口脚本的命令行参数会覆盖配置文件中的 `Checks`，因此不能只修改 `freshclam.conf`。扫描器健康探针检查守护进程，worker 健康探针和每次文件扫描进一步检查病毒库时效。

故障期间文件保持私有隔离，下载接口返回 423。恢复扫描服务后，使用生产 ops profile 的 `manage scan_attachments` 重新扫描隔离文件。此操作仅在实际引擎判定干净后允许授权下载，不会把 infected/error 文件公开。不要打开开发环境“允许未扫描附件”的选项。

Docker 的 `restart: unless-stopped` 不会仅因健康状态变为 unhealthy 而重启服务。守护进程故障后，先排查日志、资源和病毒库更新，必要时显式重启 ClamAV；确认服务恢复健康后再重扫待处理/失败附件：

```bash
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml restart clamav
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml ps
# 等待扫描服务健康后执行；不启动 API 或发送邮件。
docker compose --env-file deploy/.env.production -f deploy/compose.production.yml --profile ops run --rm -T --no-deps manage scan_attachments
```

### 备份和隔离恢复

```bash
bash deploy/bin/backup.sh

# 也可以把备份放在已准备好的独立安全目录。
bash deploy/bin/backup.sh --backup-root /srv/axle-backups

# 恢复到脚本新建的独立项目，不覆盖正在使用的环境。
bash deploy/bin/restore.sh /srv/axle-backups/backup-TIMESTAMP
```

备份会先记录并停止正在运行的入口、前端、API 和 worker，等待在途任务结束，再制作 PostgreSQL custom-format dump 及公共/私有媒体归档。备份以临时目录写入，完整生成 manifest 和 SHA256 校验后才原子提交为完成目录。失败保留未完成目录并尝试恢复此前运行的服务；不同部署/备份操作通过项目锁互斥。备份目录为 0700、文件为 0600，日志不回显密码。

恢复只接受已验证的完整备份和一个完全新、空的项目，拒绝覆盖已有数据库/卷；媒体归档拒绝路径越界、符号链接、硬链接及其他非普通条目。恢复后不自动启动 API、Celery 或入口，也不回放邮件，防止旧通知误发。先核对记录、文件及迁移链，再计划切换。脚本可供系统 cron 调用，但本次不会自动创建任何定时任务。应另行安排异机备份副本，并定期在隔离项目中验证恢复；Redis 和病毒库不代替数据库/附件备份。

### Cloudflare 可信代理和发布前验收

Nginx 仅对[官方 Cloudflare IP 段](https://developers.cloudflare.com/fundamentals/concepts/cloudflare-ip-addresses/)信任 `CF-Connecting-IP`，对其他来源忽略该头；向 Django 始终覆盖为一个规范化的客户端地址，后端固定为一个可信代理。Next 服务端取数通过仅 Docker 内网开放、只允许 GET/HEAD 的 Nginx 入口转发，生产 `ALLOWED_HOSTS` 仍只包含正式公开域名。

```bash
python3 deploy/configure.py refresh-proxies
```

可信网段只从官方 HTTPS API 刷新并校验后原子替换；网络错误不会覆盖旧文件。刷新后应重新创建 Nginx 容器以重新绑定文件，不要只在仍绑定旧 inode 的容器内 reload。若不经 Cloudflare 访问，可把 `CLOUDFLARE_REALIP_CONFIG` 指向 `./nginx/direct-realip.conf`。没有替用户修改 Cloudflare 账号、DNS、HSTS 或证书设置。

正式发布仍需核对真实内容、公司资料和政策文案，以及目标主机的 DNS/HTTPS、Turnstile 真挑战、SMTP 实际投递、私有媒体权限、真实扫描器及扫描失败恢复。生产镜像构建成功和本地隔离验证不等于整个公网环境已经上线。

### 当前服务器目录

正式上线版本为 `v1.0.0`，对应仓库 `axle` 分支上的同名 Git 标签。服务器正式发布目录为 `/home/wanshang/apps/wanshang/releases/v1.0.0`，由该标签检出；本地、GitHub 和服务器使用同一个提交。源码文本统一使用 LF，逐文件 SHA256 核对结果及运行镜像 ID 记录在独立发布凭据中。运行配置保存在 `shared/production.env`；`APP_VERSION=1.0.0` 和 `GIT_SHA` 分别写入 API、前端、Nginx 镜像的 OCI 版本与提交标签，业务密码和证书仍独立保存。

2026-10-31 的部署使用 SSH 配置别名 `wanshang`，对应 `wanshang@194.163.182.225`；公开域名为 `axle.wan-shang.com`。项目位于 `/home/wanshang/apps/wanshang`，`current` 指向当前发布目录，`releases/` 保存源码版本，`shared/production.env` 保存模式为 0600 的生产配置，`shared/tls/` 保存入口证书，`shared/backups/` 保存一致性备份。不要把此处路径与本机预览容器混用。

```bash
ssh wanshang
cd /home/wanshang/apps/wanshang/current
bash deploy/bin/status.sh --env-file /home/wanshang/apps/wanshang/shared/production.env
bash deploy/bin/backup.sh --env-file /home/wanshang/apps/wanshang/shared/production.env --backup-root /home/wanshang/apps/wanshang/shared/backups
python3 deploy/bin/renew_tls.py --env-file /home/wanshang/apps/wanshang/shared/production.env --state-dir /home/wanshang/apps/wanshang/shared --dry-run
```

首次初始化使用 `manage configure_wanshang` 写入已提供的公司资料和六类产品目录。演示产品、询盘、附件及访客记录不作为正式数据迁入；真实商品和媒体应通过生产后台维护。
