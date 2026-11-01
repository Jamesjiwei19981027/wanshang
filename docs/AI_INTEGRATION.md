# WANSHANG AI 接入说明

现有系统负责产品展示、资料下载、询价提交、通知和后台管理。AI 功能应在这些流程上增加独立能力。以下目录和接口来自当前源码；新增模块、接口及字段是开发建议，尚未实现。

## 现有数据流

```text
Next.js 页面和表单
        │
        ▼
Django REST API ─── PostgreSQL
        │
        ├── Redis 限流与缓存
        └── Celery 通知和附件扫描

Django Admin ─── 公司资料、产品、内容、询盘及跟进
```

| 业务 | 主要源码 |
| --- | --- |
| 产品、分类、应用、规格 | `backend/catalog/models.py` 中的 `Product`、`Category`、`Application`、`AxleSpec` |
| 产品文档与图片 | `ProductDocument`、`ProductImage`，以及 `file_security.py`、`storage.py` |
| 公司资料与内容 | `SiteSetting`、`CompanyProfile`、`CompanyPhoto`、`NewsArticle`、`Banner` |
| 询价与跟进 | `Inquiry`、`InquiryItem`、`InquiryFollowup`、`InquiryAttachment` |
| 留言与访问归因 | `ContactMessage`、`VisitSession`、`PageView`、`AnalyticsEvent` |
| 接口校验与输出 | `backend/catalog/serializers.py`、`views.py`、`renderers.py` |
| 前端请求和表单 | `frontend/src/lib/api.ts`，以及 `RFQForm.tsx`、`ProductInquiryForm.tsx`、`ContactForm.tsx` |

## 可复用接口

以下路径均相对于站点根地址，实际字段以序列化器为准：

| 接口 | 用途 |
| --- | --- |
| `GET /api/products/` | 产品筛选与分页，支持产品列表查询 |
| `GET /api/products/{slug}/` | 已发布产品详情、规格及关联资料 |
| `GET /api/products/{slug}/related/` | 现有规则式关联产品 |
| `GET /api/categories/`、`GET /api/applications/` | 分类与应用场景 |
| `GET /api/filters/` | 可用筛选选项 |
| `GET /api/documents/` | 公开可用的产品文档列表 |
| `GET /api/news/`、`GET /api/news/{slug}/` | 已发布新闻内容 |
| `GET /api/company-profile/`、`GET /api/site-settings/` | 对外公司和网站资料 |
| `POST /api/inquiries/` | 提交询价 |
| `POST /api/contact/` | 提交联系留言 |

对应接口也挂载在 `/api/v1/`。未版本化的 `/api/` 保留原始响应；`/api/v1/` 的成功响应使用 `{ success, data, message }`，错误使用 `{ success, error_code, message, fields }`。分页也有差异：原始产品列表返回 `count/next/previous/results`，v1 的 `data` 返回 `results/page/page_size/total/total_pages`。接入时不要只改 URL 而忽略响应结构。

网站已有 `/llms.txt` 和 `/llms-full.txt`，用于输出网站及公开产品信息。它们不是聊天接口，也不意味着已有模型服务、向量数据库或 RAG 检索系统。

## 建议先实现的功能

建议第一阶段只做公开产品问答与选型辅助：用户描述用途、承载量、尺寸、制动方式和数量，AI 从已发布资料中检索，返回相关产品链接、依据和缺失参数。参数不足时追问；没有资料时转人工。

第二阶段可增加后台询价摘要、翻译和回复草稿。该阶段会接触客户数据，需要明确后台权限、可发送字段、模型服务及保留规则。回复草稿先由员工确认再发出。

## 建议新增的模块

| 建议位置 | 职责 |
| --- | --- |
| `backend/ai_assistant/` | 独立 Django 应用，承接 AI 路由、权限和业务编排 |
| `backend/ai_assistant/providers.py` | 封装模型服务调用、超时、重试和错误转换 |
| `backend/ai_assistant/retrieval.py` | 查询或索引已发布产品与内容，保留来源记录 |
| `backend/ai_assistant/serializers.py` | 校验问题长度、允许的字段与结构化响应 |
| `frontend/src/components/AiAssistant.tsx` | 问答入口、来源链接、加载与失败提示 |
| `frontend/src/lib/ai.ts` | 调用本站 AI API；不直接持有模型密钥 |

应用注册放在 `backend/config/settings.py`，路由接入 `backend/config/urls.py`。新服务的密钥、模型名称和限额由环境变量配置。变量名和具体模型服务由实现者确定；当前项目没有读取任何 AI 专用配置。

## 建议接口契约

可以新增 `POST /api/v1/ai/ask/`，先返回非流式 JSON；这一路径目前不存在。若复用现有 `VersionedJSONRenderer`，视图返回普通业务数据，让 renderer 统一包裹，避免重复套一层 envelope。

请求示意：

```json
{
  "question": "需要带制动的车轴，应该补充哪些尺寸？",
  "product_slug": null
}
```

成功响应示意：

```json
{
  "success": true,
  "data": {
    "answer": "请补充额定承载量、轮毂面距和弹簧中心距。",
    "sources": [],
    "missing_fields": ["rated_capacity", "hub_face_length", "spring_center"],
    "needs_human_review": true
  },
  "message": ""
}
```

来源应指向实际查询到的公开产品或文档。让后端根据检索结果生成链接，不接受模型任意编造的 URL。未来需要流式响应时，再单独定义流式协议和失败行为。

## 开发边界

- 模型密钥只放在后端环境中，不能放入 `NEXT_PUBLIC_*`、浏览器代码或前端响应。
- 首版知识源限于对外发布的数据；沿用当前视图的发布状态、有效期和关联对象可见性筛选，不能直接把整个数据库交给模型。
- 不把询盘邮箱、电话、后台备注、访问归因和私有附件纳入公开知识库。后台摘要功能只读取已授权记录及必要字段。
- 保留询价的 Turnstile、限流、`Idempotency-Key`、单位校验、附件隔离和扫描机制。AI 可以预填草稿，正式提交仍走现有表单和接口。
- 产品资料与用户输入都作为内容处理，不能把其中的文字当作调用工具、读取文件或提升权限的指令。
- 不让 AI 自行承诺载重适配、认证、价格、交期或自动发送报价。选型结论由业务人员确认。
- 模型调用设超时、请求大小、访问限额和费用上限。模型不可用时显示可理解的错误，原产品浏览与询价仍应可用。

## 验收重点

先验证公开产品有依据可追溯、未发布内容不会被检索、信息不足会追问、无依据不会编造。再验证密钥不会到达浏览器、模型超时不会阻塞原询价、重复提交不会新增询盘或重复通知，以及输入与文档中的指令不能触发越权读取。

对实际新增行为补相应测试，并运行 `START_HERE.md` 中的原项目检查。交付时说明所选模型服务、发送字段、保留策略、调用成本及关闭开关。
