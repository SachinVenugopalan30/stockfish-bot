import json

import httpx

from app.llm.base import Decision, LLMProvider, TradeContext
from app.llm.prompt import SYSTEM_PROMPT, build_user_message


class OllamaProvider(LLMProvider):
    def __init__(self, model: str = "llama3", host: str = "http://localhost:11434"):
        self.model = model
        self.host = host.rstrip("/")

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def single_shot(self, system: str, user_message: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(f"{self.host}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"].strip()

    async def decide(self, context: TradeContext) -> Decision:
        system = SYSTEM_PROMPT.format(max_position_pct=context.max_position_pct)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": build_user_message(context)},
            ],
            "stream": False,
            "format": "json",
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{self.host}/api/chat", json=payload)
            response.raise_for_status()
            raw = response.json()["message"]["content"].strip()
        data = json.loads(raw)
        return Decision(
            action=data["action"],
            quantity=float(data.get("quantity", 0)),
            reasoning=data["reasoning"],
            confidence=float(data.get("confidence", 0.5)),
        )
