import json
import os

from anthropic import AsyncAnthropic

from app.llm.base import Decision, LLMProvider, TradeContext
from app.llm.prompt import SYSTEM_PROMPT, build_user_message


class ClaudeProvider(LLMProvider):
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.model = model
        self.client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def provider_name(self) -> str:
        return "claude"

    async def decide(self, context: TradeContext) -> Decision:
        system = SYSTEM_PROMPT.format(max_position_pct=context.max_position_pct)
        message = await self.client.messages.create(
            model=self.model,
            max_tokens=256,
            system=system,
            messages=[{"role": "user", "content": build_user_message(context)}],
        )
        raw = message.content[0].text.strip()
        data = json.loads(raw)
        return Decision(
            action=data["action"],
            quantity=float(data.get("quantity", 0)),
            reasoning=data["reasoning"],
            confidence=float(data.get("confidence", 0.5)),
        )
