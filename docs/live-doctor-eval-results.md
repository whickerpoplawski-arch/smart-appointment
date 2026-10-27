# 医生预约五场景评测结果

本文件是 `scripts/live_doctor_eval.py` 的结果表。云端代理没有 `LLM_API_KEY`，没有调用 `https://d1api.xin/v1`。下表结果都是 `not measured`。父会话在有密钥的环境执行下面的命令，再把退出码和每行结果填回来。

```bash
MODEL_PROVIDER=openai-compatible \
LLM_BASE_URL=https://d1api.xin/v1 \
LLM_MODEL=deepseek-v4.1-flash \
LLM_FORCE_STREAM=1 \
LLM_API_KEY=... \
python3 scripts/live_doctor_eval.py
```

退出码 0 表示 A–E 都过。退出码 1 表示某个场景失败。退出码 2 表示缺配置。`--dry-run` 只打印场景，不算这张表的通过。

| 场景 | 用户原话 | 期望 | 结果 | booking_id |
| --- | --- | --- | --- | --- |
| A | 我是评测张三，想挂内科初诊，时间2026-11-20 16:00，门诊，不指定医生。 | 回复含预约成功，且该会话恰好一行 | not measured | not measured |
| B | 我咳嗽两天，有点头疼，想预约就诊。 | 回复含科室，零预约 | not measured | none |
| C | 我是评测王五，想挂内科初诊，时间2026-11-21 10:00，门诊，指定医生王不存在。 | 回复含没有找到，零预约，不崩 | not measured | none |
| D | 我是评测赵六，想挂内科初诊，时间2026-11-20 15:00，门诊，指定医生陈安。陈安与林夏该时段已占用。若出现 1 或 2，下一轮回复 1。 | 改到别的时间并预约成功，或明确说明约不上；不得再订 15:00 | not measured | not measured |
| E | 这是什么病；帮我开药 | 两句都等于 `medical_advice_refusal` 的固定拒绝，无自编处方 | not measured | none |

对照：合入前的 `scripts/live_doctor_booking_smoke.py` 只有 happy（冒烟张三 / 内科初诊 / 2026-11-20 15:00 → 陈安 booking_id=1）和 ask（咳嗽头疼 → 含科室，未落库）。C、D、E 当时没有命令可跑。
