"""CLI contract for the live interview booking smoke. No network."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "live_interview_booking_smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("live_interview_booking_smoke", SCRIPT)
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


class TestScenarioDecisions:
    def test_happy_path_requires_success_text_and_one_booking_id(self):
        smoke = _load_smoke()

        assert smoke.happy_failure("预约成功！", [{"id": 7}]) is None
        assert smoke.happy_failure("请问应聘的岗位是什么？", []) is not None
        assert smoke.happy_failure("预约成功！", []) is not None

    def test_ask_path_requires_position_question_and_no_booking(self):
        smoke = _load_smoke()
        asked = "请问候选人的姓名是？ 请问应聘的岗位是什么？ 请问这是第几轮面试？"

        assert smoke.ask_failure(asked, []) is None
        assert smoke.ask_failure("预约成功！", [{"id": 3}]) is not None
        assert smoke.ask_failure("好的，已记下。", []) is not None


class TestSmokeCli:
    def test_help_exits_zero_without_a_key(self):
        result = _run("--help", env=_clean_env())

        assert result.returncode == 0
        assert "dry-run" in result.stdout

    def test_dry_run_exits_zero_and_hides_secrets(self):
        secret = "sk-live-secret-do-not-print"
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
        assert "岗位" in combined

    def test_live_run_without_key_exits_nonzero(self):
        result = _run(
            env=_clean_env(
                MODEL_PROVIDER="openai-compatible",
                LLM_BASE_URL="https://d1api.xin/v1",
                LLM_MODEL="deepseek-v4.1-flash",
            )
        )

        assert result.returncode != 0
        assert "LLM_API_KEY" in result.stderr + result.stdout
