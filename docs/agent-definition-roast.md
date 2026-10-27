# 它算不算 Agent：按源码分层，不按 README

对象：本仓库当前 `main`（面试预约）。方法：读 `agents/`、`api/chat_handler.py`、`web/routes.py`、`requirements.txt`。不跟 README 的「多 Agent 协作」自我介绍走。

## 裁决

**算一种很窄的 Agent，不算你简历上那个 Agent。**

主架构是 **(D) 领域工作流 / 有限状态机机器人**：LLM 负责分类和抽槽，Python 负责下一步做什么。旁边挂着一块 **(A) 一次性 RAG 聊天**，和一块预约成功之后才启动的 **(B) 单工具旁路**。**(C) 多 Agent 编排** 和 **(E) 长程 coding harness** 都名不副实。

对外可以叫「面试预约助手」或「任务型对话系统」。技术上最多写成：「垂直任务型对话 Agent：LLM 抽槽，规则策略执行。」写成「自研多智能体 / ReAct / MCP / 对标 Claude Code」会被这一份文件当面拆掉。

做得对的地方，两句：槽位只在非「未知」时覆盖（`InterviewProcessor.update_history_from_data`），落库失败置 `booking_failed` 并保留已填信息（`InterviewAgent.run_stream`）；`find_interviewer` 的参数合同和四态返回是认真的工具边界（`agents/interview/interviewer_tools.py`）。问题是这套合同没有接到模型的动作空间上。

---

## 1. 定义分层：本仓落在哪一层

| 层 | 判据 | 本仓 |
|---|---|---|
| (A) LLM chatbot | 一轮提示进、文本出，无动作选择 | 咨询主路径就是这个。`ConsultationProcessor.process_consultation_stream`：检索 `top_k=3` → 生成 → 结束。`ConsultantAgent._reset_state_after_consultation` 立刻把共享状态打回 `CLASSIFY`，没有多轮咨询记忆 |
| (B) tool-using agent / ReAct | 模型从工具集里选动作，读 observation，再决定下一步，直到停 | **主路径没有。** 全仓 `create_openai_tools_agent` / `AgentExecutor` 只出现在 `InterviewProcessor.__init__`，工具只有 `WeatherMCPTool`，调用点在 `_process_successful_interview`：库已经写成功，线下面试才 `ainvoke` 一次，生成不超过 80 字的出行提醒。这是配菜，不是控制环 |
| (C) multi-agent orchestration | 多个 Agent 有隔离上下文、独立工具、失败可重派 | **没有。** `AgentRouter` 持有两个对象指针，`_setup_agent_states` 把同一个 `SharedState` 塞进去。`CandidateBehaviorAgent` 根本不在路由器里 |
| (D) domain workflow / FSM bot | 状态和转移预先写死，模型只填表或生成话术 | **这是本体。** `StateEnum` 三个业务态：`CLASSIFY` / `INTERVIEW` / `CONSULT`（`config/constants.py`）。`ClassificationProcessor.process_task_stream`：该分类就分类，否则 `route_by_state` 续聊 |
| (E) coding / long-horizon harness | 开放多步、预算、checkpoint、可验证停止、压缩上下文 | **零。** 无 max-turns、无 checkpoint、无独立 verifier、无计划-执行分离。完成条件是「四个必填槽齐 + `save_interview` 返回成功」 |

### 能不能自称「Agent」

可以，但必须带定语，而且定语要和代码一致。

- 产品/简历安全说法：**招聘面试场景的任务型对话系统（槽位填充 + 规则匹配 + RAG 问答）**。
- 勉强能说的技术说法：**单进程、FSM 约束的垂直对话 Agent**。模型有感知（分类、抽 JSON），没有动作选择权。
- 不能说：多智能体系统、ReAct Agent、MCP Agent、Planner、长程 Agent、Agent 框架。

`agents/interview/interviewer_tools.py` 模块文档自己写了：「调用方（当前是 InterviewProcessor，后训练后是 Planner 小模型）」。作者知道 Planner 还不存在。README「设计文档」却链到 `docs/superpowers/specs/2026-10-26-interview-planner-finetune-spec.md`，仓库里没有 `docs/`（本文件除外）。预告不能当实现。

### 简历怎么写才不翻车

写你真正改过、测试能指到的东西：

> 在开源门店预约项目上做招聘域改造。对话侧用 LLM 做意图分类和槽位抽取（姓名 / 岗位 / 轮次 / 时间），策略侧用 Python 状态机完成追问、面试官冲突检查、推荐确认和同一事务落库。另有 FAISS FAQ 问答。`find_interviewer` 做成了固定参数合同，当前由业务代码调用，尚未接到模型 tool-call。

不要写：自研多 Agent、工具自选、MCP、后训练 Planner、长链路 harness。来源一句要留着——README「项目来源」已经指向 jerry-ai-dev/smart-appointment-ai-agent，面试里装成从零设计会被目录映射打脸，那是另一份审计的结论，这里不重复展开。

---

## 2. 证据清单

### 自主 tool selection：主路径没有

一轮用户消息在 `InterviewAgent.run_stream` 里的顺序是写死的：

1. `InputParser.parse_stream` 让模型吐 JSON（姓名、岗位、轮次、形式、时间、时长、面试官、偏好、确认、unrelated）。
2. `parse_data` 失败就整表填「未知」，不重试、不换策略。
3. `update_history_from_data` 合并槽位。
4. 分支：`unrelated` → 回调归类；槽齐或 `awaiting_confirmation` → `handle_complete_interview`；否则 → `handle_incomplete_info` 按缺槽列表提问。

`handle_complete_interview` 里检索面试官不是模型选的。`InterviewerFinder.find_interviewer_with_thought` 自己拼好四个键，再调用 `find_interviewer(...)`。`FIND_INTERVIEWER_SCHEMA` 只被 `tests/test_interviewer_tools.py` 断言过，没有任何 `bind_tools`。

界面上的 `[THOUGHT][面试机器人] 正在按岗位方向查找空闲面试官...` 是 `yield_func` 里的格式化字符串，不是模型的 thought。把 UI 标签当成 CoT，是自欺。

确认意图更不是模型决策：`_handle_recommendation_response` 用正负词表 `any(pos in user_response)`。「不可以」同时命中「可以」和「不」，靠 `is_positive and not is_negative` 挡一下。这是关键词机器人。

### observation → act 环：一轮用户消息算一次观测，策略是 if

真正「读工具结果再决定下一步」的环只有天气旁路：`create_openai_tools_agent` + `AgentExecutor`，工具列表 `self.tools = [self.weather_tool]`，而且包在 `if mode == '线下' and self.agent_executor` 里，发生在 `save_interview` 成功之后。预约失败、缺槽、冲突，都不会进这个环。

`WeatherMCPTool._get_weather_data` 用 `aiohttp` 打 `api.openweathermap.org`。返回文案写死「北京当前天气」，`city` 参数不影响这句话的城市名。类名带 MCP，实现是 HTTP。`requirements.txt` 有 `mcp>=1.0.0` 和 `langchain-experimental`，全仓库 Python 源码零 `import mcp`、零 `langchain_experimental`。

主对话的「环」是跨轮的：用户下一句话是唯一新观测，Python 再走同一条管线。这是对话系统，不是 agentic loop。

### 状态机代替了规划

`StateManager.allowed_transitions` 只允许：

```text
CLASSIFY → INTERVIEW | CONSULT
INTERVIEW → CLASSIFY
CONSULT → CLASSIFY
```

`can_transition_to` 全仓只有定义、没有调用方。真正改状态的是 `set_state`，以及 `InterviewAgent.run_stream` / `ConsultantAgent._reset_state_after_consultation` 直接写 `self.state.value = StateEnum.CLASSIFY`。转移表是装饰。

分类器更说明「标签 ≠ 能力」。`TaskClassifier` 的提示词要求输出五类：`interview` / `query` / `pay` / `statistics` / `other`。`ClassificationProcessor.process_task_stream` 只路由前两个，其余一律 `handle_unsupported_task`，固定回复「暂不支持该类型任务」。`pay` 和 `statistics` 是提示词里的幽灵状态，没有规划器接它们。

`StateEnum.OTHER` 同样无人使用。

### 多 Agent：同进程路由，不是隔离

`api/chat_handler.py` 的 `get_task_agent()` 惰性创建一个 `TaskClassificationAgent`，里面 new 一个 `InterviewAgent` 和一个 `ConsultantAgent`。三者同一进程、同一 `SharedState` 实例（一个带 `.value` 的可变对象，不是消息总线，也不是每个 Agent 一份 transcript）。

`CandidateBehaviorAgent` 不在 `AgentRouter` 构造参数里。咨询路径在 `ConsultationProcessor._record_consultation_behavior` 里当场 `CandidateBehaviorAgent()`，再调 `record_behavior`。预约落库路径走的是 `InterviewDatabase.candidate_behavior_service`，连这个类都不经过。它的 LLM 用途是 `generate_personalized_reminder`：拼好偏好字符串，`ainvoke` 一次，要 80 字文案。这是报表函数，不是协作 Agent。

没有独立工具沙箱，没有独立记忆，失败不会把子任务重派给另一个 Agent。`route_to_interview` 捕获异常后 `reset_to_classify`，然后把错误字符串 yield 出去。重置不是重规划。

### 记忆：槽位在进程里，业务在 SQLite，对话不持久

| 记忆 | 存在哪 | 挂了以后 |
|---|---|---|
| 对话原文 | `InterviewAgent` 的 `InMemoryChatMessageHistory` | 丢。咨询 Agent 连这个对象都没有 |
| 工作记忆（槽位、`awaiting_confirmation`） | 单例上的 `self.interview_history` dict | 丢 |
| 业务事实（面试记录、日程、行为） | SQLite，`InterviewDatabase.save_interview` → `InterviewService` | 还在 |
| 忙碌时段缓存 | 模块级 `interviewer_busy_periods` | 丢，和 DB 双轨 |

会话模型是全局单候选人，代码自己承认了：`api/chat_handler.py` 模块级 `global_session_id = str(uuid.uuid4())`，两个 Agent 都用它。`web/routes.py` 的 `ChatRequest` 虽有 `state` 字段，`chat_stream_endpoint` 只把 `chat.message` 传进 `ProcessUserInput_stream`。后者签名接受 `state` 和 `context`，函数体不用。两个浏览器标签抢同一份 `interview_history`。

`chats_by_session_id` 这个 dict 看起来像多会话，调用方永远只传入那一个全局 id。字典是空壳。

### 失败：保留槽位，不重规划，也没有回合预算

- 落库失败：`booking_failed = True`，`run_stream` 不 `reset()`，下一句用户输入可以再试。这是重试，不是改计划。
- 没有空闲面试官：`create_interview_failure_message` 说完就结束。不会自动改日期、改时长、再调一次 `find_interviewer`。`CandidateBehaviorAgent._find_available_times` 会扫未来两天的整点，但只服务于提醒接口，不服务于预约失败。
- 分类或路由抛错：`ClassificationProcessor` 调用 `force_reset()`，状态回到分类，已收集槽位不一定还在（面试侧 unrelated 路径特意不 `reset()` 历史；异常路径会把状态清掉，历史仍挂在单例上，两边不一致）。
- 无 `max_turns` / `max_iterations`（天气 `AgentExecutor` 用的是库默认值，主对话没有）。
- unrelated 转交没有深度限制。`InterviewAgent` 发现 `unrelated` 就把状态改回 `CLASSIFY`，调用 `unrelated_callback`。这个回调是 `TaskClassificationAgent.handle_unrelated`，它再次 `process_task_stream(同一句用户输入)`。分类器若仍输出 `interview`，会再次进入 `run_stream`。同一轮请求可以递归，没有回合计数器能砍断它。

---

## 3. 打分

**作为垂直业务 Agent：6 / 10。**  
预约这条业务链是闭合的：抽槽、缺项追问、指定面试官冲突则推荐并等待确认、事务写面试记录和日程、失败留槽。测试能指到这些分支。扣分扣在控制面：策略是 FSM，确认是关键词，全局单 session，失败不改计划。

**作为通用 Agent 系统：2 / 10。**  
可复用的部分只有「一个枚举 + 一个路由器 + 一次旁路 AgentExecutor」。没有动作空间、没有隔离的子 Agent、没有持久对话、没有预算和回放。给 2 分是因为天气旁路和 `find_interviewer` 合同证明作者知道工具环长什么样，只是没把它放到主任务上。再高就是替 README 说话。

---

## 4. 哪些「不算 Agent」是抬杠，哪些是准确的

### 抬杠或过严

- 「里面没有模型，就是 CRUD。」错。`TaskClassifier.classify_task`、`InputParser.parse_stream`、咨询 RAG、`generate_personalized_reminder`、天气 `AgentExecutor` 都在调 LLM。管理页能在没配模型时打开，是懒加载，不是没模型。
- 「用了状态机就自动失去 Agent 资格。」过严。任务型对话（状态跟踪 + 策略）在文献里就叫 conversational agent。本仓的问题不是「有 FSM」，而是 **动作选择权全部在 Python**，模型的输出被限制成 JSON 字段。
- 「`InMemoryChatMessageHistory` 等于完全没有记忆。」过严半截。工作记忆是 `interview_history`，长期记忆是 SQLite 里的面试记录和候选人行为。准确说法：对话轨迹不落盘，进程重启不能续聊，也不能按用户隔离。
- 「一次 `AgentExecutor` 都没有，所以连 tool-using 的边都沾不上。」不准确。沾上了，但是在预约成功之后、只为线下面试的天气句子。用它证明「主系统是 ReAct」才是夸大；用「完全没有」否定它，也是没读到 `interview_processor.py` 第 19–25 行和第 382–398 行。

### 准确

- 「主循环不是 Agent，是预约流程脚本。」准确。见第 2 节 `run_stream` 的五步。
- 「多 Agent 是 if/else 路由。」准确。`route_by_state` 两个分支，共享一个枚举。第四个「Agent」不在路由图上。
- 「MCP 是假的。」准确。类名 `WeatherMCPTool`，传输是 OpenWeather HTTP，依赖 `mcp` 未被 import。
- 「失败不会重规划。」准确。保留槽位供用户改口再跑同一条管线；不会自己换时段再查。
- 「不是 Claude Code 那类 harness。」准确。对照 (E) 那一行，每一项都是缺。
- 「`[THOUGHT]` 是思考过程。」不准确，这是 UI 文案。谁把这个当可解释性卖点，谁在虚报。

一句话收口：**说「这不是通用 Agent / 不是多 Agent 运行时」是事实；说「这连对话系统都不是、一文不值」是抬杠。** 它是一个能把面试约上去的 FSM 机器人，LLM 是它的眼睛和嘴，不是它的手。

---

## 5. 若要更像真 Agent（仍只做面试预约，最多 5 条）

1. **把 `find_interviewer` 接进有界工具环，并保留现在的 Python 回退。** 每轮模型只能在三个动作里选：`ask_user`、`find_interviewer`、`finish`。参数用已有的 `FIND_INTERVIEWER_SCHEMA` / `_validate_arguments` 校验，非法就拒绝并让模型重填，最多 3 次。工具结果（`available` / `unavailable` / `no_match` / `invalid_time`）必须回到模型或一个显式策略函数，再决定下一动作。抽槽 JSON 可以留下，但它不再是唯一控制通道。回退路径就是今天的 `run_stream`，模型超时或连续非法参数时走它。

2. **会话从全局变量改成请求级。** `ChatRequest` 增加 `session_id`（现有的 `state` 字段请真正传进处理函数，或者删掉）。`interview_history`、`InMemoryChatMessageHistory`、`SharedState` 放进以 session 为键的字典，禁止再使用模块级 `global_session_id`。槽位快照写入 SQLite，进程重启能按 session 恢复到「缺哪个槽 / 是否在等确认」。补一个测试：两个 session 交错发消息，槽位互不覆盖。

3. **失败时做一步域内重规划，而不是念失败文案。** `no_match` 或 `conflict` 且没有替代面试官时，调用一个新工具 `search_alternative_slots`（未来两个工作日、按轮次时长，扫描逻辑可以搬 `CandidateBehaviorAgent._find_available_times`），把最多两个改期选项写回 observation，再问用户选哪个。选中之后才第二次 `find_interviewer`。不要上「反思 Agent」。这一步就是本领域的 replan。

4. **拆掉伪 MCP，别用天气旁路冒充主 Agent。** `WeatherMCPTool` 改名为 `OpenWeatherTool`（或直接删掉这条旁路）。从 `requirements.txt` 移除未被 import 的 `mcp` 和 `langchain-experimental`。若保留天气，文档和代码注释写明：它在落库成功之后运行，不属于预约策略。README 里那两篇不存在的 spec，要么提交真实文件，要么删链接。悬空的「后训练 Planner」比没有 Planner 更伤。

5. **给状态机和转交加上预算，让非法路径失败。** `set_state` 必须先过 `can_transition_to`，非法转换抛错而不是 `print`。`pay` / `statistics` 要么做出对应处理，要么从 `TaskClassifier` 提示词和 `valid_categories` 删除。unrelated 回调加深度参数，深度 ≥ 1 时只走 `UnrelatedHandler` 的固定回复，禁止再次 `route_to_interview`。主对话设 `max_turns`（建议 12）：到点输出已收集槽位并停止，而不是无限追问。`[THOUGHT]` 改成日志，聊天流里只留 `[REPLY]`。

做完 1 和 3，才有资格说「垂直 ReAct，动作空间只有预约」。做完 2 和 5，才有资格说「这个 Agent 能多用户跑」。做完 4，面试时才不会死在名词上。五条都不做，它就继续是 (D)，请继续用第 1 节那句简历。
