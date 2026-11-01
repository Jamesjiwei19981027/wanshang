# 原生运行时安全修复与验证

日期：2026-10-24（Asia/Shanghai）。本轮接续 [旧镜像的 15 项 High 评估](security-risk-assessment-2026-10-24.md)，实际更换运行时、修复上传解析边界，并对新制品重新验证。

**结果：新后端 Linux amd64 镜像扫描为 Critical 0、High 0，之前评估的 15 个 High 编号全部不再检出。仍有 3 个独立 Medium 编号（5 次包匹配），不能表述为零漏洞。267 项 PostgreSQL/Redis 后端测试全部通过。代码与镜像修复完成，现有业务服务尚未更新。**

## 制品与证据

- 镜像：`wanshang-linux-check:0002ae2e3c0d`。
- Docker OCI index：`sha256:4c30722921724f77a0717c5ea3629d761755737dcaf942d5340772aad5f95170`。
- amd64 平台 manifest：`sha256:0db69e17f831aaf5865fd3241832d9b1c507e4cc6421535f8f5414d9583c1e92`。
- 镜像配置及扫描器 image ID：`sha256:2050af01d4c6cdbe904d4a1eecbbf34105e7f532261c79e661c871ecd041c03f`。它与 OCI index 是不同层级的摘要，已通过导出归档验证对应关系。
- 证据目录：`output/security-remediation-2026-10-24/`。原始扫描、工具校验、数据库状态、镜像对应关系、源码哈希与测试日志均保留。旧报告的数字只对应旧 Debian 制品。

实际构建和执行验证的架构为 amd64；基础镜像的 arm64 manifest 已核验存在，但本轮没有执行 arm64 回归。未重启旧 worker、修改业务数据库或覆盖 `.env`，测试使用独立容器、临时凭据及专用卷，结束后已清理测试容器与卷。

## 实际修复

| 范围 | 修复与验证边界 |
|---|---|
| 基础运行时 | 改为 digest 固定的官方 Python 3.13.17 / Alpine 3.24，实际为 Alpine 3.24.3、musl 1.2.6-r3。使用同一稳定发行版的包，不混入 edge 软件源。 |
| zlib | 安装 `1.3.2-r1`，包含 CVE-2026-85091 的发行版补丁；这项采用真实补丁处理。 |
| PostgreSQL 客户端 | 新增锁定的 `container` extra，在 builder 编译 psycopg C 扩展，链接系统 libpq 18.6-r0 和 OpenSSL 3.5.9-r0；最终镜像没有 psycopg-binary 私有库，也不依赖编译器进行 ctypes 库发现。 |
| 不需要的原生组件 | 本项目安装的编译器和原生库开发包只保留在 builder。最终包清单和实际加载库均无 glibc、GNU C++ 运行库、Kerberos/GSS、Perl、ACL、GNU libmount 等原有组件。对应告警通过移除组件及受影响代码处理，未宣称为这些组件安装了尚未发布的补丁。 |
| 图片库 | 从锁定 Pillow 13 sdist 编译所需 PNG/JPEG/WebP 原生能力；关闭 TIFF/JPEG2000/AVIF、LCMS、字体/RAQM、XCB 等可选原生能力，避免 wheel 引入额外 C++/codec 库。实际执行 PNG/JPEG/WebP 编解码往返及全部插件初始化，库依赖可用。 |
| 上传边界 | `Image.open(..., formats=[expected_format])` 在解析前选择允许的解码器，伪装为 PNG 的其他图片不能先进入 TIFF 等解析器。新增测试证明拒绝时不写询盘/附件、不发送通知，正常格式继续可用且文件指针复位。PDF/CAD 原有校验保留。 |
| 持续检查 | `config/runtime_security.py` 在最终构建阶段及 Linux 联调中核对原生包、驱动、codec 和实际加载库。CI 扫描同一个已验证镜像，High/Critical 阻断，`only-fixed: false`，保留原始 JSON；同时修复原有 `permissions` YAML 写法导致的解析错误。 |

旧 15 项的处理对应关系：

| 旧告警组 | 新制品中的处理 |
|---|---|
| Perl：82560、9538；ACL：54369、54370 | 对应组件未安装。 |
| GCC/libstdc++：95619、102010 | 无 C++ 运行库、相关开发头文件及可选 C++ codec；不以“新版 GCC 已修复”作无证据结论。 |
| glibc：5435、19499 | 运行时改为 musl，不再包含 glibc。 |
| Kerberos：107778 | libpq 不带 GSS/Kerberos 依赖，相关组件移除。 |
| ncurses：CVE-2025-69720 | 使用 6.6_p20260516-r0 的库，原受影响 infocmp 工具不在镜像。 |
| util-linux：76642、78408、78409、78410 | 无对应 GNU mount/nsenter/libmount 实现。基础镜像仍有 BusyBox applet 和来源为 util-linux 的 libuuid，不能声称同名命令或整个源包完全消失；它们不等同于上述受影响实现。 |
| zlib：85091 | `1.3.2-r1` 已含对应补丁。 |

表中除完整列出的 2025 编号外，其余均为 `CVE-2026-` 前缀。全部 15 个完整编号及复扫交集保存在 `scan/scan-summary.json`，交集为空。移除 C++ 依赖的结论针对本次最小镜像；未来引入扫描器或其他原生插件后必须重新评估。

## 验证结果

| 检查 | 本轮结果 | 证据路径（相对于本轮目录） |
|---|---|---|
| PostgreSQL 16.15 / Redis 7.4.11 全量后端回归 | 267 项，全部通过，无跳过 | `linux/postgres-tests.log` |
| 隔离联调 | 迁移、Redis 原子计数、worker 通信、表单入库、异步通知、私有附件共享及扫描器缺失时保持隔离均通过；测试资源清理成功 | `linux/results.json`、`linux/linux-smoke.log` |
| 权限 | UID 10001，NoNewPrivs=1，CapEff/CapBnd/CapPrm 均为 0 | `linux/linux-smoke.log` |
| SQLite 回归 | 267 项，260 通过，7 项真实 Redis 测试跳过；对应测试在上面的真实 Redis 环境全部执行 | `backend-tests.log` |
| 镜像扫描 | Grype 0.121.0，Critical 0 / High 0 / Medium 5 次匹配，3 个独立编号，其他级别 0；`--fail-on high` 退出码 0 | `scan/container-vulnerabilities.json`、`scan/scan-summary.json` |
| 扫描数据库 | 2026-10-23T06:30:15Z 构建，校验有效；本轮更新检查报告无更新 | `scan/grype-db-status.json`、`scan/grype-db-update.log` |
| Python 依赖审计 | 从最终镜像读取实际安装的 25 个版本，包括 psycopg-c，再执行 pip-audit，已知漏洞 0 | `scan/installed-python-requirements.txt`、`scan/python-audit.json` |
| 源码静态扫描与锁文件 | Bandit 无报告，既有固定 argv / HTTPS 调用的抑制延续人工复核；`uv lock --check` 通过 | `bandit.json`、`python-lock.log` |
| 制品一致性 | 镜像内 80 个应用/配置/模板/锁定文件与当前源码哈希一致；扫描归档与测试镜像的平台、配置和 diff IDs 对应 | `image-source-integrity.json`、`scan/image-provenance.json` |
| CI 配置 | YAML 可解析，权限为 contents:read，高危拦截与包含无修复条目的配置通过检查 | `ci-validation.json` |

原始扫描没有项目排除规则或本轮 VEX/忽略条目，`ignoredMatches=0`，没有以仅扫描已有补丁条目的方式降低数量。扫描器自带的内核头文件默认规则仍存在，但本镜像无对应排除匹配。pip-audit 覆盖第三方 Python 包，不覆盖解释器和系统库，不能用它的零结果否定下面的原生 Medium。

本轮未修改前端，前端测试、类型检查、lint、生产构建及 npm audit 的前轮结果见 [追加复核](security-recheck-2026-10-24.md)，未将它们记作本轮重新执行。

## 仍保留的中危告警

这些条目对应的受影响代码仍在镜像中，没有被静默忽略。详细源码、调用条件和官方补丁核对记录见 `scan/medium-assessment.json`：

| 编号 / 匹配次数 | 当前调用条件与剩余状态 |
|---|---|
| CVE-2025-15367 / 1 | POP3 客户端将含控制符的外部文本拼入命令，可能造成 POP3 协议命令注入。业务和已安装依赖未发现 poplib 调用；邮件使用 SMTP。官方修复暂未回补 3.13，当前解释器中的实现未修复。它不是操作系统命令执行。 |
| CVE-2026-12345 / 1 | 攻击者可在 TemporaryDirectory 清理时修改目录树并竞争符号链接替换，错误恢复可能越界删除或改权限。业务无直接调用；Django 的 symlinks_supported() helper 内存在调用，但未发现使用该 helper 的生产路径。测试及隔离工具使用私有临时目录。3.13 回补 PR 尚未合并；3.14.9 有相应修复，但当前项目版本范围排除 3.14，需要独立兼容性迁移验证。 |
| CVE-2025-60876 / 3 | BusyBox wget 将未拒绝/编码的控制符或空格拼入 HTTP 请求目标。wget applet 仍存在，业务未发现调用或把用户 URL 传入的路径。核验 Alpine 1.37.0-r31 全部 44 个补丁未发现修复；上游 1.38.0 也不能据版本号认定已解决。三次匹配分别来自 busybox、busybox-binsh、ssl_client 源包归属。 |

对最终镜像的 1,791 个非测试 Python 源文件做了调用搜索；它不能穷尽动态调用，也不能证明将来增加的新功能安全。当前没有建立这三项由公开业务请求触发的利用路径，但不能据此声称组件已修复或没有受影响代码。保持临时目录不受不可信本地写入者控制，避免新增未经审查的 POP3/wget 使用，继续跟踪受支持分支的官方补丁。不能把扫描器给出的 alpha/rc 修复版本直接替换到本项目要求的 3.13 运行环境；更新或迁移解释器后需同步调整断言并重新回归。

## 部署与功能边界

本次最小 libpq 不提供 GSS/Kerberos 客户端认证和 LDAP 连接服务发现。PostgreSQL 服务端 LDAP 密码认证不依赖客户端 libldap，不应混为一谈。需使用被省略的客户端功能或可选图片库时，先制作有证据的派生基线并执行同等回归与扫描。

当前镜像默认仍启动 Celery worker；独立 API、前端和 HTTPS 入口不会因构建镜像自动更新。现有开发预览、旧 worker 及未采用本基线的主机 Python 环境不包含本次制品的修复结论。发布时需要按 [Linux 部署基线](linux-deployment.md) 重建并重新创建服务，核对真实进程的运行时与配置。

正式上线尚需目标环境验证生产启动方式、正确的数据库迁移分支与备份恢复、真实 SMTP/Turnstile、私有附件权限和实际恶意软件扫描器。当前隔离联调证明扫描器不可用时文件保持私有，没有证明真实扫描器已安装或可用；派生镜像若安装 ClamAV 等工具，需重新处理其原生依赖、扫描结果及运行策略。本轮未替用户迁移或重启既有业务环境。

## 官方依据

- [Alpine 3.24 安全数据库](https://secdb.alpinelinux.org/v3.24/main.json)、[zlib 打包配置](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/zlib/APKBUILD)及[上游 zlib 补丁](https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148)：核验 `1.3.2-r1` 的实际修复。
- [Alpine PostgreSQL 18 打包配置](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/postgresql18/APKBUILD)：核验 libpq 客户端功能和依赖。
- [Pillow 源码构建文档](https://pillow.readthedocs.io/en/stable/installation/building-from-source.html)：核验逐包 codec 构建开关。
- [固定版本的官方 Grype action 定义](https://github.com/anchore/scan-action/blob/e1165082ffb1fe366ebaf02d8526e7c4989ea9d2/action.yml)：核验扫描与失败阈值配置。
- [CPython POP3 修复及未回补说明](https://github.com/python/cpython/pull/143924#issuecomment-3774869691)、[临时目录 3.13 回补 PR](https://github.com/python/cpython/pull/158429)：核验解释器的剩余状态。
- [Alpine BusyBox 构建定义](https://raw.githubusercontent.com/alpinelinux/aports/3.24-stable/main/busybox/APKBUILD)、[BusyBox wget 提议补丁](https://lists.busybox.net/pipermail/busybox/2025-November/091818.html)：核验 wget 告警仍需保留。

官方源码、补丁及关键下载哈希另存于本轮 `native/`、`runtime/`、`scan/` 证据子目录。
