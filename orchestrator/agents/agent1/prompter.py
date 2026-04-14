"""
agents/agent1/prompter.py — Prompter subagent.

For each step in a Plan, generates an optimized prompt adapted to the
target model's style. Uses Gemini to write the prompts.

Usage:
    prompter = PrompterSubagent(bus)
    prompts = await prompter.generate_prompts(plan, context_per_step)
"""
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import asyncio

from orchestrator.core.bus import MessageBus
from orchestrator.agents.agent1.planner import Plan, PlanStep

_log = logging.getLogger("orchestrator.prompter")

# Prompt style guidance per model family
PROMPT_STYLES: dict[str, str] = {
    "claude":    "Use XML tags for structure (<task>, <context>, <instructions>). Be explicit about chain-of-thought. Use detailed step-by-step instructions.",
    "gemini":    "Use structured markdown sections (## Task, ## Context, ## Instructions). Verbose context is fine. Numbered steps preferred.",
    "gpt":       "Use markdown. Direct imperative mood. Include concrete examples. No unnecessary preamble.",
    "deepseek":  "Code-first. Minimal prose. Precise spec with types and edge cases. Include function signature.",
}


def _model_family(model: str) -> str:
    model = model.lower()
    if "claude" in model:
        return "claude"
    if "gemini" in model:
        return "gemini"
    if "gpt" in model or "openai" in model:
        return "gpt"
    if "deepseek" in model or "codestral" in model:
        return "deepseek"
    return "claude"  # safe default


class PrompterSubagent:
    AGENT_ID = "agent1_prompter"

    def __init__(self, bus: MessageBus):
        self._bus = bus

    async def generate_prompts(
        self,
        plan: Plan,
        context_per_step: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Generate an optimized prompt for each plan step.
        Returns {step_id: prompt_string}.
        """
        context_per_step = context_per_step or {}
        prompts: dict[str, str] = {}

        for step in plan.steps:
            ctx = context_per_step.get(step.step_id, "")
            prompt = await self._generate_for_step(step, ctx)
            prompts[step.step_id] = prompt
            _log.debug("prompter: step=%s model=%s chars=%d",
                       step.step_id, step.recommended_model, len(prompt))

        return prompts

    async def _generate_for_step(self, step: PlanStep, context: str) -> str:
        family = _model_family(step.recommended_model)
        style = PROMPT_STYLES[family]

        meta_prompt = (
            f"You are writing a prompt that will be sent to the model '{step.recommended_model}'.\n"
            f"Style guide for this model family: {style}\n\n"
            f"Task step to accomplish:\n{step.description}\n"
        )
        if context:
            meta_prompt += f"\nRelevant context (include what's useful):\n{context}\n"
        meta_prompt += (
            f"\nTask type: {step.task_type}\n\n"
            "Write a complete, optimized prompt for the target model. "
            "Return only the prompt text, nothing else."
        )

        result = await asyncio.to_thread(self._call_claude_text, meta_prompt)
        if result:
            return result

        # Fallback: simple direct prompt
        _log.warning("Prompter: Claude unavailable, using direct prompt for step %s", step.step_id)
        return f"{step.description}\n\n{context}".strip()

    def _call_claude_text(self, prompt: str) -> str | None:
        try:
            import anthropic
            from orchestrator.core.config import ANTHROPIC_API_KEY
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            _log.error("Prompter Claude error: %s", e)
            return None
