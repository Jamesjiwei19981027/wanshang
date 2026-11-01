# 上线前追加安全复核与修复

> 原生运行时已在后续修复中更新；最新镜像扫描与回归结果见 [原生安全修复报告](security-remediation-2026-10-24.md)。本文保留本轮修复与旧镜像的历史证据。

日期：2026-10-24，Asia/Shanghai。基线提交：`edca7a5`，审查对象为当前工作区，包括进入本轮前已有的未提交修改。

**结论：本轮发现的五项代码/配置问题已修复，并补充两项边界加固。当前本机仍是开发预览配置，不能直接作为正式上线环境。** 发布前必须核验实际进程版本、生产配置、真实验证码与邮件、附件扫描及备份恢复；容器原生组件的剩余告警也需要继续处置。

## 覆盖范围

分别复核了 Django/DRF 公开接口与管理员权限、登录与接口限流、文件上传/扫描/授权下载、输入和产品发布过滤、邮件队列、Next.js 渲染/代理/图片边界、第三方分析与同意状态、生产配置、锁文件、CI 和容器。原有修复得到保留，先前问题和历史证据见 [首次安全审查](security-review-2026-10-24.md)。本轮证据独立保存于 `output/security-recheck-2026-10-24/`，没有把历史结果当成本轮执行结果。

验证使用内存 SQLite、不含 `.env` 的前端副本及独立 PostgreSQL/Redis 容器。没有向真实客户发送测试邮件，没有迁移或清空既有业务数据库，也没有重启现有预览/旧 worker。临时容器和卷均按专用项目清理，最终镜像保留用于检查。

## 本轮追加修复

| 问题 | 已证实的触发条件与影响 | 修复及验证 |
|---|---|---|
| 公开接口并发限流绕过（中） | 原 DRF history 的读取、检查、写回不原子，额度为 2 时，16 个同时请求全部放行；可增加垃圾询价、通知和文件处理负担。 | 新增 `catalog/rate_limits.py`。生产在 Redis 内使用服务端时钟及一个 Lua 脚本完成滑动窗口预占；本机内存缓存对完整操作加锁。保留 IP/邮箱/附件等独立配额。真实 Redis 的 24 并发仅放行 2 个，覆盖五种表单桶、逐项到期和拒绝请求不延长 TTL。缓存故障返回 503，验证码、写库和通知均不执行。 |
| 文档筛选的控制字符导致 PostgreSQL 500（低） | `GET /api/documents/?product=model%00unsafe` 的 NUL 会进入 PostgreSQL 文本参数并报 `DataError`，SQLite 不会暴露同一差异。 | 文档筛选拒绝不可打印字符，在数据库查询前返回 400。原始及 v1 的 documents/downloads 四条路径覆盖 NUL、Tab、换行、DEL；同时复核并测试产品列表的 14 种筛选字段，恶意值不产生 SQL 查询。 |
| 生产 SMTP 可配置为明文（条件风险） | `DJANGO_ENV=production` 且两项邮件加密开关都为 False，旧设置仍允许启动；远程 SMTP 可能明文传输认证信息与询价内容。 | 生产必须恰好启用 `EMAIL_USE_TLS`（STARTTLS）或 `EMAIL_USE_SSL`（隐式 TLS）之一。错误配置启动失败；测试同时验证两种加密模式以及开发明文模式仍能启动。没有发起真实 SMTP 连接。 |
| 搜索词进入统计日志（隐私） | 全站搜索组件把完整自由文本写入事件 `metadata.query`，可能记录访客输入的邮箱或误贴的 token，绕过已有 URL 查询串清洗。 | 改为仅记录 `query_length`、结果数和入口。通过真实组件与事件发送链回归，确认敏感文本不进入请求；界面搜索功能保持原样。 |
| 撤回同意后的异步页面统计（隐私） | 创建统计会话的请求在途时撤回同意，原 Promise 回调仍会发起后续页面浏览请求。 | 在回调内再次检查最新同意状态。回归手动控制首次请求完成时机，确认撤回后仅保留已在途请求，不再发送第二个请求，归因标识已清除。 |

另补充两项加固：

- 图片优化器设置 `images.maximumRedirects: 0`，防止已批准图片源通过重定向访问白名单外的源；这是条件防护，未声称现有媒体源存在可利用的开放重定向。媒体/CDN 地址必须直接返回图片内容。
- 服务端 API 数据层标记 `server-only`，在客户端组件误引用时由构建阻止。内部 API 地址不会因未来误用该模块而被带入客户端。

## 最终验证

| 项目 | 本轮结果 | 证据（均位于本轮 output 目录） |
|---|---|---|
| 后端 SQLite 全套 | 263 项，256 通过、7 项真实 Redis 测试跳过 | `backend-final-tests.log` |
| Linux + PostgreSQL + Redis 全套 | **263/263 通过，无跳过**，包含真实 Redis 并发/过期测试 | `linux-final/postgres-tests.log`、`linux-final/results.json` |
| 数据库迁移及系统检查 | 临时数据库迁移成功；Django 检查通过，模型没有新增待生成迁移 | `linux-final/database-migrations.log` |
| 端到端后端联调 | 联系与询价返回 201；异步销售通知/客户回执成功；无扫描器时文件继续私有隔离 | `linux-final/linux-smoke.log` |
| 前端干净安装与构建 | `npm ci`、Next 类型生成、typecheck、lint、完整生产 build 通过 | `frontend-*.log`、`frontend-verification.json` |
| 前端安全回归 | **10/10 通过** | `frontend-security-tests.log` |
| 构建产物与错误配置 | 安全头、生产禁用 unsafe-eval、本地图片 IP 禁用、图片重定向禁用、开发代理缺失均符合预期；11 组错误配置被拒绝 | `frontend-artifact-checks.log` |
| 依赖审计 | 公开 npm registry 全量 audit、pip-audit 均未发现已知漏洞；`uv lock --check` 通过 | `frontend-audit.json`、`python-audit.json`、`python-lock.log` |
| Python 业务源静态扫描 | 最终 33 个业务源文件 Bandit 无报告，已有扫描器固定 argv/HTTPS 调用抑制经过人工复核；扫描后源哈希未变 | `bandit-final.json`、`bandit-final-source-integrity.json` |
| 合成生产配置 | Django `check --deploy --fail-level WARNING` 通过，使用临时 secret 和示例 HTTPS 域名 | `production-config-check.log` |
| 常见凭据模式检查 | 当前受跟踪及非忽略的新文件共 216 个无匹配；`.env` 被忽略、未受跟踪，可见 Git 历史也未记录该路径 | `secret-scan.json`；有限模式检查不证明所有形式或历史均未泄露 |

最终测试镜像为 `wanshang-linux-check:f9a0093705ad`，镜像 ID 为 `sha256:f5ec317ba5a51c86a1975f9c2a83f87ac1e5e7152559bef85de8f9a1c9b9bdc9`。镜像内 73 个应用源/依赖配置文件的 SHA-256 与最终本机文件一致，见 `final-image-source-integrity.json`。运行态确认 Python 3.13.17、DRF 3.18.3、PostgreSQL 16.15、Redis 7.4.11；psycopg 使用系统 libpq 17.11、无 binary wheel；worker UID=10001、capabilities 为零、NoNewPrivs=1。

**本轮生产前端 HTTP 冒烟未执行。** 工具自动审批先后拒绝后台启动和受工具托管的临时 `next start`，仅返回 `rejected: blocked by policy`。没有再尝试绕过策略，也没有启动相应服务。安全头及图片选项已检查实际构建产物，但这不能替代真实 HTTP 请求验收；正常页面、`.env` 404 和图片接口应在部署环境补验。证据为 `frontend-runtime-policy-rejection.log`。

## 必须完成的上线事项

1. **使用生产配置和生产进程。** 本机配置仍为 development、DEBUG=True、开发示例 secret，未配置 Turnstile 和扫描器，并允许开发环境未扫描文件。应由部署环境提供强密钥、真实 HTTPS 主机、媒体域名、正确 CORS/可信代理、SMTP 和验证码配置。`wanshang-axle-preview` 的 API 使用 `runserver`、前端使用 `next dev`，它们属于预览，不应作为生产启动方式。
2. **确认实际 API、worker 和前端均使用发布版本。** 本轮只读发现 `trailer-parts-celery-1` 仍为 Python 3.13.11、Django 5.2.16、DRF 3.17.1、Pillow 12.3.0、psycopg 3.3.4；这不是本轮验证镜像。先确认该服务所属项目与数据库，再按正确发布方案重建/重建容器，不能把源码锁文件的新版本当成已运行的版本。
3. **完成真实验证码、邮件与附件验收。** 在正式域名验证正确/过期/错误域名 token、两类通知及 TLS 邮件投递；worker 内必须有可用且特征库更新的扫描程序，API 与 worker 共享私有持久存储。验证干净文件、感染测试样本、扫描故障及恢复后的重扫；无扫描器时保持隔离只证明故障关闭有效。
4. **部署原子限流所需的 Redis 权限。** 除常规缓存命令及后台计数用的 `EVAL`、`EXISTS`、`INCRBY` 外，还需 `TIME`、`ZREMRANGEBYSCORE`、`ZCARD`、`ZRANGE`、`ZADD`、`PEXPIRE` 访问应用命名空间。脚本拒绝或缓存不可用时接口返回 503。新 `:atomic-window:v1` 键在发布时重新起算配额，旧键自然到期。网关仍要控制连接数、速率、超时及 38 MiB 请求体。
5. **核对数据库分支并验证备份恢复。** 先前记录的 drone/axle 迁移历史分叉需要在目标库核对；不要直接套用当前迁移或以 `--fake` 隐藏差异。生产应用使用单独的最小权限数据库角色，避免沿用 Compose 初始化超级用户。数据库、媒体备份及恢复演练在正式发布前完成。
6. **核实业务和隐私内容。** 替换示例公司资料、联系方式、产品规格/认证与隐私页待填项；复核实际 Google 标签容器。第一方统计清洗不约束任意后续添加的 GTM 自定义脚本。

具体命令、目录和运行验真方式见 [Linux 部署基线](linux-deployment.md)。本轮没有代替目标部署环境执行上述发布动作。

## 持续风险与依据

### 最终镜像的原生组件告警

> 后续已对下述 15 个 High 独立条目逐项评估：3 个受影响组件缺失、1 个当前平台实现不受影响、10 个条件风险、1 个 zlib 条目保留调查。可利用性判断与发布条件见 [剩余风险评估](security-risk-assessment-2026-10-24.md)。下列原始扫描计数与退出码予以保留，没有通过忽略规则改为零告警。

Grype 0.121.0 对最终测试镜像进行了新的实际扫描，`--fail-on high` **退出码为 2，此项未通过**。扫描数据库构建于 2026-10-23 06:30:15 UTC，实际检查没有可更新候选；工具压缩包 SHA-256 与官方 checksums 一致。

原始扫描仍为 **240 个匹配、110 个独立漏洞 ID**：2 Critical、84 High、65 Medium、16 Low、73 Negligible 匹配。结合新下载的 Debian Security Tracker、镜像内实际包版本、`dpkg` 版本比较和可选组件安装状态：

- 28 个匹配属于发行版已回补或不受影响，其中包含两个按 OpenSSL 上游裸版本匹配的 Critical。
- 3 个匹配所涉及的 sudo 或 SASL DIGEST-MD5 插件在该镜像未安装。
- **保留 209 个匹配、80 个独立 ID 继续审查，其中 15 High、26 Medium、5 Low、34 Negligible。** 未证明这些条目全部存在当前项目的可利用路径，也没有排除所有间接路径；不能称为已全部修复。

在单独临时容器更新官方 apt 索引，核对 97 个已安装包的候选版本，仅 `tzdata` 从 `2026c-0+deb13u1` 有 `2026e-0+deb13u1` 候选；没有找到能直接消除上述保留告警的当前安全包升级。未为清除扫描数字而删除必需系统库、混用发行版或修改正在运行的服务。上线前应处置或明确评估这些残余风险，并持续应用官方后续安全更新。

原始结果与逐项分诊保存在 `container-vulnerabilities.json`、`container-triage.json`、`container-summary.json`；包安装状态和候选版本见 `container-runtime-probe.json`、`container-native-version-comparisons.json`、`container-apt-candidates.json`。官方状态应按 [Debian Security Tracker](https://security-tracker.debian.org/tracker/) 的发行版包版本判断，不能只比较上游版本字符串。

另核验扫描归档与最终构建的身份关系：Docker inspect 的 `f5ec317…` 为 OCI index，平台 manifest 指向的 config `255b746…` 与 Grype imageID 一致；16 个压缩层及解压后 diff ID 全部匹配，九项身份检查通过。见 `archive-image-provenance.json`，确认扫描没有误用旧镜像归档。`container-cleanup-verification.json` 确认没有最终镜像对应的临时容器残留。

### 其他保留边界

生产 CSP 仍允许脚本 `unsafe-inline`，用于当前静态 Next 页面及同意脚本；禁止内联事件处理器并转义 JSON-LD 已提供部分保护。此次没有发现可复现的新增 XSS、SQL 注入或后台越权路径，但检查结果不构成不存在漏洞的证明。后续引入富文本或新标签时，应重新审查渲染边界并评估 nonce 方案。

应用依赖审计为零不能据此表述为整个镜像零漏洞。完整机器可读验证汇总为 `output/security-recheck-2026-10-24/verification-summary.json`。

技术依据：[DRF 对并发限流的说明](https://www.django-rest-framework.org/api-guide/throttling/)、[Redis 原子限流](https://redis.io/docs/latest/develop/use-cases/rate-limiter/)、[PostgreSQL 文本类型的字符限制](https://www.postgresql.org/docs/17/datatype-character.html)、[Django SMTP 加密设置](https://docs.djangoproject.com/en/5.2/ref/settings/#email-use-tls)、[Django 部署检查](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)。Next 图片重定向行为以项目安装版本附带的 `node_modules/next/dist/docs/01-app/03-api-reference/02-components/image.md` 指南及构建产物为依据。
