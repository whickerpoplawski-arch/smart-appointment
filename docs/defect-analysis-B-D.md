# 缺陷定位：B（症状追问弱）与 D（改约日期跳跃）

仓库：whickerpoplawski-arch/smart-appointment  
基准：`cursor/doctor-polish-rung3-2026` @ `a7736e971867e6d8cd8e61c03ffdff129cc46724`（PR #7 tip，本文件提交前未改代码）  
评测对照：`docs/live-doctor-eval-results.md` 与 2026-10-27 live 记录（A–E 契约 5/5，体验有洞）

本文件只记录在当前树上核对过的原因。产品边界仍是 specialty_only：不建症状→科室关键词表，不诊断、不开药。

## B — 只说症状时追问科室，但句子不可操作

**现象（live）**  
用户：「我咳嗽两天，有点头疼，想预约就诊。」  
助手：「请问要挂哪个科室？比如内科、外科或儿科。」  
契约 PASS（含「科室」、未落库）。体验弱：三科举例，没有完整科室菜单，也没有和拒诊句对齐的「不能判断症状」口径。

**核对**

1. 科室表只认用户说出的名字，注释写明不做症状映射。`services/appointment_service.py:17-29` 的 `DEPARTMENT_CHOICES` 共 10 个：内科、外科、骨科、儿科、皮肤科、眼科、耳鼻喉科、口腔科、妇产科、中医科。
2. 短句在 `agents/appointment/message_builder.py:27`：`请问要挂哪个科室？比如内科、外科或儿科。` `question_for`（同文件 `:35-37`）原样返回这句。
3. 完整菜单另有一条，live 没有走到。`specialty_menu_question`（`:57-61`）文档写「只列出科室名，不按症状推荐科室」，正文是 `请先选择科室：` 加上全部 `DEPARTMENT_CHOICES`。旧流程 `create_appointment_failure_message` 在 `needs_specialty` 时用这句，并追加「我不会根据症状分诊」（`:183-186`）。
4. 工具环两条出口不一样：
   - `ask_user` 与 finish 缺槽都走短句。`booking_tool_loop.py:238-241` 在缺槽时忽略模型 `message`，调用 `question_for`；`question_blocking_finish`（`:102-107`）同样。
   - `no_match` 且 `needs_specialty` 才走完整菜单（`:181-182`）。
5. 模型提示允许两条路（`:377-378`）：`find_doctor` 且 specialty 为 null，或 `ask_user` 且 message 含「科室」。live 回复与 `:27` 逐字相同，与 `:61` 的「请先选择科室：内科、外科、骨科、…」不同。因此 live B 走的是 `ask_user` → `question_for("specialty")`，不是 `needs_specialty` 菜单。
6. 拒诊固定句在 `agents/consultant/consultation_processor.py:16-25`：`我不能判断症状或开具用药。请先选择科室，我可以帮您预约医生档期。` 科室追问没有复用这句。

**对材料的一处修正**  
材料把 `needs_specialty` 和短句写成同一次 live 出口。代码里它们是两条句子。现象只可能来自短句那条。`needs_specialty` 已经会列出 10 个科室，但仍不带拒诊口径，且不是这次 live 的那句。

**地板（specialty_only）**  
两条出口共用一句：拒诊口径 + 全部 `DEPARTMENT_CHOICES` + 「请您自己选定，不会按症状推荐科室」。不把咳嗽/头疼映射成内科，不落库，未选科室不得 finish。

## D — 忙时选项从墙钟起扫，不用请求的开始时间

**现象（live）**  
用户指定陈安 **2026-11-20 15:00**（该日为周五；种子已占用）。  
助手选项：1. **2026-10-28 09:00**；2. 2026-10-28 10:00。  
契约 PASS（未订回 15:00，选 1 后 booking_id=4）。日期从 11-20 跳到 10-28，回复未提请求日。

**核对**

1. `agents/appointment/alternative_slots.py:53-74` `slots_for_history` 取 `moment = now or time_config.now()`（`:60`），再交给 `search_alternative_slots`（`:74`）。`history["start_time"]` 未读取。`time_config.now()`（`config/time_config.py:18-23`）是北京墙钟。
2. `search_alternative_slots`（`:27-49`）用 `iter_business_days(now)`（`:14-24`）从该时刻所在日期起取 **2** 个工作日，整点 9–17，跳过 `start <= now`，最多 **2** 个选项。上限在文件顶部 `MAX_ALTERNATIVE_SLOTS = 2`、`BUSINESS_DAY_COUNT = 2`，与控制面一致，本次不放宽。
3. 调用链把 history 传进来了，但锚点仍是墙钟：`agents/appointment_agent.py:127-129` → `slots_for_history(finder, history)`。
4. 选项文案 `reply_for_slots`（`booking_tool_loop.py:412-416`）只写「这个时段约不上。可以改到：…」，没有请求日期。
5. 2026-10-27 是周二，2026-10-28 是周三。10-28 09:00/10:00 正好是「从墙钟起的下两个可约整点」：墙钟落在 2026-10-27 18:00（含）之后，或已经是 2026-10-28 09:00 之前。评测日记的是 America/Martinique 的 2026-10-27；北京时间比它早 12 小时，所以「评测日 10-27、选项 10-28」与墙钟扫描相容。根因不是时区算错一天，而是根本没用 `2026-11-20 15:00`。

**地板**  
`start_time` 能解析时，搜索下界取请求时刻与墙钟中较晚的那个：同日只给请求时刻之后的整点，不够再取随后的工作日，仍最多 2 个、窗口仍是 2 个工作日。解析失败才退回墙钟。文案写明「相对你要的 YYYY-MM-DD」。不得把忙点本身放进选项。

## 非目标

- 不做 HIS，不做症状→科室关键词表，不放宽六态、三次非法回退、`MAX_TURNS`、两个工作日两个选项。
- 不改咨询拒答的用户可见全文，只让科室追问复用其中「不能判断症状 / 请先选择科室」那一句。
