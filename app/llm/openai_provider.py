import json
import os
from openai import AsyncOpenAI
from app.llm.base import LLMProvider, TradeContext, Decision
from app.llm.prompt import SYSTEM_PROMPT, build_user_message

class OpenAIProvider(LLMProvider):
    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", "sk-placeholder"))

    @property
    def provider_name(self) -> str:
        return "openai"

    async def decide(self, context: TradeContext) -> Decision:
        system = SYSTEM_PROMPT.format(max_position_pct=context.max_position_pct)
        response = await self.client.chat.completions.create(
            model=self.model,
            max_tokens=256,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": build_user_message(context)},
            ],
        )
        raw = response.choices[0].message.content.strip()
        data = json.loads(raw)
        return Decision(
            action=data["action"],
            quantity=float(data.get("quantity", 0)),
            reasoning=data["reasoning"],
            confidence=float(data.get("confidence", 0.5)),
        )
