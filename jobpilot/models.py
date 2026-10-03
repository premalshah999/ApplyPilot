"""MiMo clients share one transport so browser and answer calls share hard budgets."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .db import Budget, Run


class BudgetExceeded(RuntimeError):
    pass


class MeteredTransport(httpx.AsyncBaseTransport):
    def __init__(self, db, config, run_id=None, inner=None):
        self.db, self.config, self.run_id = db, config, run_id
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        body = await request.aread()
        args = json.loads(body or b"{}")
        if "/chat/completions" not in request.url.path:
            return await self.inner.handle_async_request(request)
        # JSON mode is documented by MiMo. Validate schemas in Python, not via unsupported strict mode.
        if args.get("response_format", {}).get("type") == "json_schema":
            args["response_format"] = {"type": "json_object"}
        args.pop("reasoning_effort", None)
        args.pop("frequency_penalty", None)
        # Thinking is on by default for MiMo v2.6 and shares the completion budget with the JSON.
        if self.config.mimo_thinking in {"enabled", "disabled"}:
            args.setdefault("thinking", {"type": self.config.mimo_thinking})
        max_tokens = args.pop("max_tokens", 2400)
        args.setdefault("max_completion_tokens", max_tokens)
        body = json.dumps(args).encode()
        headers = dict(request.headers)
        headers.pop("content-length", None)
        request = httpx.Request(
            request.method, request.url, headers=headers, content=body, extensions=request.extensions
        )
        # Conservative upper reservation: serialized bytes >= usual token count, including screenshots.
        reserve = (
            len(body) * self.config.mimo_input_price
            + args["max_completion_tokens"] * self.config.mimo_output_price
        ) / 1_000_000
        day = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        with self.db.exclusive() as s:
            budget = s.get(Budget, day)
            if not budget:
                budget = Budget(day=day, spent=0, submissions=0)
                s.add(budget)
            run = s.get(Run, self.run_id) if self.run_id else None
            if budget.spent + reserve > self.config.daily_budget_usd:
                raise BudgetExceeded("Daily model budget reached")
            if run and run.model_calls >= self.config.max_model_calls:
                raise BudgetExceeded("Application model-call limit reached")
            budget.spent += reserve
            if run:
                run.cost += reserve
                run.model_calls += 1
        # On transport failure retain the reservation conservatively: billing outcome is unknown.
        response = await self.inner.handle_async_request(request)
        content = await response.aread()
        try:
            usage = json.loads(content).get("usage", {})
            actual = (
                usage["prompt_tokens"] * self.config.mimo_input_price
                + usage["completion_tokens"] * self.config.mimo_output_price
            ) / 1_000_000
        except (ValueError, KeyError, TypeError):
            actual = reserve
        with self.db.exclusive() as s:
            budget = s.get(Budget, day)
            budget.spent = max(0, budget.spent + actual - reserve)
            if self.run_id and (run := s.get(Run, self.run_id)):
                run.cost = max(0, run.cost + actual - reserve)
        return response

    async def aclose(self):
        await self.inner.aclose()


def http_client(db, config, run_id=None):
    return httpx.AsyncClient(transport=MeteredTransport(db, config, run_id), timeout=25)


async def structured(config, db, output_type, instructions, prompt, run_id=None, max_tokens=2400):
    if not config.mimo_api_key:
        raise RuntimeError("MiMo is not configured. Add MIMO_API_KEY to .env and restart.")
    from openai import AsyncOpenAI
    from pydantic_ai import Agent, PromptedOutput
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    async with http_client(db, config, run_id) as client:
        openai = AsyncOpenAI(
            api_key=config.mimo_api_key, base_url=config.mimo_base_url, http_client=client, max_retries=0
        )
        model = OpenAIChatModel(config.mimo_model, provider=OpenAIProvider(openai_client=openai))
        agent = Agent(
            model,
            output_type=PromptedOutput(output_type),
            instructions=instructions,
            retries=1,
            model_settings={"temperature": 0.1, "max_tokens": max_tokens, "timeout": 25},
        )
        result = await agent.run(prompt)
        return result.output
