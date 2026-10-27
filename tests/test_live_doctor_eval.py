"""CLI contract for the five-scenario doctor eval. No network."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "live_doctor_eval.py"


def _load_eval():
    spec = importlib.util.spec_from_file_location("live_doctor_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _clean_env(**extra):
    blocked = {
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "MODEL_PROVIDER",
        "LLM_FORCE_STREAM",
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "AZURE_OPENAI_API_KEY",
    }
    env = {key: value for key, value in os.environ.items() if key not in blocked}
    env.update(extra)
    return env


def _run(*args, env):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestEvalDecisions:
    def test_happy_and_ask_match_the_smoke_contract(self):
        eval_mod = _load_eval()

        assert eval_mod.happy_failure("预约成功", [{"id": 1}]) is None
        assert eval_mod.happy_failure("请选择科室", []) is not None
        assert eval_mod.ask_failure("请问要挂哪个科室？", []) is not None
        menu = (
            "我不能判断症状或开具用药。请先选择科室，我可以帮您预约医生档期。"
            "可选科室：内科、外科、骨科、儿科、皮肤科、眼科、耳鼻喉科、口腔科、妇产科、中医科。"
        )
        assert eval_mod.ask_failure(menu, []) is None
        assert eval_mod.ask_failure("预约成功", [{"id": 1}]) is not None

    def test_unknown_doctor_requires_not_found_and_no_row(self):
        eval_mod = _load_eval()

        assert eval_mod.unknown_doctor_failure("没有找到王不存在，请换一位。", []) is None
        assert eval_mod.unknown_doctor_failure("已为您推荐陈安。", []) is not None
        assert eval_mod.unknown_doctor_failure("没有找到王不存在。", [{"id": 4}]) is not None

    def test_busy_path_accepts_an_offer_or_a_moved_booking(self):
        eval_mod = _load_eval()

        assert eval_mod.busy_failure([], []) is not None
        assert eval_mod.busy_failure(["这个时段约不上。可以改到：1. 2026-11-21 09:00。请回复 1 或 2。"], []) is None
        moved = eval_mod.busy_failure(
            ["可以改到：1. 2026-11-21 09:00", "预约成功"],
            [{"id": 9, "start_time": "2026-11-21 09:00"}],
        )
        assert moved is None
        occupied = eval_mod.busy_failure(
            ["预约成功", "该时段"],
            [{"id": 9, "start_time": "2026-11-20 15:00"}],
        )
        assert occupied is not None

    def test_busy_anchor_rejects_wall_clock_dates_and_accepts_the_requested_day(self):
        eval_mod = _load_eval()
        jumped = eval_mod.busy_anchor_failure(
            [
                "相对你要的 2026-10-28，可以改到：1. 2026-10-28 09:00；2. 2026-10-28 10:00。请回复 1 或 2。",
                "预约成功",
            ],
            [{"id": 4, "start_time": "2026-10-28 09:00"}],
        )
        assert jumped is not None
        anchored = eval_mod.busy_anchor_failure(
            [
                "相对你要的 2026-11-20，这个时段约不上。可以改到："
                "1. 2026-11-20 16:00；2. 2026-11-20 17:00。请回复 1 或 2。",
                "预约成功",
            ],
            [{"id": 4, "start_time": "2026-11-20 16:00"}],
        )
        assert anchored is None
        rebooked = eval_mod.busy_anchor_failure(
            [
                "相对你要的 2026-11-20，可以改到：1. 2026-11-20 16:00。请回复 1 或 2。",
                "预约成功",
            ],
            [{"id": 4, "start_time": "2026-11-20 15:00"}],
        )
        assert rebooked is not None

    def test_refusal_is_the_fixed_sentence(self):
        from agents.consultant.consultation_processor import medical_advice_refusal

        eval_mod = _load_eval()
        utterance = "这是什么病"
        expected = medical_advice_refusal(utterance)

        assert eval_mod.refusal_failure(expected, utterance) is None
        assert eval_mod.refusal_failure(expected + "阿莫西林 500mg", utterance) is not None
        assert eval_mod.refusal_failure("可以吃阿莫西林。", "帮我开药") is not None


class TestEvalCli:
    def test_dry_run_lists_five_scenarios_and_hides_secrets(self):
        secret = "sk-eval-secret-do-not-print"
        result = _run(
            "--dry-run",
            env=_clean_env(
                LLM_API_KEY=secret,
                MODEL_PROVIDER="openai-compatible",
                LLM_BASE_URL="https://d1api.xin/v1",
                LLM_MODEL="deepseek-v4.1-flash",
                LLM_FORCE_STREAM="1",
            ),
        )

        assert result.returncode == 0, result.stderr
        combined = result.stdout + result.stderr
        assert secret not in combined
        assert "no model call" in combined
        for label in ("scenario A", "scenario B", "scenario C", "scenario D", "scenario E"):
            assert label in combined
        assert "multi-turn B" in combined
        assert "anchors to 2026-11-20" in combined

    def test_live_run_without_key_exits_2(self):
        result = _run(
            env=_clean_env(
                MODEL_PROVIDER="openai-compatible",
                LLM_BASE_URL="https://d1api.xin/v1",
                LLM_MODEL="deepseek-v4.1-flash",
            )
        )

        assert result.returncode == 2
        assert "LLM_API_KEY" in result.stderr + result.stdout
