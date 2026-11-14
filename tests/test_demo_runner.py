from dataclasses import dataclass

from scripts.demo_runner import HAPPY_REQUEST, STOCK_OUT_REQUEST, run_demo


@dataclass
class Result:
    status: str
    order_id: str
    substitutes: list[dict]


class FakeAgent:
    async def run(self, request, profile_key="hotel", *, today=None):
        if profile_key == "hotel_tight":
            return Result("pending_approval", "order-over", [])
        if request == STOCK_OUT_REQUEST:
            return Result("substitutes_proposed", "order-stock", [{"product_id": "replacement"}])
        assert request == HAPPY_REQUEST
        return Result("placed", "order-happy", [])


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Test double unexpectedly required an event loop")


def test_three_demo_scenarios_and_audit_are_asserted():
    approved = []

    async def approve(order_id):
        approved.append(order_id)
        return {"status": "placed"}

    async def verify():
        return {"ok": True}

    outcomes = run_immediate(run_demo(FakeAgent(), approve, verify))

    assert [(o.scenario, o.status) for o in outcomes] == [
        ("happy", "placed"), ("stock-out", "substitutes_proposed"), ("over-budget", "placed"),
    ]
    assert approved == ["order-over"]
