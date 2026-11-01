# WANSHANG 代码交接说明

这份代码供内部同事在现有网站上增加 AI 功能。业务名称、联系方式、产品分类及现有业务逻辑保留原样；实际环境配置和 Git 历史不在包内。2026-11-01 起已接入网站智能助手（DeepSeek）并完成产品参数、搜索、询价单改造，详见 `docs/智能化升级实施记录.md`。

## 包内内容

| 目录或文件 | 用途 |
| --- | --- |
| `frontend/` | Next.js 16、React 19、TypeScript 网站，含产品、搜索、询价、联系和内容页面 |
| `backend/` | Django 5.2、Django REST framework、Django Admin、Celery，以及数据库迁移和测试 |
| `docker-compose.yml` | 本地 PostgreSQL、Redis、Celery 工作进程 |
| `deploy/` | 生产部署脚本与配置结构，使用时须自行准备环境配置和证书 |
| `docs/` | 原部署、安全说明，以及新增的 `AI_INTEGRATION.md` |
| `.github/` | CI 工作流；其中的测试环境值是测试占位值 |
| `stitch_industrial_axle_pro/` | 原界面设计参考，与实际应用源码分开放置 |
| `README.md` | 原项目说明和维护命令 |
| `HANDOFF_MANIFEST.json` | 包内文件清单及 SHA-256，用于核对文件完整性 |

前后端依赖锁文件与 `frontend/vendor/minimatch-compat` 均已保留。安装依赖时使用锁文件，不要只复制 `src/`。

## 环境配置与数据

真实的 `.env`、生产环境配置、Git 仓库历史、数据库、客户询盘和附件、备份、运行日志、依赖目录、缓存及构建输出均未打包。包内的业务模型和测试样例属于源码，不代表真实客户数据。

仅保留 `.env.example` 与 `deploy/env.production.example` 两份环境示例，它们使用占位密码，不提供真实账号密码。示例仍含现有业务联系方式，这是本次交接允许保留的信息。不要将示例值直接用于生产。

请在独立目录和开发数据库中工作。生产账号、SMTP 密码、Turnstile 密钥、服务器访问权限及未来 AI 服务密钥，由项目负责人另行提供；开发阶段使用自己的测试配置即可。

## 本地启动

准备 Docker、Node.js 与 npm、uv 0.13.5 或更新版本；后端 Python 版本以 `backend/.python-version` 为准。项目依赖版本以包内锁文件为准。

在解压后的根目录执行：

```powershell
Copy-Item .env.example .env
```

编辑新建的 `.env`，保持 `DJANGO_ENV=development`。自行设置开发用 `DJANGO_SECRET_KEY`、PostgreSQL 密码和 Redis 密码，并同步修改 `DATABASE_URL`、`REDIS_URL`、`DJANGO_CACHE_URL` 内的连接密码。保留控制台邮件后端，避免向真实客户发信。

根目录的 `.env` 供 Django 与 Compose 使用。Next.js 的本地配置放在 `frontend/.env.local`，只需填写前端使用的配置，例如：

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api
API_BASE_URL=http://localhost:8000/api
NEXT_PUBLIC_SITE_URL=http://localhost:3000
NEXT_PUBLIC_ENABLE_STARTER_FALLBACK=false
```

在根目录启动本地基础设施。若本机已有同类服务，先调整 `.env` 中的端口与对应连接地址，避免冲突：

```powershell
docker compose -p wanshang-ai-dev up --build -d
```

启动后端：

```powershell
cd backend
uv sync --locked
uv run python manage.py migrate
uv run python manage.py configure_wanshang
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

网站：`http://localhost:3000`；API：`http://localhost:8000/api/`；管理后台：`http://localhost:8000/admin/`。Unix 本地安装还需按原 README 准备系统 libpq。

`configure_wanshang` 会写入公司资料、六个产品分类、车轴参数清单和助手默认设置，不会生成真实产品。启用助手需在管理后台“AI providers”里添加至少一家大模型供应商并填入密钥（可配多家，按优先级自动切换）。需要样例产品时，仅在可丢弃的演示数据库运行 `seed_demo`；演示参数不能作为真实产品承诺。

## AI 开发入口

先看 [AI 接入说明](docs/AI_INTEGRATION.md)，再阅读以下源码：

- 产品和内容数据：`backend/catalog/models.py`、`serializers.py`、`views.py`。
- 现有接口路由：`backend/catalog/urls.py`、`backend/config/urls.py`。
- 前端数据读取与类型：`frontend/src/lib/api.ts`、`types.ts`。
- 询价流程：`frontend/src/components/RFQForm.tsx`、`ProductInquiryForm.tsx`，以及后端 `submissions.py`、`tasks.py`。

本次没有重排运行代码，也没有更改现有接口；新增材料只用于说明项目结构与后续开发位置。

## 开发完成后的检查

```powershell
cd backend
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py test
```

```powershell
cd frontend
npm run test:security
npm run lint
npx next typegen
npm run typecheck
```

生产构建另需满足原 README 中的公开 HTTPS 地址配置要求。涉及 PostgreSQL、Redis 并发行为和部署时，再运行原项目的容器集成检查。

交付新增 AI 功能时，应附修改清单、独立配置项、数据库迁移（如有）、验证结果及关闭 AI 的方式。交接包本身不提供生产部署权限。
