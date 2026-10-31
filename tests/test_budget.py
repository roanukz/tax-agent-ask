"""sim.budget.BudgetedClient: what a call that fails costs, offline with fake clients."""

import anthropic
import httpx2
import pytest

from ledger.spend import cost_usd, load_prices
from sim import budget
from sim.budget import BudgetedClient
from sim.fakes import FakeUsage, ScriptedClient

PRICES = load_prices()
HAIKU = "claude-haiku-5-5"
REQUEST = {"model": HAIKU, "max_tokens": 1024, "messages": [{"role": "user", "content": "x" * 3000}]}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("fake", response=httpx2.Response(code, request=request), body=None)


def raises(error: BaseException):
    def reply(kwargs):
        raise error

    return reply


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    status_error(anthropic.RateLimitError, 429),
    status_error(anthropic.InternalServerError, 500),
    status_error(anthropic.BadRequestError, 400),
], ids=["429", "500", "400"])
async def test_a_call_the_api_answers_with_an_error_status_costs_nothing(error):
    inner = ScriptedClient([raises(error), '{"answer": "ok"}'], usage=FakeUsage(input_tokens=600, output_tokens=40))
    guard = BudgetedClient(inner, model=HAIKU, prices=PRICES, limit_usd=1.0)
    with pytest.raises(type(error)):
        await guard.messages.create(**REQUEST)
    assert guard.spent_usd == 0.0 and guard.unknown_usd == 0.0 and guard.in_flight_usd == pytest.approx(0)
    await guard.messages.create(**REQUEST)
    assert guard.spent_usd == cost_usd(HAIKU, {"input_tokens": 600, "output_tokens": 40}, PRICES), "only the billed call"


@pytest.mark.anyio
@pytest.mark.parametrize("error", [
    anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
    TimeoutError("cut off"),
], ids=["timeout", "connection", "canceled-or-other"])
async def test_a_call_whose_usage_is_unknown_still_counts_at_its_bound(error):
    guard = BudgetedClient(ScriptedClient([raises(error)]), model=HAIKU, prices=PRICES, limit_usd=1.0)
    with pytest.raises(type(error)):
        await guard.messages.create(**REQUEST)
    assert guard.spent_usd == pytest.approx(budget.call_bound_usd(REQUEST, HAIKU, PRICES), abs=1e-6)
