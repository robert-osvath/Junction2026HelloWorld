const state = {
  voice: true,
  approval: null,
  agentState: null,
  conversation: [],
  jury: [],
  events: [],
  orders: [],
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
let elevenSleepTimer = null;
let backendPollTimer = null;
const TURN_SILENCE_MS = 1200;
const TURN_MAX_MS = 5000;
const VOICE_DIAGNOSTICS_ENABLED = false;
const recentVoiceEvents = new Map();

function renderJury() {
  juryList.replaceChildren(...state.jury.map((verdict) => {
    const row = document.createElement("div");
    row.className = "jury-row";
    const mark = document.createElement("span");
    mark.className = "checkmark";
    mark.textContent = verdict.decision === "reject" ? "!" : "✓";
    const juror = document.createElement("strong");
    juror.textContent = `${verdict.juror} jury`;
    const reason = document.createElement("span");
    reason.textContent = verdict.reasons?.join("; ") || "No explanation returned.";
    const confidence = document.createElement("span");
    confidence.textContent = verdict.confidence == null ? verdict.decision : `${Math.round(verdict.confidence * 100)}% confidence`;
    row.append(mark, juror, reason, confidence);
    return row;
  }));
  if (!state.jury.length) juryList.textContent = "No jury verdict is available yet.";
}

function renderTimeline() {
  timeline.replaceChildren(...state.events.map((event) => {
    const item = document.createElement("li");
    const time = document.createElement("time");
    const title = document.createElement("strong");
    time.textContent = event.time;
    title.textContent = event.title;
    item.append(time, title, document.createTextNode(event.detail));
    return item;
  }));
  if (!state.events.length) timeline.textContent = "No backend activity has been recorded yet.";
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

async function speak(text) {
  if (!state.voice) return;
  try {
    const response = await fetch("/experience/speak", {
      method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({text}),
    });
    if (response.ok) {
      const audio = new Audio(URL.createObjectURL(await response.blob()));
      audio.play();
      await new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; });
      return;
    }
  } catch (_) { /* Browser speech remains a local demo fallback. */ }
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
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

async function getBackendAgentState() {
  const ordersResponse = await fetch("/orders");
  if (!ordersResponse.ok) throw new Error("Vimo backend orders are unavailable");
  const orders = await ordersResponse.json();
  const active = orders.find((order) => order.status === "pending_approval") || orders[0] || null;
  state.orders = orders;
  state.approval = orders.find((order) => order.status === "pending_approval") || null;
  state.agentState = active ? {
    order_id: active.id,
    order_status: active.status,
    jury_decision: active.status === "pending_approval" ? "human_approval" : active.status,
    verdicts: active.verdicts || [],
    summary: orderSummary(active),
  } : null;
  return state.agentState;
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
  const policyClaims = String(rules.claim || "").split(",").map((claim) => claim.trim()).filter(Boolean).map((claim) => {
    const normalized = claim.toUpperCase().replace(/[ -]+/g, "_");
    return normalized === "LACTOSE_FREE" ? "FREE_FROM_LACTOSE" : normalized;
  });
  const response = await fetch("/experience/create-draft", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({
      buyer_name: rules.buyer || "My kitchen",
      auto_approve_limit: Number(rules.limit || 500),
      budget: Number(rules.budget || 2000),
      product_id,
      product_intent,
      quantity: Number(quantity),
      unit,
      deliver_by,
      required_claims: required_claims.length ? required_claims : policyClaims,
      banned_allergens: String(rules.allergens || "").split(",").map((value) => value.trim()).filter(Boolean),
      strict_allergens: Boolean(rules.strict_allergens),
      substitutes_allowed: Boolean(rules.substitutes),
      exception_reason,
    }),
  });
  if (!response.ok) throw new Error("The live backend could not create this order draft");
  return response.json();
}

function euro(value) {
  return new Intl.NumberFormat("en-FI", {style: "currency", currency: "EUR"}).format(Number(value || 0));
}

function orderTotal(order) {
  return (order?.lines || []).reduce((total, line) => total + Number(line.total_eur || 0), 0);
}

function orderLabel(order) {
  const line = order?.lines?.[0];
  return line ? `${line.units_needed} ${line.sales_unit} ${line.name}` : order?.need?.text || "Order";
}

function orderSummary(order) {
  if (!order) return "No backend order is active.";
  const line = order.lines?.[0];
  const delivery = line?.available_date || order.need?.deliver_by || "delivery date unavailable";
  return `${orderLabel(order)} for ${euro(orderTotal(order))}, delivery ${delivery}. Status: ${order.status.replaceAll("_", " ")}.`;
}

function statusLabel(status) {
  return String(status || "waiting").replaceAll("_", " ");
}

function renderOrderWatch(orders) {
  const watch = $("#order-watch");
  watch.replaceChildren(...orders.slice(0, 6).map((order) => {
    const row = document.createElement("div");
    row.className = "watch-row";
    const details = document.createElement("div");
    const title = document.createElement("strong");
    const summary = document.createElement("span");
    title.textContent = orderLabel(order);
    summary.textContent = `${statusLabel(order.status)} · ${euro(orderTotal(order))} · ${order.id}`;
    details.append(title, summary);
    const badge = document.createElement("span");
    badge.className = "status";
    badge.textContent = statusLabel(order.status);
    row.append(details, badge);
    return row;
  }));
  if (!orders.length) watch.textContent = "No orders have been created by the backend yet.";
}

async function refreshCockpit() {
  const [ordersResponse, auditResponse] = await Promise.all([fetch("/orders"), fetch("/audit")]);
  if (!ordersResponse.ok || !auditResponse.ok) throw new Error("Backend records are unavailable");
  const orders = await ordersResponse.json();
  const audit = await auditResponse.json();
  state.orders = orders;
  state.approval = orders.find((order) => order.status === "pending_approval") || null;
  const active = state.approval || orders[0] || null;
  state.agentState = active ? {
    order_id: active.id, order_status: active.status,
    jury_decision: active.status === "pending_approval" ? "human_approval" : active.status,
    verdicts: active.verdicts || [], summary: orderSummary(active),
  } : null;
  state.jury = active?.verdicts || [];
  state.events = audit.slice(0, 8).map((event) => ({
    time: new Date(event.ts).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}),
    title: event.action.replaceAll(".", " "), detail: event.human_summary,
  }));
  renderJury();
  renderTimeline();
  renderOrderWatch(orders);
  $("#active-count").textContent = String(orders.length);
  $("#tracked-note").textContent = orders.length === 1 ? "1 backend order" : `${orders.length} backend orders`;
  $("#placed-value").textContent = euro(orders.filter((order) => order.status === "placed").reduce((total, order) => total + orderTotal(order), 0));
  $("#pending-count").textContent = String(orders.filter((order) => order.status === "pending_approval").length);
  $("#decision-status").textContent = statusLabel(active?.status);
  $("#decision-note").textContent = active ? active.id : "No order evaluated";
  $("#orders-updated").textContent = audit[0] ? `Updated ${new Date(audit[0].ts).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}` : "No backend events yet";
  $("#decision-name").textContent = active ? orderLabel(active) : "No active order";
  $("#decision-badge").textContent = statusLabel(active?.status);
  $("#decision-request").textContent = active?.need?.text || "Create an order draft to see the live recommendation and jury evaluation.";
  $("#recommendation-name").textContent = active?.lines?.[0]?.name || "No recommendation yet";
  $("#recommendation-detail").textContent = active ? `${euro(orderTotal(active))} · delivery ${active.lines?.[0]?.available_date || active.need.deliver_by} · ${statusLabel(active.status)}` : "Live order details appear after backend evaluation.";
  const pending = state.approval;
  $("#approval-banner").hidden = !pending;
  if (pending) {
    $("#approval-title").textContent = "Approval needed before this order can proceed";
    $("#approval-detail").textContent = orderSummary(pending);
    $("#approve-order").disabled = false;
    $("#reject-order").disabled = false;
    $("#voice-decision").disabled = false;
  }
  return active;
}

function clearElevenSleepTimer() {
  clearTimeout(elevenSleepTimer);
  elevenSleepTimer = null;
}

function armElevenSleepTimer() {
  clearElevenSleepTimer();
  elevenSleepTimer = setTimeout(() => {
    // Electron's built-in speech recognition is not dependable enough to be
    // the sole wake engine. Keep ElevenLabs audio available and let its agent
    // configuration enforce the wake phrase, so Vimo remains usable.
    setVimoState("Vimo is on standby", "Listening for “Hey Vimo” or “Vimo”. I will only respond to a request or a verified backend update.", true);
    const presence = $("#voice-presence");
    if (presence) {
      presence.textContent = "● Listening for wake phrase";
      presence.dataset.state = "listening";
    }
  }, 5000);
}

function startBackendDecisionPolling() {
  clearInterval(backendPollTimer);
  let lastPendingId = state.approval?.id || null;
  backendPollTimer = setInterval(async () => {
    try {
      await refreshCockpit();
      const changedToPending = state.approval && state.approval.id !== lastPendingId;
      lastPendingId = state.approval?.id || null;
      if (changedToPending) {
        showBackendApprovalPopup();
        setVimoState("Vimo needs your decision", "A verified backend decision is ready for review.");
      }
    } catch (_) { /* Keep the live conversation available while the backend reconnects. */ }
  }, 5000);
}

function openDecisionConfirmation(decision = null, transcript = "") {
  if (!state.approval || state.approval.status !== "pending_approval") return false;
  selectedVoiceDecision = decision;
  $("#voice-dialog").showModal();
  const line = state.approval.lines?.[0];
  const details = [
    ["Product", line?.name || "Not available"],
    ["Quantity", line ? `${line.units_needed} ${line.sales_unit}` : "Not available"],
    ["Total", line ? `€${Number(line.total_eur).toFixed(2)}` : "Not available"],
    ["Available", line?.available_date || "Not available"],
    ["Delivery", line?.logistic_model || "Not available"],
    ["Status", state.approval.status.replaceAll("_", " ")],
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
  $("#voice-transcript").textContent = transcript || orderSummary(state.approval);
  if (decision) {
    const label = decision === "approve" ? "Approve this order" : "Reject this order";
    $("#voice-status").textContent = `Vimo heard “${decision}”. Confirm to record this decision in the buyer workflow.`;
    $("#confirm-voice").textContent = label;
    $("#confirm-voice").disabled = false;
  } else {
    $("#voice-status").textContent = "Review the recommendation, then choose approve or reject visibly.";
    $("#confirm-voice").disabled = true;
  }
  return true;
}

function showBackendApprovalPopup() {
  if (state.approval?.status !== "pending_approval") return false;
  $("#approval-title").textContent = "Vimo needs your decision";
  $("#approval-detail").textContent = orderSummary(state.approval);
  $("#approval-banner").hidden = false;
  return openDecisionConfirmation();
}

async function startElevenLive() {
  if (elevenConversation) return;
  setVimoState("Vimo is connecting", "Starting your live Vimo conversation…", true);
  const sessionResponse = await fetch("/experience/elevenlabs/signed-url");
  if (!sessionResponse.ok) throw new Error("Could not start the ElevenLabs live session");
  const {signed_url: signedUrl} = await sessionResponse.json();
  const {Conversation} = await import("https://esm.sh/@elevenlabs/client@latest?bundle");
  elevenConversation = await Conversation.startSession({
    signedUrl,
    clientTools: {
      get_backend_state: async () => {
        await refreshCockpit();
        return state.agentState || {message: "No backend orders exist yet."};
      },
      search_catalog: async ({query, required_claims = []} = {}) => {
        if (!query?.trim()) return {products: [], message: "A product or category is required."};
        const products = await searchBackendCatalog(query, required_claims);
        return {products, source: "Valio Aimo catalog"};
      },
      show_approval_popup: async () => {
        await refreshCockpit();
        const shown = showBackendApprovalPopup();
        return {shown, message: shown ? "The buyer approval dialog is open." : "No approval is currently required."};
      },
      create_order_draft: async (request = {}) => {
        const draft = await createCatalogDraft(request);
        await refreshCockpit();
        const shown = draft.status === "pending_approval" && openDecisionConfirmation(null, "Vimo created this live backend order draft.");
        return {created: draft.status === "pending_approval", order_id: draft.id, order_status: draft.status, shown};
      },
      propose_order_decision: async ({decision} = {}) => {
        if (decision !== "approve" && decision !== "reject") {
          return {shown: false, message: "A decision must be approve or reject."};
        }
        await refreshCockpit();
        const shown = openDecisionConfirmation(decision, `Vimo heard: “${decision} this offer.”`);
        return {shown, message: shown ? `The ${decision} confirmation is ready for the buyer.` : "There is no pending order to decide."};
      },
      execute_order_decision: async ({decision} = {}) => {
        if (decision !== "approve" && decision !== "reject") {
          return {executed: false, message: "A decision must be approve or reject."};
        }
        await refreshCockpit();
        const updated = await recordBuyerDecision(decision);
        return updated
          ? {executed: true, order_id: updated.id, order_status: updated.status}
          : {executed: false, message: "There is no pending order to decide."};
      },
      show_suggestion_popup: async ({summary = "Vimo has a purchasing suggestion ready for review."} = {}) => {
        $("#suggestion-copy").textContent = summary;
        $("#suggestion-dialog").showModal();
        return {shown: true};
      },
    },
    onConnect: async () => {
      setVimoState("Vimo is on standby", "Listening for “Hey Vimo” or “Vimo”.", true);
      await loadDecision();
      startBackendDecisionPolling();
    },
    onModeChange: (event) => {
      const mode = typeof event === "string" ? event : event?.mode;
      if (mode === "speaking") {
        clearElevenSleepTimer();
        setVimoState("Vimo is speaking", "You can interrupt me at any time.");
      }
      if (mode === "listening") {
        armElevenSleepTimer();
      }
    },
    onUserTranscript: () => {
      clearElevenSleepTimer();
      setVimoState("Vimo is awake", "I heard you. Checking the latest purchasing evidence…", true);
    },
    onError: () => {
      elevenConversation = null;
      setVimoState("Vimo needs attention", "The live conversation disconnected. Select Talk to Vimo to reconnect.");
    },
    onDisconnect: () => {
      elevenConversation = null;
      clearInterval(backendPollTimer);
      backendPollTimer = null;
    },
  });
  armElevenSleepTimer();
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
  const approvalOpen = state.agentState?.jury_decision === "human_approval" || state.approval?.status === "pending_approval";
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
  setVimoState("Vimo is monitoring", `Rules for ${rules.buyer} are active. I will call out only when something needs your attention.`);
  vimoEnabled = true;
  speak(`Your rules are active. I will call out only when something needs your attention.`).then(startWakeListener);
}

function startVimo({announce = true} = {}) {
  if (!localStorage.getItem("aimo-rules")) {
    $("#rules-dialog").showModal();
    setVimoState("Set your purchasing rules", "Vimo needs your buyer limits before it can create or approve any order.");
    return;
  }
  vimoEnabled = true;
  const alert = "Vimo is active. Tell me what your kitchen needs, or ask what needs attention.";
  setVimoState("Vimo is active", "Say “Hey Vimo” for an update, a question, or an approval decision.");
  startElevenLive().catch((error) => {
    console.warn("ElevenLabs live session unavailable", error);
    startLegacyVimo(announce, alert);
  });
}

function startLegacyVimo(announce, alert) {
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
  try {
    await refreshCockpit();
  } catch (_) {
    $("#approval-banner").hidden = true;
  }
}

async function recordBuyerDecision(decision) {
  if (!state.approval || state.approval.status !== "pending_approval") return null;
  const endpoint = decision === "approve" ? "approve" : "reject";
  const response = await fetch(`/orders/${state.approval.id}/${endpoint}`, {method: "POST"});
  if (!response.ok) throw new Error("The decision could not be recorded");
  const updated = await response.json();
  selectedVoiceDecision = null;
  $("#approval-banner").hidden = true;
  if ($("#voice-dialog").open) $("#voice-dialog").close();
  if ($("#suggestion-dialog").open) $("#suggestion-dialog").close();
  $("#approve-order").disabled = true;
  $("#reject-order").disabled = true;
  $("#voice-decision").disabled = true;
  await refreshCockpit();
  setVimoState("Vimo is sleeping", "Decision completed. Say “Hey Vimo” when you need me.");
  return updated;
}

$("#open-rules").addEventListener("click", () => $("#rules-dialog").showModal());
$("#start-vimo").addEventListener("click", startVimo);
$("#rules-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const rules = Object.fromEntries(new FormData(event.currentTarget));
  localStorage.setItem("aimo-rules", JSON.stringify(rules));
  $("#policy-copy").textContent = `${rules.buyer}: orders up to €${rules.limit} can proceed automatically when all dietary and delivery rules are met.`;
  $("#rules-dialog").close();
  speak("Your purchasing rules have been saved.");
  if (!vimoEnabled) startVimo();
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
  const update = state.agentState?.summary || "There is no backend order to report yet.";
  speak(update);
});
$("#end-day").addEventListener("click", async () => {
  await refreshCockpit();
  const rules = JSON.parse(localStorage.getItem("aimo-rules") || "{}");
  const verifiedEvents = state.events.map((event) => event.detail).filter(Boolean).slice(0, 12);
  let summary = verifiedEvents.length
    ? verifiedEvents.join(" ")
    : "No backend purchasing activity has been recorded today.";
  try {
    const response = await fetch("/experience/brief", {
      method: "POST", headers: {"content-type": "application/json"},
      body: JSON.stringify({buyer_name: rules.buyer || "Buyer", kind: "end_of_day", verified_events: verifiedEvents.length ? verifiedEvents : [summary]}),
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
if (savedRules) {
  const rules = JSON.parse(savedRules);
  $("#policy-copy").textContent = `${rules.buyer}: orders up to €${rules.limit} can proceed automatically when all dietary and delivery rules are met.`;
} else {
  setVimoState("Vimo is ready when you are", "Select Talk to Vimo once, then answer four quick questions by voice.");
}
renderJury();
renderTimeline();
loadDecision();
// Once microphone access has been granted, Vimo arms itself on every page
// visit. The visible button is retained only for browsers that require a
// one-time user gesture before they permit microphone/audio access.
setTimeout(() => startVimo({announce: false}), 350);
