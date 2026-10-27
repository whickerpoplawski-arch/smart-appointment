# 待就诊人员预约 Agent

面向招聘就诊场景的智能预约与咨询系统。患者可以在对话中说明就诊科室、就诊类型和时间，系统自动匹配合适的医生、检查日程冲突、完成预约并生成提醒；同时提供就诊 FAQ 知识问答、医生日程管理和患者偏好分析。

项目基于 FastAPI、LangChain、FAISS、SQLite 与多 Agent 协作架构，保留了原「门店预约系统」的五层架构与工程实践，把业务域整体替换为招聘就诊。

## 核心能力

- **意图识别与路由**：判断消息属于就诊预约、就诊咨询、无关问题还是后台分析，并分发给对应 Agent。
- **多 Agent 协作**：任务分类 Agent、咨询 Agent、就诊预约 Agent、患者行为 Agent 各司其职。
- **RAG 就诊咨询**：用 FAISS 检索就诊 FAQ 知识库，结合大模型回答流程、轮次、材料、地点、改期等问题，支持流式输出。
- **智能就诊预约**：按就诊科室匹配科室，检查医生日程；指定医生冲突时按简介相似度推荐同方向且空闲的医生，等患者确认后再落库。
- **日程与冲突控制**：预约记录与医生忙碌日程在同一事务内写入，冲突或异常整体回滚。
- **患者偏好分析**：记录就诊预约行为，分析常申岗位、常选医生、常用轮次与时段，用于后续推荐和跟进提醒。
- **就诊提醒**：预约成功文案里，门诊提醒提前到场，互联网医院提醒网络、摄像头、麦克风与会议链接。
- **数据管理**：知识库、医生、日程和患者行为的增删改查，数据变化后自动维护检索索引。
- **降级与兜底**：LLM 或 Embedding 不可用时退化为模板文案或默认排序，不阻断预约主流程。

## 角色与流程

患者发起预约，系统为其匹配医生：

```text
患者输入
  → 解析：姓名 / 就诊科室 / 就诊类型 / 形式 / 时间 / 时长 / 指定医生 / 偏好
  → 必填校验（姓名、岗位、轮次、时间）
  → 匹配：岗位方向 + 该时段空闲
      ├ 指定医生空闲 → 直接预约
      ├ 指定医生忙碌 → 推荐同方向空闲医生 → 患者确认
      └ 无可用医生 → 给出改期建议
  → 落库：预约记录 + 医生忙碌日程（同一事务）+ 患者行为
  → 生成成功消息与就诊提醒
```

就诊形式默认「线上」。时长未说明时按轮次取默认值：一面 45 分钟、二面 60 分钟、HR 面 30 分钟、终面 30 分钟。轮次与形式记录在预约记录中，不参与医生筛选。

## 系统架构

五层架构，核心原则是下层不反向调用上层：

```text
Web & Application Layer   app.py, web/          页面、路由入口、启动
API Layer                 api/                  外部接口、请求编排、响应封装
Agents Layer              agents/               Agent、任务路由、对话流程
Services Layer            services/            业务逻辑、推荐、向量处理
DB Layer                  db/                  数据模型、连接、Repository
```

允许的调用方向：

- Web 层调用 API 层
- API 层调用 Agents 层或 Services 层
- Agents 层调用 Services 层
- Services 层调用 DB 层

禁止：下层反向调用上层；Web 绕过 API 直接访问 Services/DB；Agents 绕过 Services 直接访问 DB。

### Agent 设计

- **Task Classification Agent**：意图分析、Agent 路由、对话状态管理、无关请求兜底。
- **Consultation Agent**：区分咨询类型，检索就诊 FAQ，构建提示词并流式回答。
- **Appointment Agent**：解析预约信息、匹配医生、处理推荐确认、写入预约并生成提醒。
- **Patient Behavior Agent**：记录患者的就诊行为，分析偏好模式，生成跟进提醒。

## 数据模型

```text
doctors                  id, name(unique), department, bio
doctor_schedules         id, doctor_id, start_time, end_time, status, appointment_id
appointments             id, patient_name, specialty, visit_type, channel, doctor_id,
                         start_time, end_time, status, session_id, created_at
knowledge_documents      知识库文档与向量
patient_behaviors        患者行为记录（action_type='appointment' 等）
patient_preferences      患者偏好（科室、就诊类型、渠道、时段、医生）
patient_recommendations  患者推荐与跟进提醒
```

预约记录承载业务语义，医生日程只负责占用与冲突检查，两者通过 `appointment_id` 关联。

## 目录结构

```text
待就诊人员预约 Agent/
├── agents/                          # Agent 层
│   ├── appointment/                 # 解析、医生匹配、消息构建、落库
│   ├── patient_behavior/          # 患者行为记录、偏好分析
│   ├── consultant/                  # RAG 咨询
│   └── task_classification/         # 意图分类与路由
├── api/                             # 接口层
│   ├── appointment.py               # 就诊预约接口
│   ├── doctor.py                    # 医生与日程接口
│   ├── patient_analysis.py        # 患者分析接口
│   └── knowledge.py                 # 知识库接口
├── services/                        # 业务层
│   ├── appointment_service.py
│   ├── doctor_service.py
│   ├── patient_behavior_service.py
│   └── knowledge_service.py
├── db/                              # 数据层
│   ├── models.py
│   ├── db_router.py
│   └── repositories/
├── web/                             # 页面与模板
├── data/                            # SQLite 数据库
├── tests/                           # 测试
├── app.py                           # 应用入口
├── requirements.txt
└── .env.example
```

## 快速开始

### 1. 创建虚拟环境

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中填写模型与数据库配置：

```env
MODEL_PROVIDER=qwen
LLM_API_KEY=your_llm_api_key_here
LLM_BASE_URL=your_openai_compatible_chat_base_url_here
LLM_MODEL=your_chat_model_name_here

EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_embedding_api_key_here
EMBEDDING_BASE_URL=your_openai_compatible_embedding_base_url_here
EMBEDDING_MODEL=your_embedding_model_name_here

DATABASE_URL=sqlite:///data/doctor_appointment.db

DEBUG=True
LOG_LEVEL=INFO
```

支持 OpenAI 兼容格式的聊天与 Embedding 服务，也支持 Azure OpenAI（`MODEL_PROVIDER=azure`）。

### 4. 启动服务

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

访问：

- Web 页面：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs
- ReDoc：http://127.0.0.1:8000/redoc

## 主要页面

| 页面 | 路径 | 模板 |
|---|---|---|
| 聊天与预约 | `/` | `web/templates/index.html` |
| 医生管理 | `/doctor` | `web/templates/doctor.html` |
| 医生日程 | `/doctor_schedule` | `web/templates/doctor_schedule.html` |
| 患者分析 | `/patient_analysis` | `web/templates/patient_analysis.html` |
| 知识库管理 | `/knowledge` | `web/templates/knowledge_management.html` |
| 系统管理 | `/admin` | `web/templates/admin_dashboard.html` |

主要接口：

- `GET /api/doctors/`、`GET /api/doctors/{id}`
- `GET /api/doctors/{id}/schedule`、`GET /api/doctors/schedules/today`
- `GET /api/appointments/`、`GET /api/appointments/{id}`

## 种子数据

首次启动会写入 10 个科室、每科至少 2 名虚构医生，并在空库写入 8 条就诊须知：预约步骤、就诊类型时长、互联网医院设备、门诊演示地址、携带物品但不入库、改期、到诊提醒、演示不收费。

## 测试

```bash
pytest
```

测试覆盖：输入解析与状态累积、就诊类型默认时长、推荐确认与拒绝、按科室匹配医生、指定医生冲突推荐、无可用医生、事务落库与冲突回滚、患者行为与偏好分析、咨询提示词与分类、任务路由与状态流转。

## 设计取舍

- **就诊类型不参与筛选**：医生表保持精简（科室 + 简介），就诊类型只影响默认时长与文案。
- **按请求区分会话**：聊天请求携带 `session_id`，槽位和状态按会话隔离，并写入 SQLite，进程重启后可以恢复缺槽和是否在等确认。患者仍只采集姓名，不建完整患者主表。
- **不迁移历史数据**：业务域已更换，旧按摩门店数据无复用价值，数据库重建。

## 项目来源

本项目由原作者的开源项目 [jerry-ai-dev/smart-appointment-ai-agent](https://github.com/jerry-ai-dev/smart-appointment-ai-agent)（按摩门店技师预约系统）改造而来，保留了其分层架构、多 Agent 协作与 RAG 工程实践，业务域替换为医生档期预约演示。
