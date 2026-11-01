# 剩余 High 告警与上线风险评估

> 后续已替换原生运行时并重新验证，最新结果见 [原生安全修复报告](security-remediation-2026-10-24.md)。本文保留为下述旧 Debian 镜像的历史评估，不代表新镜像的剩余告警。

评估日期：2026-10-24（Asia/Shanghai）。对象为当前业务代码及后端 Linux amd64 镜像 `wanshang-linux-check:f9a0093705ad`，Docker OCI index 为 `sha256:f5ec317ba5a51c86a1975f9c2a83f87ac1e5e7152559bef85de8f9a1c9b9bdc9`。

**判断：在本报告的制品、权限与调用边界持续成立时，这 15 项 High 没有发现可由当前网站公开请求直接触发的已确认利用路径，不应仅凭其扫描级别否决该镜像。不过，当前开发预览不能直接公开上线；正式部署和附件扫描等验收仍未完成。**

这是一项有边界的可利用性评估，不是修改漏洞原始严重度，也没有把扫描失败改成通过。Grype 原始报告仍保留，`--fail-on high` 仍为退出码 2。此前的 263 项 PostgreSQL/Redis 回归和前端构建验证见 [追加安全审查](security-recheck-2026-10-24.md)；本轮只读评估未修改应用、镜像、业务数据或运行服务，没有运行漏洞利用程序。

## 评估结果

| 分类 | 数量 | 发布意见 |
|---|---:|---|
| 受影响组件未安装 | 3 | 两个 Perl 模块和 ACL 命令缺失；对这个精确镜像可记录为不受对应缺陷影响，不能外推未来镜像。 |
| 当前平台实现不受对应缺陷影响 | 1 | libstdc++ 的 aligned new 实际调用 posix_memalign，不走相关溢出分支；只对已检查的 amd64 制品成立。 |
| 需要额外调用链、特权或配置的条件风险 | 10 | 当前未识别公开业务触发路径；保留下面的权限和运维约束，不作为各自独立上线阻断。 |
| 版本范围有争议，保留调查 | 1 | zlib 官方发行版仍标记受影响；当前业务没有找到所需 gzip 写入路径，暂不判为误报，也不据此单独阻断该制品。 |

因此，**可以从“尚无实质解释的 15 条 High”推进到“4 条有具体不受影响证据、10 条有条件风险、1 条保留调查”**。原始的 80 个独立条目中，其他 65 个 Medium/Low/Negligible 不在本次逐条评估范围内；没有被自动排除。

## 15 项逐条判断

| CVE / 组件 | 漏洞实际触发条件 | 本项目证据与判断 |
|---|---|---|
| [CVE-2026-82560](https://security-tracker.debian.org/tracker/CVE-2026-82560)，Perl Pod::Text | 格式化特制 POD 文本导致 CPU/内存耗尽。 | 镜像只有 perl-base；Pod::Text 正常导入失败，模块及 pod2text 命令均不存在。**受影响组件未安装，不阻断此镜像。** |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538)，Perl Archive::Tar | 读取恶意 tar 头部声明的超大长度，分配过量内存。 | Archive::Tar 正常导入失败，模块及 ptar 命令均不存在。**受影响组件未安装，不阻断此镜像。** 源包级匹配不能证明最小 perl-base 含该模块。 |
| [CVE-2026-54370](https://security-tracker.debian.org/tracker/CVE-2026-54370)，ACL 命令 | 特权 getfacl/setfacl/chacl 遍历攻击者可修改的路径时发生竞态。 | 镜像没有 acl CLI 包，三个命令均未安装。**具体受影响命令缺失，不阻断此镜像。** 这不排除下面另一项 libacl API 风险。 |
| [CVE-2026-95619](https://security-tracker.debian.org/tracker/CVE-2026-95619)，libstdc++ aligned new | 某些分配实现对巨大 size 做对齐舍入时整数溢出。 | Pillow 初始化确实加载 libstdc++；进一步检查实际 ELF 函数及 PLT/GOT，确认 aligned new 调用 posix_memalign，上游修复说明该实现不受此问题影响。**本次平台实现不受影响，不是 Debian 包已修复。** |
| [CVE-2026-102010](https://security-tracker.debian.org/tracker/CVE-2026-102010)，GNU PBDS binary_heap | 应用使用特定 priority queue 的 erase_if，触发内部指针未更新。 | 当前代码未使用该结构，已检查对象没有相应已识别调用。模板可能内联，不能凭符号缺失证明所有 wheel 静态实例均不含相关代码。**当前风险低，置信度中等；添加 C++ 扩展后重评。** |
| [CVE-2026-5435](https://security-tracker.debian.org/tracker/CVE-2026-5435)，glibc DNS 格式化 | 调用过时 ns_printrrf/ns_printrr/fp_nquery 格式化特制 TSIG 记录。 | 网站的普通 DNS 解析不等于这些格式化函数；应用和检查对象中未发现相关入口。**不单独阻断；新增 DNS 诊断/格式化工具时重评。** |
| [CVE-2026-19499](https://security-tracker.debian.org/tracker/CVE-2026-19499)，glibc strfmon | 用特定右对齐宽度和目标缓冲区调用 strfmon/strfmon_l。 | 当前应用与检查对象中未识别这些调用。**当前风险低，不单独阻断；添加原生货币格式化模块后重评。** |
| [CVE-2026-107778](https://security-tracker.debian.org/tracker/CVE-2026-107778)，Kerberos | GSS 接收服务处理票据与 ticket_info 数量不匹配的 KRB-CRED。 | libpq 仅导入发起端 gss_init_sec_context，没有发现项目提供 GSS 接收服务。**当前角色不匹配，不单独阻断。** 不能把“攻击者需先认证”当作通用缓解；上游指出部分未加密 KRB-CRED 路径可能无需先认证。 |
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720)，ncurses infocmp | infocmp CLI 解析特制终端输入。 | infocmp 实际存在，不能写成组件缺失；业务代码不调用该工具，也未把访客输入交给它。**当前未发现公开请求触发路径，不单独阻断。** |
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642)，util-linux mount | 失败的挂载 helper 后仍执行特权 X-mount 后置钩子。 | mount 有 SUID 位，但 fstab 只有注释、app 不可写；受审部署非 root、capabilities 清零、NoNewPrivs=1，未提供特权挂载业务。**当前条件不成立；不得新增此类授权。** |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408)，nsenter | 特权操作员对攻击者控制目标执行 nsenter --join-cgroup，继承泄露的特权 FD。 | nsenter 是普通 0755；应用不调用。**限制特权运维流程后可接受。** NoNewPrivs 不能收回已经打开并继承的特权 FD，不能单靠容器开关排除此项。 |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409)，mount subdir | Linux ≥6.15 加 fstab 授权的 X-mount.subdir，配合中间路径符号链接。 | 没有相关 fstab 授权且 app 无挂载特权。**由权限/配置缓解。** 本机内核 6.6 的结果不推广为目标生产内核判断。 |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410)，受限 bind mount | 替换 fstab 授权源路径，特权 mount 的 owner/group/mode 钩子作用到错误目标。 | fstab 无授权条目，app 不能改写，也没有项目 helper 调用。**当前条件不成立；新增委托挂载则重新阻断评估。** |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369)，libacl API | 特权调用者对攻击者可替换的路径使用旧 acl_*_file API。 | libacl1 存在，项目没有找到此类 API 或特权维护任务，代表性业务导入也未映射 libacl。**限制特权维护流程后可接受。** 禁止 root 对上传可写目录树使用不安全 ACL 操作；未来升级 ACL 2.4.0 后调用者仍需采用新的安全 API。 |
| [CVE-2026-85091](https://security-tracker.debian.org/tracker/CVE-2026-85091)，zlib | gzip 写入停顿后，外部缓冲区状态与 gzwrite/gzprintf/gzvprintf 组合触发溢出。 | Python/Pillow 实测均用系统 zlib 1.3.1；当前上传图片校验不是 gzip writer。131 个对象的动态符号检查未发现相关 gz* 导入，CPython zlib 模块与 Pillow ZIP 解码源码使用 inflate/deflate。**未识别所需业务路径，但保留版本争议调查，不标记包已修复或全局误报。** |

## 评估证据及其边界

使用最终镜像进行正常模块导入、Pillow 全插件初始化、普通 PNG/JPEG/WebP 校验、libpq 加载以及 ELF 动态符号/分配函数引用检查。共检查 131 个 Python/Pillow/相关原生对象。没有用“代码没有 C++”排除 native 风险：Pillow 实际确会加载 libstdc++；也没有假设 Image.open 只会自动识别三种许可格式，全插件边界已纳入检查。

动态符号检查不能排除所有内联模板、静态链接或未来 dlsym 路径，因此 PBDS 等条目保留相应置信度与重评条件。缺模块的三个条目采用导入、命令查找、安装状态及官方拆包清单交叉核验。正常探测不等于漏洞利用测试，也不覆盖未指定的独立 API 主机、arm64 制品、宿主机管理工具或前端其他原生依赖。

zlib 额外核对了 [Debian 实际版本源码](https://sources.debian.org/src/zlib/1%3A1.3.dfsg%2Breally1.3.1-1/gzwrite.c/) 与 [上游 v1.3.1 源码](https://github.com/madler/zlib/blob/v1.3.1/gzwrite.c)：两份文件 SHA-256 相同，都没有较新 writer 中的 gz_vacate。但 [Debian bug 1146895](https://bugs.debian.org/1146895) 和 [上游讨论](https://github.com/madler/zlib/issues/1310) 仍有旧版本触发范围疑问，所以没有仅凭缺少函数名或版本号结案。当前不阻断的主要依据是项目没有找到所需 writer 调用链。

对 aligned new 的判断另有 [GCC 上游补丁与说明](https://github.com/gcc-mirror/gcc/commit/200183d4ab5d57575f3dfebc6af3baf649910b9e) 和当前二进制调用证据。Kerberos 角色及认证前提参考 [上游 PR 1511](https://github.com/krb5/krb5/pull/1511)。本地提权不能只看非 root：[内核 NoNewPrivs 文档](https://docs.kernel.org/userspace-api/no_new_privs.html) 说明它不能撤销已有特权；对应限制按实际部署组合判断。

原始证据保存于 `output/security-risk-assessment-2026-10-24/`：`native-assessment.json`、`native-runtime-evidence.json`、`native-expanded-symbol-evidence.json`、`native-aligned-new-evidence.json`、`local-privilege-assessment.json`、`perl-and-release-assessment.json`、`zlib-assessment.json`。所有正常探测容器均已清理。

## 真正需要完成的发布门槛

| 门槛 | 当前判断 | 上线所需结果 |
|---|---|---|
| 当前开发预览直接对外公开 | **阻断。** 本机是 development/DEBUG、示例 secret，预览用 runserver/next dev。 | 使用独立生产密钥、正式 HTTPS/CORS/代理/验证码设置和生产服务命令，核验目标运行态及真实 HTTP。 |
| 旧 worker 是否处理本项目任务 | **目标归属未确认。** 旧服务仍是较早依赖，且未配置新容器权限限制；不能套用本报告缓解结论。 | 若参与本次站点队列，更新为匹配版本并核验权限；若属于另一项目，保持隔离，不因它在本机存在就无差别操作。 |
| 目标数据库迁移链与恢复 | **目标库未验收。** 历史库存在 drone/axle 分叉，不足以证明新的独立目标库也有问题。 | 选定正确库/迁移链，完成备份副本恢复和迁移演练；独立 axle 新库可避免复用旧分叉。 |
| 保留附件功能 | **验收阻断。** 当前镜像无真实扫描引擎；故障关闭保持隔离，未证明可以正常处理干净文件。 | 配置并验收真实扫描器、特征库和共享私有存储，验证干净/感染/失败/恢复流程；若暂不提供附件，必须在服务端停止接收，而非仅隐藏前端。 |
| 正式域名、验证码、SMTP、代理及 HTTP 验收 | **证据尚不完整。** 合成设置、console 邮件和构建产物不能证明真实部署已通过。 | 两类表单和通知、token/action/hostname、TLS、429/503、请求体限制、私有路径及安全响应头实际通过。 |

原始镜像没有扫描引擎，安装扫描器会形成新制品。扫描器可能带入 Perl 模块、C++ 或 gzip writer；**必须对真正发布的扫描器镜像重新检查，不能直接沿用本次组件缺失或无调用链结论。** 同理，新增权限、授权 fstab 挂载、GSS 接收服务、原生扩展或后台 root 维护脚本，都会触发相关重评。

## 发布建议

业务代码可以进入生产候选验收。对上述精确镜像及约束，建议记录定向、有条件的风险结论，无需为了让扫描数字归零而混装发行版、删除必需库或全局忽略 High。**正式上线仍以发布门槛完成为准，目前不建议把现有预览直接发布。** 本轮没有代替项目方接受所有风险，也没有修改 CI 或扫描器忽略规则。
