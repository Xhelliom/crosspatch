"""Client OpenRouter. Zone mutable, mais le budget est vérifié par le kernel."""
from __future__ import annotations

import json
import os
import httpx

BASE = "https://openrouter.ai/api/v1"


class Budget(Exception):
    """Levée quand le plafond est atteint. Jamais rattrapée dans la boucle."""


class LLM:
    def __init__(self, model: str, archive, max_usd: float):
        self.model = model
        self.archive = archive
        self.max_usd = max_usd
        self.client = httpx.Client(
            base_url=BASE,
            timeout=180,
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "X-Title": "crosspatch",
            },
        )

    def chat(self, messages: list[dict], gen_id: int | None = None,
             temperature: float = 0.7, json_mode: bool = False) -> str:
        if self.archive.spent() >= self.max_usd:
            raise Budget(f"plafond atteint : {self.max_usd} USD")

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "usage": {"include": True},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        r = self.client.post("/chat/completions", json=payload)
        r.raise_for_status()
        data = r.json()

        cost = (data.get("usage") or {}).get("cost", 0.0)
        self.archive.charge(gen_id, "llm", float(cost))
        return data["choices"][0]["message"]["content"]

    def chat_json(self, messages: list[dict], gen_id: int | None = None) -> dict:
        raw = self.chat(messages, gen_id, temperature=0.4, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Un modèle moyen rate parfois le JSON. Un seul retry, puis on
            # marque la génération failed — c'est une donnée, pas un bug.
            fixed = self.chat(
                messages + [{"role": "assistant", "content": raw},
                            {"role": "user", "content": "JSON strict uniquement."}],
                gen_id, temperature=0.0, json_mode=True)
            return json.loads(fixed)
