# 上线前安全审查与修复记录

> 本文保留首次审查记录。随后独立复核又修复了公开接口并发限流、SMTP 明文配置及前端隐私问题；最新代码、测试数量和发布要求见 [追加安全复核](security-recheck-2026-10-24.md)。下文的近似限流边界已被后续原子限流实现替代。

审查日期：2026-10-24（Asia/Shanghai）。基线提交：`edca7a5`；本文对应此基础上的本轮工作区修复。

本次检查覆盖 Django/DRF 公开接口、后台权限与登录、上传和下载、异步扫描与邮件、Next.js 页面与代理、外部链接、分析与同意管理、锁定依赖、容器及 CI。验证使用 SQLite 内存数据库、独立 PostgreSQL/Redis 容器和不含环境文件的前端副本。

**结论：已修复下列代码问题，正式发布仍需完成生产配置及目标环境验收。** 本机 `.env` 仍用于开发预览；其调试、示例内容、验证码和扫描配置不构成已验收的生产环境。

## 已修复的问题

| 项目 | 复现或风险条件 | 修复与验证 |
|---|---|---|
| 后台登录并发限流绕过（中） | 额度为 2 时，8 个同时到达的请求都能在失败计数更新前进入认证。 | 在密码验证前使用共享缓存原子预占；并发回归证明只有 2 个请求进入认证，其余返回 429。成功登录仍清理对应计数。 |
| Redis 过期竞态导致持续锁定（中） | Django 默认客户端分开执行 EXISTS 和 INCR；中间过期时，INCR 会创建没有 TTL 的键。 | 新增 `config.cache.AtomicRedisCache`，通过一个固定 Lua 脚本检查并递增；缺失键返回 ValueError，登录逻辑重新建立有限期额度，已有 TTL 保留。登录计数使用新版本命名空间，避免继承旧的永久锁；增加可控回归和真实 Redis 过期/并发测试。 |
| 非法产品编号造成公开接口 500（中） | `product=²` 被 `isdigit()` 接受后转换失败；100 位数字及部分事件产品引用在 SQLite 中溢出。 | 文档查询只把 ASCII 十进制作为编号，显式限定正 64 位主键范围；事件和页面访问的产品引用也在查询前检查。测试覆盖原始及 v1 接口、Unicode 型号、越界编号和异常 JSON 类型。 |
| 队列故障时附件扫描阻塞请求（中） | Broker 不可用时，HTTP 请求会同步执行最多 5 次、每次最多 60 秒的扫描。 | 排队失败记录 ERROR，文件保持私有隔离，下载继续返回 423；请求不执行扫描器。条件更新保护已经完成的 worker 结果，回归验证恢复命令能处理全部五份附件。 |
| 表单 Origin 校验不完整（加固） | 同主机的 HTTP Origin 可通过 HTTPS 请求校验，带路径或控制字符的伪 Origin 也未严格排除。 | 完整匹配协议、主机和端口；拒绝凭据、路径、查询、片段及控制字符。合法同源请求和配置的前端来源继续通过。 |
| Turnstile 缺少部署域名绑定（条件风险/加固） | 一个 widget key 同时用于其他站点或预览环境时，仅检查 success/action 未限定本部署的 hostname；公开测试 secret 也可能误用。 | 生产 token 的 hostname 必须属于已配置的 CORS 前端或 ALLOWED_HOSTS；生产启动拒绝三种 Cloudflare 公开测试 secret。测试覆盖缺失、类型错误、外部和合法域名。 |
| 邮件及文档链接处理不严（低） | CMS 邮箱形如 `sales@example.com?subject=Injected` 会被旧校验接受；文档链接允许反斜线、控制字符或嵌入认证信息。 | 严格验证邮箱并编码合法 local part 特殊字符；文档链接在 URL 规范化前检查危险字符和凭据。保留合法邮件别名和下载链接。 |
| 查询串与片段进入追踪记录（隐私） | 页面 `?token=...#...` 会原样进入 source_url、landing/referrer，默认同源 Referer 及内置 GA 也可能再次带出原 URL。 | 表单页面 URL 去掉 query/hash，landing 仅保留路径，referrer 仅保留 origin；历史 session 数据读取时清洗，独立 UTM 字段保留。fetch 与响应头使用 strict-origin，内置 GA 显式设置清洗后的 page_location/page_referrer。 |
| 撤回分析同意不完整（隐私） | Google 命令队列使用普通数组；仅删除 www 子域 cookie 会遗漏父域 GA cookie；其他标签页的已加载脚本仍运行。 | 使用 Google 的 Arguments 队列格式，清除归因标识和父域/安全前缀 cookie；跨标签页撤回会重新加载页面停用已运行标签。测试验证未同意不发送第一方统计请求。 |
| CI 开发依赖告警可被忽略（流程） | 原开发依赖审计设置 continue-on-error。 | 全部前端依赖的 moderate 及以上告警阻断 CI，并接入本轮前端安全回归脚本。 |

最终测试日志、首次全套回归发现的超大编号故障和扫描原始结果保留在 `output/security-verification-2026-10-24/`。对风险成立条件作了区分：Turnstile 共享 key 和非法 Origin 的加固，不代表已证明本项目线上存在验证码或账户绕过。

## 验证证据

- 后端最终 SQLite 内存回归：251 项，248 项通过、3 项真实 Redis 测试在该环境跳过；系统检查通过，模型迁移检查无差异。
- 前端独立安全回归：8/8 通过；类型检查、lint 和生产构建通过。前端副本通过 `npm ci` 从锁文件安装，使用公开示例 HTTPS 地址构建。
- 生产 Next.js 进程 HTTP 验证：联系、询价、隐私页面返回 200 且安全响应头生效；`.env`、`package.json` 返回 404；本地 IP 和非许可图片域返回 400。共 7 项通过，临时服务已退出。
- `npm audit` 全部依赖：0 个已知漏洞；`pip-audit`：未发现已知漏洞。Python 环境与 `uv.lock` 一致。
- Bandit 业务源代码扫描：0 项报告，测试和迁移文件排除；少量既有扫描器调用带有人工核验的 nosec 标记。静态扫描结果与依赖审计不能代替业务权限或容器检查。
- 使用完整生产参数的合成配置执行 Django `check --deploy --fail-level WARNING`：通过。该检查使用临时生成的测试 secret 和示例域名，真实 SMTP、域名证书及验证码仍需目标环境验收。
- 当前受跟踪文件的常见私钥、GitHub/AWS/API key 格式扫描无命中；`.env` 未受 Git 跟踪。此检查覆盖当前文件和有限的已知模式，不是凭据泄露历史的完整证明。

### Linux、PostgreSQL 和 Redis 集成

从本轮最终代码重新构建 `wanshang-linux-check:29232f99ad56`，镜像 ID 为 `sha256:7b49cc4b2772f977f66fb561f934596bd610d9ad27663d1404d1abc34aba520d`。独立项目迁移成功，**251 项测试全部通过，无跳过**，其中包括三个真实 Redis 回归；联系与询价均返回 201，异步销售通知和客户确认成功，未配置扫描器时附件继续隔离。缓存原子递增、TTL 保留、过期键不重建为永久键均通过验证。

运行时确认 Python 3.13.17、DRF 3.18.3、PostgreSQL 16.15、Redis 7.4.11；psycopg 使用系统 libpq 17.11，未加载 binary wheel。worker UID=10001，capabilities 为零，NoNewPrivs=1。临时容器和数据卷已清理。证据为 `output/security-verification-2026-10-24/linux-final/results.json`、`postgres-tests.log` 和 `linux-smoke.log`。

### 容器系统包告警：仍需保留

Grype 0.121.0 对最终镜像的扫描完成，`--fail-on high` **退出码为 2**。漏洞数据库构建于 2026-10-23 06:30:15 UTC；本轮更新检查没有更新候选。原始结果为 **240 个匹配、110 个独立 CVE ID**，包含 2 Critical、84 High、65 Medium、16 Low、73 Negligible 匹配。

本轮重新下载 Debian Security Tracker，逐项对比先前分诊的全部 advisory/package/version，结果及发行版状态没有变化，并重新核对组件安装状态：

- 28 项匹配属于 Debian 已回补或不受影响。两个 Critical 均按 OpenSSL 裸版本匹配；镜像实际安装 `3.5.7-1~deb13u3`，官方记录将对应 [CVE-2026-63073](https://security-tracker.debian.org/tracker/CVE-2026-63073) 和 [CVE-2026-75803](https://security-tracker.debian.org/tracker/CVE-2026-75803) 标为已修复。
- 3 项匹配对应实际未安装的 sudo 或 SASL DIGEST-MD5 插件。`dpkg-query` 可能对只有记录而未安装的包返回成功，因此本轮另核对了 Status-Status 与插件文件。
- **仍保留 209 个匹配、80 个独立 CVE ID 跟踪**，其中独立 ID 为 15 High、26 Medium、5 Low、34 Negligible。当前未确认这些条目全部存在应用可利用路径，也未排除所有间接路径。比如 [Kerberos CVE-2026-107778](https://security-tracker.debian.org/tracker/CVE-2026-107778) 需要 GSS acceptor 服务处理恶意凭据，当前项目以数据库客户端使用 libpq；这只是条件分析，不能推广到其他条目。

在同一依赖镜像的临时容器更新官方 apt 索引，核对 OpenSSL、libpq、glibc、zlib、SQLite、ncurses、Perl、C++ runtime、ACL、util-linux、Kerberos、SASL、gzip 等 16 个包，候选版本均等于已安装版本。现有受支持软件源没有可直接安装的这些包更新。升级发行版或自行维护系统库需要单独验证，不能把此次代码修复表述成全部系统漏洞已解决。

原始扫描、官方 tracker、分诊对照、组件核验和 apt 候选证据分别保存在 `container-vulnerabilities.json`、`debian-security-tracker.json`、`container-summary.json`、`container-optional-components.log`、`container-apt-policy.log`，均位于本轮 output 目录。先前逐项条件说明见 `output/security-review-2026-10-23/final-image-scan.md`；本轮复核结果在前述文件中。

## 部署与故障恢复要求

1. 使用 `DJANGO_ENV=production`、`DJANGO_DEBUG=False` 和独立强密钥，配置真实 HTTPS 前端、API 和媒体域名；关闭 starter fallback。当前本地配置保留开发用途，生产密钥和域名应由部署环境提供。
2. 配置真实 Turnstile widget/secret、准确的 action 与允许域名；在实际域名提交两种表单，核验正确 token、过期 token 和错误域名 token。
3. 把扫描程序及更新的病毒特征库安装进执行扫描的 worker 环境，API 与 worker 共享私有持久存储。队列故障恢复后执行 `uv run python manage.py scan_attachments` 或使用后台重扫，买家无需重提询价。失败入队的扫描目前需要这一步恢复操作。
4. 私有媒体保持不可公开访问；后台下载同时要求员工身份、模型权限和 CLEAN 状态。公共媒体与受保护附件使用独立目录。
5. API 仅由受控反向代理访问，代理覆盖转发头，`DJANGO_NUM_PROXIES` 与实际链路一致。Redis 自定义 ACL 需允许固定计数脚本所需的 EVAL、EXISTS 和 INCRBY。公共 DRF 限流仍是近似限流，网关承担连接数、请求速率、超时及 38 MiB 请求体上限控制。
6. 在目标环境验证真实 SMTP、销售通知和客户回执、正常上传与隔离失败、扫描恢复、备份恢复及数据库迁移计划。现有部署文档已指出历史迁移分支差异，发布前核对目标数据库分支与当前应用。
7. 用核实的经营主体、隐私联系、保留期限、处理方等替换隐私/Cookie 页中的待填内容，核实产品资料、认证和联系方式。GTM 自定义标签有读取页面的权限，后续容器标签配置需要持续审查。

## 保留的技术边界

- 生产 CSP 的 script-src 仍包含 unsafe-inline，以兼容当前静态页面/Next.js 脚本方案；script-src-attr 为 none，生产禁用 unsafe-eval，JSON-LD 已转义 `<`。此次审查未找到可利用的 HTML 注入路径。若后续加入富文本 HTML，应同步采用经过验证的清洗与 nonce/CSP 方案。
- 本机与临时容器的成功验证不能证明既有运行服务已升级。将本轮源码发布到 API、worker 和前端后，核对实际运行版本和配置。
- 容器发行版告警需要按包版本、官方回补和可达条件继续管理；依赖 audit 的 0 项结果不能概括为整个系统无漏洞。

## 相关官方依据

- [Cloudflare token 服务端验证与 hostname](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)、[公开测试 key](https://developers.cloudflare.com/turnstile/troubleshooting/testing/)。
- [Google 同意队列格式](https://developers.google.com/tag-platform/security/guides/consent)、[GA page_location/page_referrer 配置](https://developers.google.com/analytics/devguides/collection/ga4/reference/config#page_location)。
- [Node.js 2026-10-21 安全发布](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases)、[Django 5.2.18 安全发布](https://www.djangoproject.com/weblog/2026/oct/06/security-releases/)、[Next.js 2026-10-14 安全更新](https://nextjs.org/blog/nextjs-security-update-october-14-2026)。
