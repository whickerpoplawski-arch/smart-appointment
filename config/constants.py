from enum import Enum
# { doctor_id: [ {"start": "...", "end": "..."} ] }
# 内存中的医生忙碌时段缓存，供预约流程快速判断，数据库为最终事实来源
doctor_busy_periods = {}

class StateEnum(Enum):
    CLASSIFY = "classify"
    APPOINTMENT = "appointment"
    CONSULT = "consult"
    OTHER = "other"

class SharedState:
    def __init__(self):
        self.value = StateEnum.CLASSIFY


# 页面页脚、咨询系统提示和预约成功文案共用这一句
DEMO_DISCLAIMER = (
    "本系统是预约演示，只帮助选择科室、就诊类型和医生档期，不提供诊断、处方或急救建议。"
    "紧急情况请前往线下医疗机构或呼叫急救电话。"
)

# 咨询拒答与科室追问共用这一句，避免两边各写一套「不能判断症状」。
SPECIALTY_CHOICE_LIMIT = (
    "我不能判断症状或开具用药。请先选择科室，我可以帮您预约医生档期。"
)
