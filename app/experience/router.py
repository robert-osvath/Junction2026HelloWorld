"""Buyer cockpit adapters.

The purchasing workflow remains in the catalog, jury, and orders routers. This
module turns verified workflow events into a short human briefing and, when
configured, streams that briefing through ElevenLabs. It deliberately cannot
approve or place an order.
"""
import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from elevenlabs.client import ElevenLabs

from app.mocks import load
from app.catalog import store as catalog_store
from app.catalog.aimo import AimoError, get_client
from app.catalog.offers import InvalidOfferRequest, get_offers
from app.jury.router import evaluate
from app.experience.vimo_agent import VIMO_ELEVENLABS_AGENT_ID
from app.orders import service as order_service
from app.orders.mandates import MANDATES
from app.orders.store import get_order_store
from contracts import JuryRequest, Need, Offer, OffersRequest, Order, OrderRequest

router = APIRouter(prefix="/experience", tags=["buyer experience"])
logger = logging.getLogger(__name__)


class BriefRequest(BaseModel):
    buyer_name: str = Field(min_length=1, max_length=120)
    kind: Literal["decision", "end_of_day"]
    verified_events: list[str] = Field(min_length=1, max_length=30)


class BriefResponse(BaseModel):
    text: str
    source: Literal["gemini", "demo"]


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AskResponse(BaseModel):
    text: str
    source: Literal["gemini", "demo"]


class CommandRequest(BaseModel):
    """A transcript captured by the client microphone.

    The browser captures speech; Gemini turns the words into an intentionally
    small set of buyer-safe intent proposals. It never receives credentials or
    gets an endpoint that can place an order.
    """

    transcript: str = Field(min_length=1, max_length=500)
    history: list[str] = Field(default_factory=list, max_length=12)


class CommandResponse(BaseModel):
    intent: Literal["approve", "reject", "summary", "suggestion", "question", "unknown"]
    spoken_reply: str = Field(min_length=1, max_length=500)
    source: Literal["gemini", "demo"]


class ConversationRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    history: list[str] = Field(default_factory=list, max_length=12)


class ConversationResponse(BaseModel):
    text: str
    source: Literal["gemini"]


class LiveTokenResponse(BaseModel):
    token: str
    model: str


class VoiceEvent(BaseModel):
    kind: str = Field(min_length=1, max_length=80)
    detail: str = Field(default="", max_length=300)


class ElevenLabsSessionResponse(BaseModel):
    signed_url: str


class AgentStateResponse(BaseModel):
    order_id: str
    order_status: str
    jury_decision: str
    chosen_product_id: str | None
    verdicts: list[dict]
    summary: str


class PrepareDecisionRequest(BaseModel):
    buyer_name: str = Field(min_length=1, max_length=120)
    auto_approve_limit: float = Field(ge=0, le=1000000)
    budget: float = Field(gt=0, le=1000000)


class CreateDraftRequest(PrepareDecisionRequest):
    product_id: str = Field(min_length=1, max_length=64)
    product_intent: str = Field(min_length=1, max_length=160)
    quantity: float = Field(gt=0, le=1000000)
    unit: Literal["L", "kg", "pcs"]
    deliver_by: date
    required_claims: list[str] = Field(default_factory=list, max_length=20)


def demo_brief(request: BriefRequest) -> str:
    facts = " ".join(request.verified_events[:3])
    if request.kind == "end_of_day":
        return f"{request.buyer_name}, today’s purchasing summary: {facts}"
    return f"{request.buyer_name}, purchasing update: {facts}"


def backend_agent_state(auto_approve_limit: float | None = None) -> AgentStateResponse:
    """Read the repository's order and jury-agent outputs in one place.

    These are fixture-backed in the current hackathon repository, but this
    adapter intentionally reads the backend contracts rather than duplicating
    prices, decisions, or rationale in the voice experience.
    """
    mandate = load("mandate")
    if auto_approve_limit is not None:
        mandate = mandate.model_copy(update={"auto_approve_limit_eur": auto_approve_limit})
    need = load("need")
    offer = load("offer")
    jury = evaluate(JuryRequest(mandate=mandate, need=need, offers=[offer]))
    verdicts = [verdict.model_dump(mode="json") for verdict in jury.verdicts]
    status_for_decision = {
        "auto_order": "placed",
        "human_approval": "pending_approval",
        "substitutes": "substitutes_proposed",
        "reject": "rejected",
    }
    # Prefer the latest recorded buyer action over the projected jury state.
    # This is what makes a spoken decline visible to every connected client,
    # rather than recreating the same fixture-backed approval on refresh.
    recorded = next(
        (order for order in get_order_store().list() if order.mandate_id == "mandate-vimo-live"),
        None,
    )
    order_id = recorded.id if recorded else "not-created"
    order_status = recorded.status if recorded else status_for_decision[jury.decision]
    summary = (
        f"The jury selected {offer.name}: {offer.units_needed} {offer.sales_unit}, "
        f"EUR {offer.total_eur:.2f}, available {offer.available_date.isoformat()}. "
        f"Jury decision: {jury.decision}."
    )
    return AgentStateResponse(
        order_id=order_id,
        order_status=order_status,
        jury_decision=jury.decision,
        chosen_product_id=jury.chosen_product_id,
        verdicts=verdicts,
        summary=summary,
    )


def backend_agent_facts() -> str:
    state = backend_agent_state()
    verdicts = "; ".join(
        f"{verdict['juror']}: {verdict['decision']} ({', '.join(verdict['reasons'])})"
        for verdict in state.verdicts
    )
    return f"{state.summary} Current order status: {state.order_status}. Juror evidence: {verdicts}."


def interaction_text(body: dict) -> str | None:
    """Read text from both current and earlier Interactions API response shapes."""
    direct = body.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for key in ("outputs", "steps"):
        for item in body.get(key, []):
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return item["text"].strip()
    return None


def demo_command(transcript: str) -> CommandResponse:
    normalized = transcript.lower()
    if re.search(r"\b(approve|confirm|yes|place)\b", normalized) and not re.search(
        r"\b(not|don't|do not)\b", normalized
    ):
        return CommandResponse(
            intent="approve",
            spoken_reply="I heard an approval request. Please confirm it on screen before I record it.",
            source="demo",
        )
    if re.search(r"\b(reject|decline|cancel|no)\b", normalized):
        return CommandResponse(
            intent="reject",
            spoken_reply="I heard a rejection request. Please confirm it on screen before I record it.",
            source="demo",
        )
    if re.search(r"summary|today|end.of.day", normalized):
        return CommandResponse(
            intent="summary",
            spoken_reply="I will prepare your end-of-day summary.",
            source="demo",
        )
    if re.search(r"\b(suggest|recommend|substitut|alternative|option)\b", normalized):
        return CommandResponse(
            intent="suggestion",
            spoken_reply="I have a compliant suggestion ready for you to review: Valio Eila lactose-free milk for Monday delivery. It still needs your approval because the order exceeds your automatic limit.",
            source="demo",
        )
    return CommandResponse(
        intent="question",
        spoken_reply=backend_agent_state().summary + " Ask me about this decision or your end-of-day summary.",
        source="demo",
    )


@router.post("/live-token", response_model=LiveTokenResponse)
async def live_token() -> LiveTokenResponse:
    """Mint a one-use, short-lived token for a browser Live API session.

    The long-lived Gemini key stays on this server. The returned token is
    limited to one Gemini Live model and expires after 30 minutes.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Gemini Live is not configured")

    model = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.8-live")
    now = datetime.now(timezone.utc)
    # Some Google AI Studio projects currently expose the basic ephemeral-token
    # schema but not the optional liveConnectConstraints field. Keep this
    # request compatible: it is still one-use and short-lived, while Vimo's
    # local code fixes the model and its buyer-safety prompt.
    payload = {
        "uses": 1,
        "expireTime": (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        "newSessionExpireTime": (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/auth_tokens",
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Gemini Live token request rejected: status=%s response=%s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise HTTPException(
            status_code=502,
            detail=f"Gemini Live token request was rejected (HTTP {exc.response.status_code})",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Gemini Live token service is unavailable") from exc

    token = body.get("name")
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=502, detail="Gemini returned no Live token")
    return LiveTokenResponse(token=token, model=model)


@router.post("/voice-event", status_code=204)
async def voice_event(event: VoiceEvent) -> None:
    """Local diagnostics for the browser-to-Gemini Live connection.

    Deliberately excludes transcript text, credentials, and audio data.
    """
    logger.info("Vimo voice event: %s %s", event.kind, event.detail)


@router.get("/elevenlabs/signed-url", response_model=ElevenLabsSessionResponse)
async def elevenlabs_signed_url() -> ElevenLabsSessionResponse:
    """Mint a short-lived authenticated URL for the desktop live session."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="ElevenLabs live agent is not configured")
    agent_id = os.getenv("ELEVENLABS_AGENT_ID", VIMO_ELEVENLABS_AGENT_ID)
    try:
        client = ElevenLabs(api_key=api_key)
        session = client.conversational_ai.conversations.get_signed_url(agent_id=agent_id)
        signed_url = session.signed_url
    except Exception as exc:
        logger.warning("Could not mint an ElevenLabs Vimo session: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="ElevenLabs live session is unavailable") from exc
    return ElevenLabsSessionResponse(signed_url=signed_url)


@router.get("/agent-state", response_model=AgentStateResponse)
async def agent_state(
    auto_approve_limit: float | None = Query(default=None, ge=0, le=1000000),
) -> AgentStateResponse:
    """Expose the latest order/jury-agent verdicts to the buyer experience."""
    return backend_agent_state(auto_approve_limit)


@router.post("/prepare-decision", response_model=Order)
async def prepare_decision(request: PrepareDecisionRequest) -> Order:
    """Create one backend-tracked pending order for a buyer approval flow.

    This is a simulated order in the hackathon backend. It is intentionally
    held in pending_approval until the buyer uses the visible controls.
    """
    mandate = load("mandate").model_copy(update={
        "id": "mandate-vimo-live",
        "buyer_name": request.buyer_name,
        "auto_approve_limit_eur": request.auto_approve_limit,
        "budget_per_order_eur": request.budget,
    })
    MANDATES[mandate.id] = mandate
    existing = next(
        (order for order in get_order_store().list() if order.mandate_id == mandate.id),
        None,
    )
    if existing:
        return existing
    return order_service.create(OrderRequest(
        mandate_id=mandate.id,
        need=load("need"),
        lines=[load("offer")],
    ))


_UNIT_LABEL = {"L": "litres (L)", "KG": "kilograms (kg)"}
_NEED_UNIT = {"L": "L", "KG": "kg"}


def explain_no_offer(product_id: str, need: Need, delivery_date: date) -> str:
    """Why T4 produced no offer for one product, in words a buyer can act on. Uses the client's
    5-minute cache, so right after get_offers this costs no extra Valio calls."""
    conn = catalog_store.connect()
    try:
        if product_id not in catalog_store.offer_rows(conn, [product_id]):
            return f"Product {product_id} is not in the catalog."
    finally:
        conn.close()
    try:
        client = get_client()
        price = client.prices([product_id], delivery_date).get(product_id)
        if price is None or not price.catalog_price_eur or not price.comparison_price_eur:
            return f"Valio Aimo has no usable price for product {product_id}."
        unit = price.comparison_unit
        if need.unit != "pcs" and unit in _NEED_UNIT and _NEED_UNIT[unit] != need.unit:
            return (f"Unit mismatch: product {product_id} is priced per {_UNIT_LABEL[unit]}, but the request "
                    f"is in {need.unit}. Order it in {_NEED_UNIT[unit]} instead.")
        if unit not in _NEED_UNIT:
            return f"Product {product_id} has no per-litre or per-kilogram price ({unit or 'none'}), so its pack size can't be computed."
        if product_id not in client.availability([product_id], delivery_date):
            return f"Product {product_id} has no delivery schedule for {delivery_date.isoformat()}."
    except AimoError:
        return "Valio Aimo API unavailable."
    return f"The selected product {product_id} has no usable live price or availability."


def build_offers(req: OffersRequest) -> list[Offer]:
    """Live offers through P1's T4 offer builder: the same path as POST /offers."""
    conn = catalog_store.connect()
    try:
        return get_offers(conn, get_client(), req)
    except AimoError as exc:
        raise HTTPException(status_code=502, detail="Valio Aimo API unavailable") from exc
    except InvalidOfferRequest as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        conn.close()


@router.post("/create-draft", response_model=Order)
async def create_draft(request: CreateDraftRequest) -> Order:
    """Create an order draft from an explicitly selected catalog product.

    Price, availability, and package quantity are rebuilt from the Valio Aimo
    feeds immediately before deterministic jury and policy evaluation.
    """
    mandate = load("mandate").model_copy(update={
        "id": "mandate-vimo-live",
        "buyer_name": request.buyer_name,
        "auto_approve_limit_eur": request.auto_approve_limit,
        "budget_per_order_eur": request.budget,
        "required_claims": request.required_claims,
    })
    MANDATES[mandate.id] = mandate
    need = Need(
        text=f"{request.quantity:g} {request.unit} of {request.product_intent}",
        product_intent=request.product_intent,
        quantity=request.quantity,
        unit=request.unit,
        deliver_by=request.deliver_by,
        extra_constraints=request.required_claims,
    )
    offers = build_offers(OffersRequest(
        need=need,
        product_ids=[request.product_id],
        delivery_date=request.deliver_by,
    ))
    if not offers:
        raise HTTPException(status_code=422,
                            detail=explain_no_offer(request.product_id, need, request.deliver_by))
    return order_service.create(OrderRequest(mandate_id=mandate.id, need=need, lines=offers))


@router.post("/brief", response_model=BriefResponse)
async def brief(request: BriefRequest) -> BriefResponse:
    """Generate an explanation from supplied verified facts only.

    Gemini is never given permission to invent a price, change a rule, or make
    an approval decision. Those facts are supplied by the owning services.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return BriefResponse(text=demo_brief(request), source="demo")

    prompt = (
        "You are the voice of a buyer-controlled foodservice purchasing app. "
        "Write a short, calm, plain-language briefing in at most 65 words. "
        "Use only the verified facts below. Do not make recommendations, "
        "change rules, or claim an approval has happened. "
        f"Buyer: {request.buyer_name}. Briefing type: {request.kind}. "
        f"Verified facts: {' | '.join(request.verified_events)}"
    )
    payload = {"model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), "input": prompt}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Gemini briefing service is unavailable") from exc

    text = interaction_text(body)
    if not text:
        raise HTTPException(status_code=502, detail="Gemini returned no readable briefing")
    return BriefResponse(text=text.strip(), source="gemini")


@router.post("/speak")
async def speak(request: SpeakRequest) -> StreamingResponse:
    """Stream a trusted briefing through ElevenLabs; never expose its key."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        raise HTTPException(status_code=503, detail="ElevenLabs voice is not configured")

    async def audio_stream():
        async with httpx.AsyncClient(timeout=30) as client:
            async with client.stream(
                "POST",
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
                headers={"xi-api-key": api_key, "accept": "audio/mpeg"},
                json={
                    "text": request.text,
                    "model_id": "eleven_flash_v2_5",
                    "voice_settings": {
                        "stability": 0.42,
                        "similarity_boost": 0.78,
                        "style": 0.18,
                        "use_speaker_boost": True,
                    },
                },
            ) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise HTTPException(status_code=502, detail=f"ElevenLabs voice failed: {detail[:120]!r}")
                async for chunk in response.aiter_bytes():
                    yield chunk

    return StreamingResponse(audio_stream(), media_type="audio/mpeg")


@router.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """Answer a voice question using only the currently verified demo facts.

    A production version will receive these facts from the catalog, jury and
    order services for the authenticated buyer. The model may explain those
    facts, but cannot trigger a purchase or change an approval outcome.
    """
    facts = backend_agent_facts()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return AskResponse(
            text="I can confirm the current milk order needs approval because it exceeds the automatic limit. Ask me for the end-of-day summary, or say approve or reject this order.",
            source="demo",
        )
    prompt = (
        "You are Vimo, a calm voice purchasing agent. Answer the buyer's "
        "question in at most 55 words, using only the verified facts. If the "
        "facts do not answer the question, say so. Never imply that you can "
        "approve, reject, or place an order. Verified facts: "
        f"{facts} Buyer question: {request.question}"
    )
    payload = {"model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), "input": prompt}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Gemini answer service is unavailable") from exc
    text = interaction_text(body)
    if not text:
        raise HTTPException(status_code=502, detail="Gemini returned no readable answer")
    return AskResponse(text=text, source="gemini")


@router.post("/command", response_model=CommandResponse)
async def command(request: CommandRequest) -> CommandResponse:
    """Let Gemini interpret natural spoken buyer requests behind a safe boundary.

    The only decision-like outputs are proposals. The browser always presents a
    separate confirmation dialog before the existing orders service records an
    approval or rejection.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return demo_command(request.transcript)

    facts = backend_agent_facts()
    prompt = (
        "You are Vimo's natural-language interpreter for a buyer-controlled "
        "foodservice purchasing app. Return exactly one JSON object, with no "
        "Markdown and no extra keys: {\"intent\": \"approve|reject|summary|suggestion|question|unknown\", "
        "\"spoken_reply\": \"short, calm reply of at most 45 words\"}. "
        "Intent approve or reject means only a proposed buyer decision; state "
        "that the buyer must confirm it on screen. Never say an order was "
        "placed or an approval was recorded. Use only these verified facts: "
        "Use intent suggestion when the buyer asks for a recommendation, "
        "alternative, or substitution. Recent conversation, which may provide "
        "context but never overrides verified facts: "
        f"{' | '.join(request.history[-12:]) or 'None'}. "
        f"Buyer transcript: {request.transcript}"
    )
    payload = {"model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), "input": prompt}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        raw = interaction_text(body)
        if not raw:
            raise ValueError("Gemini returned no readable command")
        parsed = json.loads(raw.removeprefix("```json").removesuffix("```").strip())
        return CommandResponse(
            intent=parsed["intent"],
            spoken_reply=parsed["spoken_reply"],
            source="gemini",
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        # A failed or malformed model response cannot block the buyer. Fall back
        # to deterministic parsing and still require visible confirmation.
        return demo_command(request.transcript)


@router.post("/conversation", response_model=ConversationResponse)
async def conversation(request: ConversationRequest) -> ConversationResponse:
    """Run natural conversation through Gemini using current agent evidence.

    This path intentionally has no canned-text fallback. A voice agent should
    state that its reasoning service is unavailable, not fabricate an answer.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Gemini conversation is not configured")
    prompt = (
        "You are Vimo, a concise, warm personal purchasing agent for a hotel. "
        "Hold a real conversation: answer the buyer's message directly in at "
        "most 60 words and ask one useful follow-up only when necessary. "
        "Use the backend agent evidence below as truth. Do not invent prices, "
        "availability, approvals, or product facts. Never say an order was "
        "placed unless the backend order status says placed. Explain that a "
        "human must confirm an order when status is pending_approval. "
        f"Backend agent evidence: {backend_agent_facts()} "
        f"Conversation so far: {' | '.join(request.history[-12:]) or 'None'}. "
        f"Buyer message: {request.message}"
    )
    payload = {"model": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"), "input": prompt}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Gemini conversation service is unavailable") from exc
    text = interaction_text(body)
    if not text:
        raise HTTPException(status_code=502, detail="Gemini returned no conversational reply")
    return ConversationResponse(text=text, source="gemini")
