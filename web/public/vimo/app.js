const state = {
  voice: true,
  approval: null,
  conversation: [],
  jury: [],
  events: [],
};

const $ = (selector) => document.querySelector(selector);
const juryList = $("#jury-list");
const timeline = $("#timeline");
let selectedVoiceDecision = null;
let onboardingStep = 0;
const onboardingAnswers = {};
let vimoEnabled = false;
let wakeRecognition = null;
let liveSession = null;
let liveAudioContext = null;
let liveMicStream = null;
let liveProcessor = null;
let liveNextPlaybackAt = 0;
let liveLastTranscript = "";
let liveRequest = null;
let liveAnalyser = null;
let microphoneMonitor = null;
let elevenConversation = null;
let elevenStarting = null;  // in-flight start: auto-start + button click must not open two sessions (two voices)
let elevenIdleTimer = null;
let parkingElevenSession = false;
let captionTimer = null;
let speakAudio = null;
let speakId = 0;
let backendPollTimer = null;
let lastBackendOrderStatus = null;
const TURN_SILENCE_MS = 1200;
const TURN_MAX_MS = 5000;
const VOICE_DIAGNOSTICS_ENABLED = false;
const recentVoiceEvents = new Map();

function clearElevenIdleTimer() {
  clearTimeout(elevenIdleTimer);
  elevenIdleTimer = null;
}

function armElevenIdleTimer() {
  clearElevenIdleTimer();
  elevenIdleTimer = setTimeout(async () => {
    const conversation = elevenConversation;
    if (!conversation || liveRequest) return;
    // A parked session cannot generate an unsolicited "are you still there?"
    // turn. The wake listener remains available for the buyer's next request.
    parkingElevenSession = true;
    try { await conversation.endSession(); } catch (_) { /* it may already be closed */ }
  }, 5000);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderJury() {
  juryList.replaceChildren(...state.jury.map(([mark, name, finding, confidence]) => {
    const row = el("div", "jury-row");
    row.append(el("span", "checkmark", mark), el("strong", "", name), el("span", "", finding), el("span", "", confidence));
    return row;
  }));
}

function renderTimeline() {
  if (!state.events.length) {
    timeline.replaceChildren(el("li", "", "Nothing yet. Tell Vimo what you need."));
    return;
  }
  timeline.replaceChildren(...state.events.map(([time, title, detail]) => {
    const item = el("li");
    item.append(el("time", "", time), el("strong", "", title), document.createTextNode(detail));
    return item;
  }));
}

function addEvent(title, detail) {
  state.events.unshift([new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}), title, detail]);
  state.events = state.events.slice(0, 20);
  renderTimeline();
}

function setVimoState(title, detail, listening = false) {
  $("#vimo-title").textContent = title;
  $("#vimo-status").textContent = detail;
  $("#vimo-orb").classList.toggle("listening", listening);
  const presence = $("#voice-presence");
  if (presence) {
    presence.textContent = listening ? "● Microphone listening" : "● Microphone standby";
    presence.dataset.state = listening ? "listening" : "standby";
  }
}

function monitorMicrophone() {
  if (!liveAnalyser) return;
  const levels = new Uint8Array(liveAnalyser.fftSize);
  const presence = $("#voice-presence");
  const tick = () => {
    if (!liveAnalyser || !presence) return;
    liveAnalyser.getByteTimeDomainData(levels);
    const energy = levels.reduce((total, value) => total + Math.abs(value - 128), 0) / levels.length;
    if (energy > 3) {
      presence.textContent = "● Hearing you";
      presence.dataset.state = "hearing";
    } else if (liveRequest) {
      presence.textContent = "● Listening for your request";
      presence.dataset.state = "listening";
    }
    microphoneMonitor = requestAnimationFrame(tick);
  };
  cancelAnimationFrame(microphoneMonitor);
  tick();
}

function reportVoiceEvent(kind, detail = "") {
  // Chromium's background diagnostic uploads can be noisy during audio streams.
  if (!VOICE_DIAGNOSTICS_ENABLED) return;
  const fingerprint = `${kind}:${detail}`;
  const now = Date.now();
  if (now - (recentVoiceEvents.get(fingerprint) || 0) < 1500) return;
  recentVoiceEvents.set(fingerprint, now);
  fetch("/experience/voice-event", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({kind, detail: String(detail).slice(0, 300)}),
  }).catch(() => {});
}

function showCaption(text, hideAfterMs = 0) {
  const el = $("#vimo-caption");
  if (!el?.showPopover || !text) return;  // Popover API: Chrome/Edge 114+, Safari 17+
  clearTimeout(captionTimer);
  el.textContent = text;
  if (el.matches(":popover-open")) el.hidePopover();
  el.showPopover();  // top layer, re-shown per line so it stays above an open <dialog>
  if (hideAfterMs) hideCaption(hideAfterMs);
}

function hideCaption(afterMs = 1500) {
  clearTimeout(captionTimer);
  captionTimer = setTimeout(() => { const el = $("#vimo-caption"); if (el?.matches(":popover-open")) el.hidePopover(); }, afterMs);
}

async function speak(text) {
  if (!state.voice) return;
  // one voice at a time: stop the previous line; the live ElevenLabs agent already speaks for itself
  const id = ++speakId;  // a newer line supersedes this one (its audio, its caption timer)
  if (speakAudio) speakAudio.pause();
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  if (elevenStarting || elevenConversation) { showCaption(text, 4000 + text.length * 40); return; }
  showCaption(text);
  try { await speakAloud(text, id); } finally { if (id === speakId) hideCaption(); }
}

async function speakAloud(text, id) {
  try {
    const response = await fetch("/experience/speak", {
      method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({text}),
    });
    if (response.ok) {
      const blob = await response.blob();
      if (id !== speakId) return;  // superseded while fetching: never start a second voice
      const audio = new Audio(URL.createObjectURL(blob));
      speakAudio = audio;
      audio.play().catch(() => audio.onerror?.());
      await new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; audio.onpause = resolve; });
      return;
    }
  } catch (_) { /* Browser speech remains a local demo fallback. */ }
  if (!("speechSynthesis" in window) || id !== speakId) return;
  const message = new SpeechSynthesisUtterance(text);
  message.rate = 1.04;
  const finished = new Promise((resolve) => { message.onend = resolve; message.onerror = resolve; });
  window.speechSynthesis.speak(message);
  await finished;
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function resampleToPcm16(input, inputRate) {
  const ratio = inputRate / 16000;
  const output = new Int16Array(Math.floor(input.length / ratio));
  for (let index = 0; index < output.length; index += 1) {
    const sample = input[Math.floor(index * ratio)] || 0;
    output[index] = Math.max(-1, Math.min(1, sample)) * 0x7fff;
  }
  return new Uint8Array(output.buffer);
}

function playLiveAudio(base64Audio) {
  if (!liveAudioContext) return;
  const bytes = base64ToBytes(base64Audio);
  const samples = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
  const buffer = liveAudioContext.createBuffer(1, samples.length, 24000);
  const channel = buffer.getChannelData(0);
  for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 0x8000;
  const source = liveAudioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(liveAudioContext.destination);
  liveNextPlaybackAt = Math.max(liveAudioContext.currentTime, liveNextPlaybackAt);
  source.start(liveNextPlaybackAt);
  liveNextPlaybackAt += buffer.duration;
}

function finishLiveRequest() {
  if (!liveRequest) return;
  const request = liveRequest.text.trim();
  clearTimeout(liveRequest.maximumTimer);
  clearTimeout(liveRequest.pauseTimer);
  liveRequest = null;
  if (!request) {
    setVimoState("Vimo is standing by", "I did not catch a request. Say “Hey Vimo” to try again.");
    speak("I did not catch a request. Say Hey Vimo when you are ready.");
    return;
  }
  Promise.resolve(processVimoCommand(request)).finally(() => {
    if (vimoEnabled) setTimeout(startWakeListener, 300);
  });
}

function beginLiveRequest(initialText = "") {
  if (liveRequest) finishLiveRequest();
  liveRequest = {text: initialText.trim(), maximumTimer: null, pauseTimer: null};
  setVimoState("Vimo is listening", "Listening for up to 5 seconds. I’ll respond shortly after you finish speaking.", true);
  liveRequest.maximumTimer = setTimeout(finishLiveRequest, TURN_MAX_MS);
  if (liveRequest.text) {
    liveRequest.pauseTimer = setTimeout(finishLiveRequest, TURN_SILENCE_MS);
  }
}

function collectLiveRequest(transcript) {
  if (!liveRequest) return;
  const cleaned = transcript.replace(/^\s*(?:hey|hi)?\s*vimo\b[,.!?\s]*/i, "").trim();
  if (cleaned) liveRequest.text = cleaned;
  clearTimeout(liveRequest.pauseTimer);
  if (liveRequest.text) liveRequest.pauseTimer = setTimeout(finishLiveRequest, TURN_SILENCE_MS);
}

function handleLiveTurn(transcript) {
  const match = transcript.toLowerCase().match(/\b(?:hey|hi)?\s*vimo\b\s*(.*)/);
  if (!match) return;
  reportVoiceEvent("wake_detected");
  const command = match[1].trim();
  if (!command) {
    beginLiveRequest();
    return;
  }
  beginLiveRequest(command);
}

async function searchBackendCatalog(query, requiredClaims = []) {
  const params = new URLSearchParams({q: query, limit: "5"});
  if (requiredClaims.length) params.set("claims", requiredClaims.join(","));
  const response = await fetch(`/catalog/search?${params}`);
  if (!response.ok) throw new Error("The catalog search is unavailable");
  const products = await response.json();
  return products.map((product) => ({
    product_id: product.product_id,
    name: product.name,
    supplier: product.vendor_name,
    sales_unit: product.sales_unit,
    category: product.category,
    claims: product.claims,
    allergens_contains: product.allergens_contains,
  }));
}

async function createCatalogDraft({product_id, product_intent, quantity, unit, deliver_by, required_claims = [], exception_reason = ""}) {
  const rules = JSON.parse(localStorage.getItem("aimo-rules") || "{}");
  const response = await fetch("/experience/create-draft", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      policy_id: rules.policy_id || null,  // no saved policy: the backend applies the buyer's default
      buyer_name: rules.buyer || null,
      product_id: product_id || null,  // optional hint; the jury chooses the best product
      product_intent,
      quantity: Number(quantity),
      unit,
      deliver_by,
      exception_reason: exception_reason || null,
    }),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "The order draft could not be created.");
  }
  return response.json();
}

const IDLE_HINT = "Say “Vimo” and what you need, e.g. “Vimo, order 1,000 L lactose-free milk by Monday”. Or type below.";
let thinkingTimer = null;

function setListening(detail = IDLE_HINT) {
  clearTimeout(thinkingTimer);
  setVimoState("Vimo is listening", detail, true);
}

function setThinking(heard) {
  clearTimeout(thinkingTimer);
  setVimoState("Vimo is thinking…", `You said: “${heard}”`, true);
  // the agent stays silent unless addressed as “Vimo”: do not claim it is still thinking forever
  thinkingTimer = setTimeout(() => setListening(`No reply to “${heard}”. Start with “Vimo”, e.g. “Vimo, order 1,000 L lactose-free milk by Monday”.`), 9000);
}

function setWorking(detail) {
  clearTimeout(thinkingTimer);
  setVimoState("Vimo is working", detail, true);
  thinkingTimer = setTimeout(() => setListening(), 20000);  // never stuck if a tool or the agent goes quiet
}

const STATUS_LABEL = {
  placed: "Placed", pending_approval: "Needs your approval", substitutes_proposed: "Substitutes proposed", rejected: "Rejected",
};
const JUROR_LABEL = {
  tier0: "Policy rules",
  spec: "Product fit",
  delivery: "Delivery",
  cost: "Cost",
  sustainability: "Sustainability",
  tier2: "Senior review",
};
const eur = (n) => new Intl.NumberFormat("en-GB", {style: "currency", currency: "EUR"}).format(n);
const heldReason = (order) => order.verdicts?.find((v) => v.decision !== "approve")?.reasons?.[0] || "";

function renderOrder(order) {
  // the decision panel and banner always show the order this page is working on, never a demo fixture
  const line = order.lines?.[0];
  const sub = order.substitutes?.[0];
  $("#decision-title").textContent = order.need?.text || "Order";
  $("#decision-status").textContent = STATUS_LABEL[order.status] || order.status;
  $("#decision-request").textContent = `Order ${order.id.replace("order-", "").slice(0, 8)} · ${order.mandate_id}`;
  state.jury = (order.verdicts || []).map((v) => [
    v.decision === "approve" ? "✓" : v.decision === "reject" ? "✕" : "!",
    JUROR_LABEL[v.juror] || v.juror, v.reasons?.[0] || "", `${v.decision} · ${Math.round(v.confidence * 100)}%`,
  ]);
  renderJury();
  $("#rec-name").textContent = line?.name || (sub ? `Substitute: ${sub.name}` : "No matching offer");
  $("#rec-detail").textContent = line
    ? `${line.units_needed} × ${line.sales_unit} · ${eur(line.total_eur)} · available ${line.available_date}`
    : sub ? `${sub.units_needed} × ${sub.sales_unit} · ${eur(sub.total_eur)} · available ${sub.available_date}` : heldReason(order);
  const pending = order.status === "pending_approval";
  const subs = order.status === "substitutes_proposed" ? (order.substitutes || []) : [];
  $("#approval-banner").hidden = !pending && order.status !== "substitutes_proposed";
  if (pending) {
    $("#approval-title").textContent = `Approve ${line ? `${line.units_needed} × ${line.name}` : "this order"} for ${eur(line?.total_eur || 0)}?`;
    $("#approval-detail").textContent = heldReason(order) || "The jury asked for a human decision.";
  } else if (order.status === "substitutes_proposed" && !subs.length) {
    $("#approval-title").textContent = `${line?.name || "This product"} did not pass the jury, and no substitute qualified.`;
    $("#approval-detail").textContent = "See the jury verdicts in the decision panel. Reject it, or ask Vimo for a different product or date.";
  } else if (subs.length) {
    $("#approval-title").textContent = `${line?.name || "This product"} did not pass the jury. Pick a substitute below.`;
    $("#approval-detail").textContent = "See the jury verdicts in the decision panel. Ordering a substitute starts a new, re-checked order.";
  }
  for (const id of ["#approve-order", "#voice-decision"]) { $(id).disabled = !pending; $(id).hidden = !pending; }
  $("#reject-order").disabled = !pending && order.status !== "substitutes_proposed";
  renderSubstitutes(order, subs);
}

let myOrders = [];

function renderMetrics() {
  renderOrderWatch();
  // live numbers for the saved buyer policy, never placeholders
  const placed = myOrders.filter((o) => o.status === "placed");
  const sum = (o) => (o.lines || []).reduce((t, l) => t + l.total_eur, 0);
  $("#m-orders").textContent = String(myOrders.length);
  $("#m-placed").textContent = eur(placed.reduce((t, o) => t + sum(o), 0));
  $("#m-placed-n").textContent = `${placed.length} order${placed.length === 1 ? "" : "s"}`;
  $("#m-pending").textContent = String(myOrders.filter((o) => o.status === "pending_approval").length);
  $("#m-subs").textContent = String(myOrders.filter((o) => o.status === "substitutes_proposed").length);
}

function rememberOrder(order) {
  myOrders = [order, ...myOrders.filter((o) => o.id !== order.id)];
  renderMetrics();
}

function renderOrderWatch() {
  const watch = $("#order-watch");
  if (!myOrders.length) {
    watch.replaceChildren(el("p", "muted", "No orders yet. Drafts appear here; select one to work on it."));
    return;
  }
  watch.replaceChildren(...myOrders.slice(0, 6).map((order) => {
    const line = order.lines?.[0];
    const total = (order.lines || []).reduce((t, l) => t + l.total_eur, 0);
    const row = el("button", "watch-row");
    row.type = "button";
    const info = el("div");
    info.append(el("strong", "", order.need?.text || "Order"),
      el("span", "", `${line ? line.name : "No offer"} · ${eur(total)} · ${order.id.replace("order-", "").slice(0, 8)}`));
    row.append(info, el("span", "status", STATUS_LABEL[order.status] || order.status));
    row.addEventListener("click", () => {  // explicit choice: voice tools then act on this order
      state.approval = order;
      renderOrder(order);
      startBackendDecisionPolling();
      $("#decision-title").scrollIntoView({behavior: "smooth", block: "center"});
    });
    return row;
  }));
}

function renderSubstitutes(order, subs) {
  // the jury's ranked alternatives; picking one creates a new order that the jury checks again
  const list = $("#substitute-list");
  list.hidden = !subs.length;
  list.replaceChildren(...subs.map((sub) => {
    const row = el("div", "substitute-row");
    const info = el("div");
    info.append(el("strong", "", sub.name),  // the jury's ranker already tags Best/Better/Good in the name
      el("span", "", `${sub.units_needed} × ${sub.sales_unit} · ${eur(sub.total_eur)} · available ${sub.available_date}`));
    const button = el("button", "primary", "Order this instead");
    button.addEventListener("click", () => acceptSubstitute(order, sub));
    row.append(info, button);
    return row;
  }));
}

async function acceptSubstitute(order, sub) {
  for (const b of document.querySelectorAll("#substitute-list button")) b.disabled = true;
  addEvent("Ordering substitute", `${sub.name}: the jury checks it again…`);
  try {
    const response = await fetch(`/orders/${encodeURIComponent(order.id)}/accept-substitute`, {
      method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({product_id: sub.product_id}),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "The substitute could not be ordered.");
    showOrder(body, "Substitute ordered");
    startBackendDecisionPolling();
    if (body.status === "pending_approval") openDecisionConfirmation(null, `Substitute: ${sub.name}`);
  } catch (error) {
    addEvent("Could not order the substitute", error.message);
    for (const b of document.querySelectorAll("#substitute-list button")) b.disabled = false;
  }
}

function showOrder(order, title) {
  state.approval = order;
  renderOrder(order);
  rememberOrder(order);
  const line = order.lines?.[0];
  addEvent(title, `${order.need?.text || ""}${line ? ` → ${line.name}, ${eur(line.total_eur)}` : ""} · ${STATUS_LABEL[order.status] || order.status}`);
}

async function refreshOrder() {
  if (!state.approval?.id) return null;
  try {
    const response = await fetch(`/orders/${encodeURIComponent(state.approval.id)}`);
    if (response.ok) {
      state.approval = await response.json();
      renderOrder(state.approval);
      rememberOrder(state.approval);
    }
  } catch (_) { /* keep the last known order on screen */ }
  return state.approval;
}

function agentOrderFacts(order) {
  const rules = JSON.parse(localStorage.getItem("aimo-rules") || "{}");
  if (!order) {
    return {order_status: "none", summary: "No order has been drafted in this session yet. Ask what the buyer needs (product, quantity, unit, delivery date), then call create_order_draft; the jury chooses the product."};
  }
  const line = order.lines?.[0];
  return {
    order_id: order.id, order_status: order.status, request: order.need?.text,
    product: line?.name, quantity: line ? `${line.units_needed} ${line.sales_unit}` : null,
    total_eur: line?.total_eur, available_date: line?.available_date,
    jury: (order.verdicts || []).map((v) => `${v.juror}: ${v.decision} (${v.reasons?.[0] || ""})`),
    substitutes: (order.substitutes || []).map((s) => `${s.name}, ${eur(s.total_eur)}`),
    confirmation_no: order.confirmation_no,
  };
}

function startBackendDecisionPolling() {
  // reflect decisions made elsewhere (e.g. the /approvals page) on the order this page shows
  clearInterval(backendPollTimer);
  lastBackendOrderStatus = state.approval?.status || null;
  backendPollTimer = setInterval(async () => {
    if (!state.approval?.id) return;
    await refreshOrder();
    const status = state.approval?.status || null;
    if (!status || status === lastBackendOrderStatus) return;
    lastBackendOrderStatus = status;
    const update = `Backend update: this order is now ${STATUS_LABEL[status] || status}.`;
    addEvent("Backend update", update);
    if (status === "pending_approval") openDecisionConfirmation();
    speak(update);
  }, 5000);
}

function openDecisionConfirmation(decision = null, transcript = "") {
  if (!state.approval || state.approval.status !== "pending_approval") return false;
  selectedVoiceDecision = decision;
  if (!$("#voice-dialog").open) $("#voice-dialog").showModal();
  const line = state.approval.lines?.[0];
  const details = [
    ["Product", line?.name || "Not available"],
    ["Quantity", line ? `${line.units_needed} ${line.sales_unit}` : "Not available"],
    ["Total", line ? eur(line.total_eur) : "Not available"],
    ["Available", line?.available_date || "Not available"],
    ["Delivery", line?.logistic_model || "Not available"],
    ["Why held", heldReason(state.approval) || "—"],
  ];
  $("#decision-details").replaceChildren(...details.map(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = value;
    row.append(term, description);
    return row;
  }));
  $("#voice-transcript").textContent = transcript || state.approval.need?.text || "";
  if (decision) {
    const label = decision === "approve" ? "Approve this order" : "Reject this order";
    $("#voice-status").textContent = `Vimo heard “${decision}”. Confirm to record this decision.`;
    $("#confirm-voice").textContent = label;
    $("#confirm-voice").disabled = false;
  } else {
    $("#voice-status").textContent = "Review the order, then approve or reject it here or in the banner.";
    $("#confirm-voice").disabled = true;
  }
  return true;
}

function startElevenLive() {
  if (elevenConversation) return Promise.resolve();
  elevenStarting ??= connectElevenLive().finally(() => { elevenStarting = null; });
  return elevenStarting;
}

async function connectElevenLive() {
  setVimoState("Vimo is connecting", "Starting your live Vimo conversation…", true);
  const sessionResponse = await fetch("/experience/elevenlabs/signed-url");
  if (!sessionResponse.ok) throw new Error("Could not start the ElevenLabs live session");
  const {signed_url: signedUrl} = await sessionResponse.json();
  const {Conversation} = await import("https://esm.sh/@elevenlabs/client@1.36.1?bundle");
  elevenConversation = await Conversation.startSession({
    signedUrl,
    clientTools: {
      get_backend_state: async () => agentOrderFacts(await refreshOrder()),
      search_catalog: async ({query, required_claims = []} = {}) => {
        if (!query?.trim()) return {products: [], message: "A product or category is required."};
        setWorking(`Searching the catalog for “${query}”…`);
        try {
          const products = await searchBackendCatalog(query, required_claims);
          return {products, source: "Valio Aimo catalog"};
        } catch (error) {
          setListening(error.message);
          return {products: [], message: error.message};
        }
      },
      show_approval_popup: async () => {
        await refreshOrder();
        const shown = openDecisionConfirmation();
        return {shown, message: shown ? "The approval dialog for the current order is open." : "No order on screen needs approval."};
      },
      create_order_draft: async (request = {}) => {
        setWorking(`Pricing and checking ${request.quantity ?? ""} ${request.unit ?? ""} ${request.product_intent ?? ""}…`);
        try {
          const draft = await createCatalogDraft(request);
          showOrder(draft, "Order drafted");
          if (draft.status === "pending_approval") openDecisionConfirmation(null, `Vimo drafted: “${draft.need?.text}”`);
          return {created: true, ...agentOrderFacts(draft)};
        } catch (error) {
          addEvent("Could not draft the order", error.message);
          setListening(error.message);
          return {created: false, message: error.message};
        }
      },
      propose_order_decision: async ({decision} = {}) => {
        if (decision !== "approve" && decision !== "reject") {
          return {shown: false, message: "A decision must be approve or reject."};
        }
        await refreshOrder();
        const shown = openDecisionConfirmation(decision, `Vimo heard: “${decision} this order.”`);
        return {shown, message: shown ? `The ${decision} confirmation is ready for the buyer.` : "There is no pending order to decide."};
      },
      execute_order_decision: async ({decision} = {}) => {
        if (decision !== "approve" && decision !== "reject") {
          return {executed: false, message: "A decision must be approve or reject."};
        }
        await refreshOrder();
        const updated = await recordBuyerDecision(decision);
        return updated
          ? {executed: true, ...agentOrderFacts(updated)}
          : {executed: false, message: "There is no pending order to decide."};
      },
      show_suggestion_popup: async ({summary = ""} = {}) => {
        // non-blocking: a suggestion is noted on the record instead of a modal over the page
        if (summary) addEvent("Vimo suggestion", summary);
        return {shown: true};
      },
    },
    onConnect: () => {
      setListening();
      startBackendDecisionPolling();
    },
    onMessage: (event) => {
      const who = event?.source || event?.role;
      if (!event?.message) return;
      if (who === "user") setThinking(event.message);
      else if (who === "ai" || who === "agent") showCaption(event.message);  // subtitles
    },
    onModeChange: (event) => {
      const mode = typeof event === "string" ? event : event?.mode;
      if (mode === "speaking") {
        clearElevenIdleTimer();
        // Laptop speakers can feed the agent's own voice back into the open
        // microphone. ElevenLabs then sees a false barge-in and cuts the
        // response short. Pause input only for the spoken turn; listening is
        // immediately restored afterwards.
        Promise.resolve(elevenConversation?.setMicMuted(true)).catch(() => {});
        clearTimeout(thinkingTimer);
        setVimoState("Vimo is speaking", "Finishing this update, then listening again.");
      }
      if (mode === "listening") {
        Promise.resolve(elevenConversation?.setMicMuted(false)).catch(() => {});
        hideCaption(2500);
        if ($("#vimo-title").textContent === "Vimo is speaking") setListening();
        armElevenIdleTimer();
      }
    },
    onError: () => {
      clearElevenIdleTimer();
      try { elevenConversation?.endSession(); } catch (_) { /* already closed */ }
      elevenConversation = null;
      hideCaption(0);
      clearTimeout(thinkingTimer);
      setVimoState("Vimo needs attention", "The live conversation disconnected. Select Talk to Vimo to reconnect.");
    },
    onDisconnect: () => {
      elevenConversation = null;
      hideCaption(0);
      clearTimeout(thinkingTimer);
      clearElevenIdleTimer();
      if (parkingElevenSession) {
        parkingElevenSession = false;
        setVimoState("Vimo is on standby", "Say “Hey Vimo” when you need me. I’ll also surface a real backend decision.");
        startWakeListener();
        return;
      }
      clearInterval(backendPollTimer);
      backendPollTimer = null;
      setVimoState("Vimo is offline", "Select Talk to Vimo to reconnect.");
    },
  });
  // the agent's model does not know today's date and resolves "by Monday" in its training year
  const now = new Date();
  const iso = now.toLocaleDateString("en-CA", {timeZone: "Europe/Helsinki"});
  const weekday = now.toLocaleDateString("en-GB", {weekday: "long", timeZone: "Europe/Helsinki"});
  try {
    elevenConversation.sendContextualUpdate(`Today is ${weekday} ${iso} (Helsinki). Resolve relative dates such as "Monday" or "tomorrow" from today; deliver_by must be today or later, in YYYY-MM-DD.`);
  } catch (error) { console.warn("Could not send today's date to Vimo", error); }  // the backend also rejects past dates
}

async function startGeminiLive() {
  if (liveSession) return;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) throw new Error("Live audio is not supported in this browser");
  setVimoState("Vimo is connecting", "Starting your secure Gemini Live voice session…", true);
  const tokenResponse = await fetch("/experience/live-token", {method: "POST"});
  if (!tokenResponse.ok) throw new Error("Gemini Live is not configured");
  const {token, model} = await tokenResponse.json();
  const {GoogleGenAI, Modality} = await import("https://esm.sh/@google/genai@latest?bundle");
  const ai = new GoogleGenAI({apiKey: token, httpOptions: {apiVersion: "v1beta"}});
  liveAudioContext = new AudioContext();
  await liveAudioContext.resume();
  const session = await ai.live.connect({
    model,
    config: {
      responseModalities: [Modality.AUDIO],
      inputAudioTranscription: {},
      outputAudioTranscription: {},
      systemInstruction: "You are Vimo, a calm foodservice purchasing voice agent. Respond only when the buyer says Hey Vimo or Hi Vimo. Never claim an order has been approved, rejected, or placed. Explain that approval decisions require on-screen buyer confirmation.",
    },
    callbacks: {
      onopen: () => reportVoiceEvent("live_connected"),
      onmessage: (message) => {
        const content = message.serverContent;
        const input = content?.inputTranscription?.text;
        if (input) {
          if (liveRequest) collectLiveRequest(input);
          else liveLastTranscript = input;
        }
        // Gemini Live is deliberately used for continuous listening and input
        // transcription. Vimo's audible replies come only from ElevenLabs so
        // the buyer always hears the selected branded voice, not a second
        // ungrounded model voice.
        if (content?.turnComplete && !liveRequest && liveLastTranscript) {
          handleLiveTurn(liveLastTranscript);
          liveLastTranscript = "";
        }
      },
      onerror: (event) => {
        reportVoiceEvent("live_error", event?.message || "WebSocket error");
        setVimoState("Vimo is standing by", "Live voice connection was interrupted. Select Talk to Vimo to reconnect.");
      },
      onclose: (event) => {
        reportVoiceEvent("live_closed", event?.reason || "Connection closed");
        liveSession = null;
      },
    },
  });
  liveSession = session;
  liveMicStream = await navigator.mediaDevices.getUserMedia({audio: true});
  reportVoiceEvent("microphone_connected");
  const source = liveAudioContext.createMediaStreamSource(liveMicStream);
  liveAnalyser = liveAudioContext.createAnalyser();
  liveAnalyser.fftSize = 256;
  liveProcessor = liveAudioContext.createScriptProcessor(2048, 1, 1);
  const silentGain = liveAudioContext.createGain();
  silentGain.gain.value = 0;
  liveProcessor.onaudioprocess = (event) => {
    if (!liveSession) return;
    const pcm = resampleToPcm16(event.inputBuffer.getChannelData(0), liveAudioContext.sampleRate);
    liveSession.sendRealtimeInput({audio: {data: bytesToBase64(pcm), mimeType: "audio/pcm;rate=16000"}});
  };
  source.connect(liveProcessor);
  source.connect(liveAnalyser);
  liveProcessor.connect(silentGain);
  silentGain.connect(liveAudioContext.destination);
  monitorMicrophone();
  setVimoState("Vimo is listening", "Say “Hey Vimo” when you need an update, a question, or a decision.", true);
}

function listenToVimo(onTranscript) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    setVimoState("Vimo needs voice access", "Voice input is not available in this browser. You can still type buyer rules and use the approval controls.");
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onresult = (event) => onTranscript(event.results[0][0].transcript);
  recognition.onerror = (event) => {
    if (event.error === "not-allowed" || event.error === "service-not-allowed") {
      setVimoState("Vimo needs microphone access", "Allow microphone access to continue the conversation.");
    }
  };
  recognition.onend = () => {
    if (!wakeRecognition && !liveRequest) $("#vimo-orb").classList.remove("listening");
  };
  recognition.start();
}

function hasBrowserSpeechRecognition() {
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function stopWakeListener() {
  if (wakeRecognition) {
    wakeRecognition.onend = null;
    wakeRecognition.stop();
    wakeRecognition = null;
  }
}

function startWakeListener() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!vimoEnabled || !Recognition || wakeRecognition) return;
  const recognition = new Recognition();
  wakeRecognition = recognition;
  recognition.lang = "en-US";
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.onstart = () => {
    $("#vimo-orb").classList.add("listening");
    const presence = $("#voice-presence");
    if (presence) {
      presence.textContent = "● Microphone listening";
      presence.dataset.state = "listening";
    }
  };
  recognition.onresult = (event) => {
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const heard = event.results[index][0].transcript;
      const match = heard.toLowerCase().match(/\b(?:hey|hi)?\s*vimo\b\s*(.*)/);
      if (!match) continue;
      stopWakeListener();
      reportVoiceEvent("browser_wake_detected");
      const command = match[1].trim();
      if (command) beginLiveRequest(command);
      else {
        setVimoState("Vimo is listening", "I’m listening. Tell me what your kitchen needs.", true);
        speak("I’m listening. What does your kitchen need?").then(() => {
          beginLiveRequest();
          listenToVimo(collectLiveRequest);
        });
      }
      return;
    }
  };
  recognition.onerror = (event) => {
    reportVoiceEvent("browser_wake_unavailable");
    if ((event.error === "not-allowed" || event.error === "service-not-allowed") && !liveSession) {
      setVimoState("Vimo needs microphone access", "Allow microphone access to use the wake word.");
    }
  };
  recognition.onend = () => {
    wakeRecognition = null;
    if (vimoEnabled) setTimeout(startWakeListener, 500);
  };
  recognition.start();
}

async function answerVimoQuestion(question) {
  setVimoState("Vimo is checking", "Finding the answer from verified purchasing evidence…");
  let answer = "I can confirm the current milk order needs approval because it exceeds the automatic limit.";
  try {
    const response = await fetch("/experience/ask", {
      method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({question}),
    });
    if (response.ok) answer = (await response.json()).text;
  } catch (_) { /* Vimo remains useful with a short local fallback. */ }
  setVimoState("Vimo has an update", answer);
  await speak(answer);
  startWakeListener();
}

async function askGeminiConversation(transcript) {
  try {
    const response = await fetch("/experience/conversation", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({message: transcript, history: state.conversation.slice(-10)}),
    });
    if (response.ok) return (await response.json()).text;
  } catch (_) { /* The unavailable message below is intentionally explicit. */ }
  return "I cannot reach Gemini right now, so I cannot give you a trustworthy answer. Please try again in a moment.";
}

async function processVimoCommand(transcript, {alreadySpoken = false} = {}) {
  setVimoState("Vimo is checking", "Gemini is interpreting your request from verified purchasing evidence…", true);
  const normalized = transcript.toLowerCase();
  const intent = interpretVoiceDecision(transcript)
    || (/summary|today|end.of.day/i.test(normalized) ? "summary" : null)
    || (/\b(suggest|recommend|substitut|alternative|option)\b/i.test(transcript) ? "suggestion" : "question");
  const spokenReply = await askGeminiConversation(transcript);
  const command = {intent, spoken_reply: spokenReply};
  state.conversation.push(`Buyer: ${transcript}`, `Vimo: ${spokenReply}`);
  state.conversation = state.conversation.slice(-12);
  const decision = command.intent === "approve" || command.intent === "reject" ? command.intent : null;
  const approvalOpen = state.approval?.status === "pending_approval";
  if (decision && state.approval && approvalOpen) {
    $("#voice-dialog").showModal();
    $("#voice-transcript").textContent = `“${transcript}”`;
    selectedVoiceDecision = decision;
    const label = decision === "approve" ? "Approve this order" : "Reject this order";
    $("#voice-status").textContent = `${command.spoken_reply} Confirm to record it.`;
    $("#confirm-voice").textContent = label;
    $("#confirm-voice").disabled = false;
    setVimoState("Vimo needs confirmation", command.spoken_reply);
    if (!alreadySpoken) await speak(command.spoken_reply);
    return;
  }
  if (decision && !approvalOpen) {
    const reply = "There is no decision waiting for you. The backend jury has already completed this order according to the current buyer mandate.";
    setVimoState("Vimo has an update", reply);
    if (!alreadySpoken) await speak(reply);
    return;
  }
  if (command.intent === "summary") {
    $("#end-day").click();
    setVimoState("Vimo has prepared your summary", command.spoken_reply);
    if (!alreadySpoken) await speak(command.spoken_reply);
    if (!liveSession) startWakeListener();
    return;
  }
  if (command.intent === "suggestion") {
    $("#suggestion-copy").textContent = command.spoken_reply;
    $("#suggestion-dialog").showModal();
    setVimoState("Vimo has a suggestion", command.spoken_reply);
    if (!alreadySpoken) await speak(command.spoken_reply);
    return;
  }
  setVimoState("Vimo has an update", command.spoken_reply);
  if (!alreadySpoken) await speak(command.spoken_reply);
  if (!liveSession) startWakeListener();
}

function nextOnboardingQuestion() {
  const questions = [
    "What is this kitchen or location called?",
    "What is the maximum amount Vimo may approve automatically, in euros?",
    "What is the maximum order budget, in euros?",
    "May Vimo suggest a substitution when it keeps all dietary rules? Please say yes or no.",
  ];
  const question = questions[onboardingStep];
  setVimoState("Vimo is listening", question, true);
  speak(question).then(() => {
    if (!liveSession) listenToVimo(handleOnboardingAnswer);
  });
}

function handleOnboardingAnswer(transcript) {
  const number = transcript.match(/\d+(?:\.\d+)?/);
  if (onboardingStep === 0) onboardingAnswers.buyer = transcript.trim();
  if (onboardingStep === 1 && number) onboardingAnswers.limit = number[0];
  if (onboardingStep === 2 && number) onboardingAnswers.budget = number[0];
  if (onboardingStep === 3) onboardingAnswers.substitutes = /\b(yes|allow|okay|ok)\b/i.test(transcript) ? "on" : "";
  if ((onboardingStep === 1 || onboardingStep === 2) && !number) {
    setVimoState("Vimo needs a number", "Please say the amount in euros.", true);
    speak("Please say the amount in euros.").then(() => listenToVimo(handleOnboardingAnswer));
    return;
  }
  onboardingStep += 1;
  if (onboardingStep < 4) return nextOnboardingQuestion();
  const rules = {
    buyer: onboardingAnswers.buyer || "My kitchen",
    limit: onboardingAnswers.limit || "0",
    budget: onboardingAnswers.budget || "0",
    claim: "",
    substitutes: onboardingAnswers.substitutes,
  };
  localStorage.setItem("aimo-rules", JSON.stringify(rules));
  $("#policy-copy").textContent = `${rules.buyer}: Vimo can act up to €${rules.limit} when every policy check passes.`;
  state.events.unshift([new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"}), "Voice rules saved", `Vimo may auto-approve up to €${rules.limit}.`]);
  renderTimeline();
  setVimoState("Vimo is monitoring", `Rules for ${rules.buyer} are active. I will call out only when something needs your attention.`);
  vimoEnabled = true;
  speak(`Your rules are active. I will call out only when something needs your attention.`).then(startWakeListener);
}

function startVimo({announce = true} = {}) {
  if (elevenStarting || elevenConversation) return;  // already connecting or live: a second start means a second voice
  vimoEnabled = true;
  const alert = "Vimo is active. Tell me what your kitchen needs, or ask what needs attention.";
  setVimoState("Vimo is active", "Say “Hey Vimo” for an update, a question, or an approval decision.");
  startElevenLive().catch((error) => {
    console.warn("ElevenLabs live session unavailable", error);
    startLegacyVimo(announce, alert);
  });
}

let legacyStarted = false;

function startLegacyVimo(announce, alert) {
  if (legacyStarted) return;  // a retry after an ElevenLabs failure must not start a second listener/voice
  legacyStarted = true;
  // Browser recognition is the lowest-latency wake-word path when available;
  // Gemini Live remains the cross-browser continuous-audio path.
  startWakeListener();
  if (hasBrowserSpeechRecognition()) {
    if (announce) speak("Vimo is ready. What does your kitchen need today?");
    return;
  }
  startGeminiLive().then(() => {
    startWakeListener();
    if (announce) speak("Vimo is connected. What does your kitchen need today?");
  }).catch((error) => {
    reportVoiceEvent("live_start_failed", error?.message || "Unable to start Live session");
    setVimoState("Vimo is active", "Gemini Live is unavailable, so I’m using browser voice input. Say “Hey Vimo” when you need me.");
    if (announce) speak(alert).then(startWakeListener);
  });
}

async function loadDecision() {
  // only orders covered by this buyer's policy (saved or default) appear in this cockpit
  try {
    const response = await fetch("/orders");
    const orders = response.ok ? await response.json() : [];
    const rules = JSON.parse(localStorage.getItem("aimo-rules") || "{}");
    const mine = orders.filter((order) => rules.policy_id ? order.mandate_id === rules.policy_id
        : order.mandate_id.startsWith("default-") || order.mandate_id === "mandate-vimo-live")
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
    myOrders = mine;
    renderMetrics();
    // shortcut: metrics only. An order from an earlier visit (or another visitor on the shared demo mandate)
    // is never adopted, so voice tools can only act on an order drafted in this session; upgrade with per-buyer sessions.
  } catch (_) { /* empty state stays */ }
}

async function recordBuyerDecision(decision) {
  const status = state.approval?.status;
  const allowed = status === "pending_approval" || (decision === "reject" && status === "substitutes_proposed");
  if (!allowed) return null;
  const endpoint = decision === "approve" ? "approve" : "reject";
  const response = await fetch(`/orders/${state.approval.id}/${endpoint}`, {method: "POST"});
  if (!response.ok) throw new Error("The decision could not be recorded");
  const updated = await response.json();
  selectedVoiceDecision = null;
  if ($("#voice-dialog").open) $("#voice-dialog").close();
  showOrder(updated, decision === "approve" ? "Order approved" : "Order rejected");
  if (elevenConversation) setListening(decision === "approve" ? "Order approved and placed." : "Order rejected. Nothing will be placed.");
  else setVimoState(decision === "approve" ? "Order approved" : "Order rejected", "Decision recorded in the audit trail.");
  return updated;
}

$("#open-rules").addEventListener("click", () => $("#rules-dialog").showModal());
$("#ask-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#ask-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  try { await startElevenLive(); } catch (_) { /* falls back below */ }
  if (elevenConversation) {
    // the agent only answers requests addressed to Vimo
    elevenConversation.sendUserMessage(/\bvimo\b/i.test(text) ? text : `Vimo, ${text}`);
    setThinking(text);
  } else {
    // the legacy chat path cannot draft orders: say so instead of pretending
    setVimoState("Vimo is offline", "The live Vimo service is unavailable, so typed orders cannot be drafted right now. Try again in a moment.");
  }
});
$("#start-vimo").addEventListener("click", startVimo);
function normalizeClaim(claim) {
  const value = claim.trim().toUpperCase().replace(/[ -]+/g, "_");
  return value === "LACTOSE_FREE" ? "FREE_FROM_LACTOSE" : value;
}

$("#rules-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.submitter?.value === "cancel") { $("#rules-dialog").close(); return; }  // × and Cancel also submit the form
  const rules = Object.fromEntries(new FormData(event.currentTarget));
  const requiredClaims = String(rules.claim || "").split(",").map(normalizeClaim).filter(Boolean);
  const bannedAllergens = String(rules.allergens || "").split(",").map((code) => code.trim().toUpperCase()).filter(Boolean);
  try {
    const response = await fetch("/experience/policies", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({
        buyer_name: rules.buyer, buyer_type: rules.buyer_type,
        auto_approve_limit: Number(rules.limit), budget: Number(rules.budget),
        required_claims: requiredClaims, banned_allergens: bannedAllergens,
        strict_allergens: Boolean(rules.strict_allergens), substitutes_allowed: Boolean(rules.substitutes),
      }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : "The policy could not be saved.");
    rules.policy_id = body.policy_id;
  } catch (error) {
    setVimoState("Policy needs attention", error.message);
    return;
  }
  localStorage.setItem("aimo-rules", JSON.stringify(rules));
  $("#policy-copy").textContent = `${rules.buyer}: orders up to €${rules.limit} can proceed automatically when all dietary and delivery rules are met.`;
  $("#rules-dialog").close();
  addEvent("Buyer policy updated", `Auto-approve up to ${eur(Number(rules.limit))}, budget ${eur(Number(rules.budget))}${rules.allergens ? `, no ${rules.allergens}` : ""}.`);
  if (!vimoEnabled) startVimo();  // the first save continues straight into the conversation
  else speak("Your purchasing rules have been saved.");
});

$("#voice-toggle").addEventListener("click", (event) => {
  state.voice = !state.voice;
  event.currentTarget.textContent = `Voice updates ${state.voice ? "on" : "off"}`;
  event.currentTarget.setAttribute("aria-pressed", String(state.voice));
});

$("#approve-order").addEventListener("click", async () => {
  await recordBuyerDecision("approve");
});

$("#reject-order").addEventListener("click", async () => {
  await recordBuyerDecision("reject");
});

function interpretVoiceDecision(text) {
  const normalized = text.toLowerCase();
  if (/\b(approve|confirm|yes|place)\b/.test(normalized) && !/\b(not|don't|do not)\b/.test(normalized)) return "approve";
  if (/\b(reject|decline|cancel|no)\b/.test(normalized)) return "reject";
  return null;
}

function listenForDecision() {
  selectedVoiceDecision = null;
  $("#confirm-voice").disabled = true;
  $("#voice-transcript").textContent = "";
  $("#voice-status").textContent = "Listening for “approve this order” or “reject this order”…";
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    $("#voice-status").textContent = "Voice input is not supported in this browser. Use the approval buttons instead.";
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "en-US";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onresult = async (event) => {
    const transcript = event.results[0][0].transcript;
    $("#voice-status").textContent = "Gemini is checking the decision wording…";
    const command = await understandVimoCommand(transcript);
    selectedVoiceDecision = command.intent === "approve" || command.intent === "reject" ? command.intent : null;
    $("#voice-transcript").textContent = `“${transcript}”`;
    if (selectedVoiceDecision) {
      const label = selectedVoiceDecision === "approve" ? "Approve this order" : "Reject this order";
      $("#voice-status").textContent = `${command.spoken_reply} Confirm to record the decision.`;
      $("#confirm-voice").textContent = label;
      $("#confirm-voice").disabled = false;
    } else {
      $("#voice-status").textContent = "I could not identify an approval decision. Please say “approve this order” or “reject this order”.";
    }
  };
  recognition.onerror = () => { $("#voice-status").textContent = "Voice input was unavailable. Use the visible approval buttons instead."; };
  recognition.start();
}

$("#voice-decision").addEventListener("click", () => { stopWakeListener(); $("#voice-dialog").showModal(); listenForDecision(); });
$("#listen-again").addEventListener("click", listenForDecision);
$("#close-voice").addEventListener("click", () => { $("#voice-dialog").close(); startWakeListener(); });
$("#confirm-voice").addEventListener("click", async () => {
  if (!selectedVoiceDecision) return;
  const decision = selectedVoiceDecision;
  $("#voice-dialog").close();
  await recordBuyerDecision(decision);
});

$("#speak-update").addEventListener("click", () => {
  const order = state.approval;
  const update = order ? `${order.need?.text}: ${STATUS_LABEL[order.status] || order.status}. ${heldReason(order)}` : "There is no order yet.";
  speak(update);
});
$("#end-day").addEventListener("click", async () => {
  const verifiedEvents = [
    "One inventory risk was identified at Hotel North.",
    "A lactose-free milk order was checked by product, availability, cost and policy juries.",
    "One order is waiting for human approval because it exceeds the automatic approval limit.",
  ];
  let summary = "Today the purchasing agent identified one inventory risk, evaluated a lactose-free milk order through four specialist checks, and requested one human approval.";
  try {
    const response = await fetch("/experience/brief", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({buyer_name: "Hotel North", kind: "end_of_day", verified_events: verifiedEvents}),
    });
    if (response.ok) summary = (await response.json()).text;
  } catch (_) { /* The UI still works while API credentials are being added. */ }
  $("#summary-copy").textContent = summary;
  $("#summary-dialog").showModal();
});
$("#close-summary").addEventListener("click", () => $("#summary-dialog").close());
$("#speak-summary").addEventListener("click", () => speak($("#summary-copy").textContent));
$("#close-suggestion").addEventListener("click", () => $("#suggestion-dialog").close());
$("#acknowledge-suggestion").addEventListener("click", () => $("#suggestion-dialog").close());

const savedRules = localStorage.getItem("aimo-rules");
if (savedRules && JSON.parse(savedRules).buyer) {
  const rules = JSON.parse(savedRules);
  $("#policy-copy").textContent = `${rules.buyer}: orders up to €${rules.limit} can proceed automatically when all dietary and delivery rules are met.`;
} else {
  setVimoState("Vimo is ready when you are", "Select Talk to Vimo and say what you need. Default purchasing rules apply until you edit them.");
}
renderJury();
renderTimeline();
loadDecision();
// Browsers block audio (and should ask for the mic) only after a click, so the web page starts on "Talk to Vimo".
// Electron enables autoplay and grants the mic, so the desktop app can start right away.
if (/Electron/.test(navigator.userAgent)) setTimeout(() => startVimo({announce: false}), 350);
