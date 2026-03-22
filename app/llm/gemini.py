import json
import os
from google import genai
from google.genai import types
from app.llm.base import LLMProvider, TradeContext, Decision
from app.llm.prompt import SYSTEM_PROMPT, build_user_message

class GeminiProvider(LLMProvider):
    def __init__(self, model: str = "gemini-2.0-flash"):
        self.model = model
        self.client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", "placeholder"))

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def decide(self, context: TradeContext) -> Decision:
        system = SYSTEM_PROMPT.format(max_position_pct=context.max_position_pct)
        response = await self.client.aio.models.generate_content(
            model=self.model,
            contents=build_user_message(context),
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=256,
            ),
        )
        raw = response.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        return Decision(
            action=data["action"],
            quantity=float(data.get("quantity", 0)),
            reasoning=data["reasoning"],
            confidence=float(data.get("confidence", 0.5)),
        )
