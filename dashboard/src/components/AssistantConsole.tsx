"use client";

import { type FormEvent, type KeyboardEvent, type ReactNode, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  Bot,
  ChartColumn,
  Check,
  CircleCheck,
  CircleMinus,
  CircleX,
  ClipboardCheck,
  Clock,
  LoaderCircle,
  Megaphone,
  MessageSquare,
  Mic,
  MicOff,
  Monitor,
  Power,
  Radio,
  RotateCcw,
  Search,
  Sparkles,
  StickyNote,
  TriangleAlert,
  Volume2,
  WifiOff,
  Wrench,
  X
} from "lucide-react";
import { fetchCurrentShift } from "@/lib/api";
import type { CashierShiftDetail } from "@/lib/types";
import {
  type ActionOutcome,
  type PendingAction,
  MAX_MESSAGE_LENGTH,
  cancelAssistantAction,
  confirmAssistantAction,
  matchSpokenDecision,
  newConversationId,
  resetAssistantConversation,
  sendAssistantMessageStream,
  liveToolText,
  toolChipText,
  type HandoverNote,
  HANDOVER_TOOLS,
  fetchHandoverNotes,
  resolveHandoverNote
} from "@/lib/assistant";
import styles from "@/app/assistant/assistant.module.css";

// Minimal Web Speech API typings (not part of TypeScript's DOM lib).
type RecognitionAlternative = { transcript: string };
type RecognitionResult = { isFinal: boolean; length: number; [index: number]: RecognitionAlternative };
type RecognitionEvent = { resultIndex: number; results: { length: number; [index: number]: RecognitionResult } };
type RecognitionErrorEvent = { error: string };
type Recognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onend: (() => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  onresult: ((event: RecognitionEvent) => void) | null;
  abort: () => void;
  start: () => void;
  stop: () => void;
};
type RecognitionConstructor = new () => Recognition;

type Status = "idle" | "listening" | "thinking" | "speaking" | "error";
// How the mic button looks: a status, or "unavailable" (no mic in this browser/page).
type MicLook = Status | "unavailable";
type ActionState = "pending" | "running" | "done" | "failed" | "cancelled" | "expired";
type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  // When it was added (shown as a small timestamp).
  at: number;
  text: string;
  tools?: string[];
  error?: string;
  // Phase 3: a prepared action shown as a confirm card under this reply.
  action?: PendingAction;
  actionState?: ActionState;
  outcome?: ActionOutcome;
};
type Pending = { action: PendingAction; messageId: number };

const ACTION_KIND_LABELS: Record<string, string> = {
  add_time: "Add time",
  shutdown_pcs: "Shut down idle PCs",
  broadcast: "Announcement",
  message_pc: "Message to PC"
};

const ACTION_KIND_ICONS: Record<string, ReactNode> = {
  add_time: <Clock aria-hidden="true" />,
  broadcast: <Megaphone aria-hidden="true" />,
  message_pc: <MessageSquare aria-hidden="true" />,
  shutdown_pcs: <Power aria-hidden="true" />
};

const TARGET_ICONS: Record<string, ReactNode> = {
  done: <CircleCheck aria-hidden="true" />,
  failed: <CircleX aria-hidden="true" />,
  offline: <WifiOff aria-hidden="true" />,
  received: <Radio aria-hidden="true" />,
  skipped: <CircleMinus aria-hidden="true" />
};

// Empty state: each chip sends its question as if typed.
const SUGGESTIONS = [
  "How did we do today?",
  "Which PCs are free?",
  "Which sessions are running out soon?",
  "Who's playing right now?",
  "Any notes from the last shift?",
  "Close my shift"
];

const CAPABILITIES: { icon: ReactNode; text: string; confirm?: boolean }[] = [
  { icon: <ChartColumn aria-hidden="true" />, text: "Sales today or any past day" },
  { icon: <Monitor aria-hidden="true" />, text: "Free PCs and running sessions" },
  { icon: <Search aria-hidden="true" />, text: "Look up members and balances" },
  { icon: <MessageSquare aria-hidden="true" />, text: "Message a PC or all PCs", confirm: true },
  { icon: <Clock aria-hidden="true" />, text: "Add time (billed later)", confirm: true },
  { icon: <Power aria-hidden="true" />, text: "Shut down idle PCs (Admin)", confirm: true },
  { icon: <StickyNote aria-hidden="true" />, text: "Leave and read handover notes" },
  { icon: <ClipboardCheck aria-hidden="true" />, text: "Check the drawer before closing" }
];

const SHOP_TIME = new Intl.DateTimeFormat("en-PH", { hour: "numeric", minute: "2-digit", timeZone: "Asia/Manila" });

function shopTime(value: number | string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : SHOP_TIME.format(date);
}
const TARGET_WORDS: Record<string, string> = {
  done: "done",
  failed: "didn't get it",
  offline: "offline",
  received: "received the shutdown command",
  skipped: "skipped, in use"
};

const NO_SPEECH_HINT =
  "I didn't hear anything. If you were speaking, check which microphone Chrome is using: click the mic icon in the address bar, or open chrome://settings/content/microphone.";

function unmatchedDecisionHint(heard: string): string {
  return `Heard: “${heard}” — say “confirm” or “cancel”, or use the buttons.`;
}
const ACTION_STATE_LABELS: Record<ActionState, string> = {
  cancelled: "Cancelled",
  done: "Done",
  expired: "Expired, ask again",
  failed: "Didn't go through",
  pending: "Needs OK",
  running: "Sending…"
};
type VoiceSupport = { mic: "ok" | "unsupported" | "insecure"; speech: boolean };

const RECOGNITION_LANG = "en-PH";
const STATUS_LABELS: Record<Status, string> = {
  error: "Something went wrong",
  idle: "Ready",
  listening: "Listening…",
  speaking: "Speaking…",
  thinking: "Thinking…"
};

function getRecognitionConstructor(): RecognitionConstructor | null {
  if (typeof window === "undefined") {
    return null;
  }
  const speechWindow = window as unknown as {
    SpeechRecognition?: RecognitionConstructor;
    webkitSpeechRecognition?: RecognitionConstructor;
  };
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null;
}

const SERVER_SUPPORT: VoiceSupport = { mic: "unsupported", speech: false };
let cachedSupport: VoiceSupport | null = null;

function readVoiceSupport(): VoiceSupport {
  if (cachedSupport === null) {
    cachedSupport = {
      // Browsers only allow the microphone on https:// or localhost.
      mic: !window.isSecureContext ? "insecure" : getRecognitionConstructor() ? "ok" : "unsupported",
      speech: "speechSynthesis" in window
    };
  }
  return cachedSupport;
}

function subscribeNever(): () => void {
  return () => undefined;
}

function micErrorMessage(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone permission was denied. Allow the mic for this site in the browser, or type your question.";
    case "audio-capture":
      return "No microphone was found. Plug one in, or pick another one in Chrome's site settings (the mic icon in the address bar), or type your question.";
    case "no-speech":
      return NO_SPEECH_HINT;
    case "network":
      return "Speech recognition needs an internet connection. You can type your question instead.";
    default:
      return `Speech recognition stopped (${code}). You can type your question instead.`;
  }
}

export function AssistantConsole() {
  const support = useSyncExternalStore(subscribeNever, readVoiceSupport, () => SERVER_SUPPORT);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [interim, setInterim] = useState("");
  // What the mic heard: live while holding the button, final after release.
  const [heard, setHeard] = useState<{ live: boolean; text: string } | null>(null);
  // Shift handover notes panel (phase 3 step d).
  const [handover, setHandover] = useState<{ enabled: boolean; notes: HandoverNote[] }>({ enabled: false, notes: [] });
  const [handoverError, setHandoverError] = useState<string | null>(null);
  // Tools the assistant has started while still thinking (streamed).
  const [liveTools, setLiveTools] = useState<string[]>([]);
  const liveTranscriptRef = useRef("");
  const [speakReplies, setSpeakReplies] = useState(true);
  const [conversationId, setConversationId] = useState(newConversationId);
  const recognitionRef = useRef<Recognition | null>(null);
  const transcriptRef = useRef("");
  const nextIdRef = useRef(1);
  const logRef = useRef<HTMLOListElement | null>(null);
  // The pending action lives in state (for rendering) and in a ref (so the
  // speech-recognition callback always sees the current one).
  const [pending, setPendingState] = useState<Pending | null>(null);
  const pendingRef = useRef<Pending | null>(null);
  const expiryTimerRef = useRef<number | null>(null);
  const busy = status === "thinking";
  // Side panel: the signed-in operator's own shift (read-only; undefined = not available).
  const [shift, setShift] = useState<CashierShiftDetail | null | undefined>(undefined);
  // Wide screens: the console fills the window below whatever the shell shows above it.
  const consoleRef = useRef<HTMLElement | null>(null);
  const [fitHeight, setFitHeight] = useState<number | null>(null);
  const spaceHeldRef = useRef(false);

  function setPending(next: Pending | null) {
    pendingRef.current = next;
    setPendingState(next);
    if (expiryTimerRef.current !== null) {
      window.clearTimeout(expiryTimerRef.current);
      expiryTimerRef.current = null;
    }
    if (next) {
      expiryTimerRef.current = window.setTimeout(() => {
        if (pendingRef.current?.action.id === next.action.id) {
          pendingRef.current = null;
          setPendingState(null);
          updateMessage(next.messageId, { actionState: "expired" });
        }
      }, Math.max(1, next.action.expires_in_seconds) * 1000);
    }
  }

  function updateMessage(id: number, patch: Partial<ChatMessage>) {
    setMessages((current) => current.map((message) => (message.id === id ? { ...message, ...patch } : message)));
  }

  function refreshHandover() {
    void fetchHandoverNotes().then((result) => {
      setHandover({ enabled: result.enabled, notes: result.notes });
      setHandoverError(result.error ?? null);
    });
  }

  useEffect(() => {
    let active = true;
    void fetchHandoverNotes().then((result) => {
      if (active) {
        setHandover({ enabled: result.enabled, notes: result.notes });
        setHandoverError(result.error ?? null);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  function refreshShift() {
    fetchCurrentShift()
      .then((detail) => setShift(detail))
      .catch(() => setShift(undefined));
  }

  useEffect(() => {
    let active = true;
    fetchCurrentShift()
      .then((detail) => {
        if (active) setShift(detail);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  // Fit the console to the window on wide screens (no page scrolling): measure where it
  // starts, since the shell may show a shift bar above it.
  useEffect(() => {
    function measure() {
      const element = consoleRef.current;
      if (!element) return;
      if (window.innerWidth < 1100) {
        setFitHeight(null);
        return;
      }
      const top = element.getBoundingClientRect().top + window.scrollY;
      setFitHeight(Math.max(560, Math.floor(window.innerHeight - top - 16)));
    }
    measure();
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    observer.observe(document.body);
    return () => {
      window.removeEventListener("resize", measure);
      observer.disconnect();
    };
  }, []);

  async function markNoteDone(noteId: string) {
    const error = await resolveHandoverNote(conversationId, noteId);
    setHandoverError(error);
    refreshHandover();
  }

  useEffect(() => {
    logRef.current?.scrollTo({ behavior: "smooth", top: logRef.current.scrollHeight });
  }, [messages, interim]);

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
      if (expiryTimerRef.current !== null) {
        window.clearTimeout(expiryTimerRef.current);
      }
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  const speak = useCallback(
    (text: string) => {
      if (!speakReplies || !support.speech || !text) {
        setStatus("idle");
        return;
      }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "en-US";
      utterance.rate = 1.03;
      utterance.onend = () => setStatus((current) => (current === "speaking" ? "idle" : current));
      utterance.onerror = () => setStatus((current) => (current === "speaking" ? "idle" : current));
      window.speechSynthesis.cancel();
      setStatus("speaking");
      window.speechSynthesis.speak(utterance);
    },
    [speakReplies, support.speech]
  );

  function addAssistantMessage(message: Omit<ChatMessage, "at" | "id" | "role">): number {
    const id = nextIdRef.current++;
    setMessages((current) => [...current, { ...message, at: Date.now(), id, role: "assistant" }]);
    return id;
  }

  async function resolvePending(decision: "confirm" | "cancel", spokenOrTyped?: string) {
    const current = pendingRef.current;
    if (!current) {
      return;
    }
    setPending(null);
    setNotice(null);
    if (spokenOrTyped) {
      setMessages((list) => [...list, { at: Date.now(), id: nextIdRef.current++, role: "user", text: spokenOrTyped }]);
    }
    updateMessage(current.messageId, { actionState: decision === "confirm" ? "running" : "cancelled" });
    setStatus("thinking");
    const result =
      decision === "confirm"
        ? await confirmAssistantAction(conversationId, current.action.id)
        : await cancelAssistantAction(conversationId, current.action.id);
    if (decision === "confirm") {
      updateMessage(current.messageId, {
        actionState: result.outcome?.ok ? "done" : "failed",
        outcome: result.outcome
      });
    }
    addAssistantMessage({ error: result.error, text: result.reply });
    if (result.error) {
      setNotice(result.error);
    }
    speak(result.reply);
  }

  async function ask(rawText: string, source: "voice" | "text" = "text") {
    const text = rawText.trim().slice(0, MAX_MESSAGE_LENGTH);
    if (!text || busy) {
      return;
    }
    // While an action is waiting, "confirm"/"cancel" are decided HERE, by an
    // exact phrase match -- never by the model. Anything else spoken is not
    // sent anywhere; typed text is treated as a new question.
    if (pendingRef.current) {
      const decision = matchSpokenDecision(text);
      if (decision) {
        await resolvePending(decision, text);
        return;
      }
      if (source === "voice") {
        setNotice(unmatchedDecisionHint(text));
        setStatus("idle");
        return;
      }
    }
    setNotice(null);
    setMessages((current) => [...current, { at: Date.now(), id: nextIdRef.current++, role: "user", text }]);
    setStatus("thinking");
    setLiveTools([]);
    const result = await sendAssistantMessageStream(conversationId, text, (tool) =>
      setLiveTools((current) => (current.includes(tool) ? current : [...current, tool]))
    );
    setLiveTools([]);
    const messageId = addAssistantMessage({
      action: result.pending_action,
      actionState: result.pending_action ? "pending" : undefined,
      error: result.error,
      text: result.reply,
      tools: result.tools_used
    });
    if (result.pending_action) {
      // A newer proposal replaces any older card that was still waiting.
      const previous = pendingRef.current;
      if (previous) {
        updateMessage(previous.messageId, { actionState: "expired" });
      }
      setPending({ action: result.pending_action, messageId });
    }
    if (result.error) {
      setNotice(result.error);
    }
    if (result.tools_used.some((tool) => HANDOVER_TOOLS.has(tool))) {
      refreshHandover();
    }
    refreshShift();
    speak(result.reply);
    if (result.error && !(speakReplies && support.speech)) {
      setStatus("error");
    }
  }

  function startListening() {
    if (support.mic !== "ok" || busy || recognitionRef.current) {
      return;
    }
    const RecognitionClass = getRecognitionConstructor();
    if (!RecognitionClass) {
      return;
    }
    if (support.speech) {
      window.speechSynthesis.cancel(); // let staff talk over a long answer
    }
    const recognition = new RecognitionClass();
    recognition.lang = RECOGNITION_LANG;
    recognition.interimResults = true;
    recognition.continuous = true;
    transcriptRef.current = "";
    liveTranscriptRef.current = "";
    setHeard({ live: true, text: "" });
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (result.isFinal) {
          finalText += result[0].transcript;
        } else {
          interimText += result[0].transcript;
        }
      }
      transcriptRef.current = finalText;
      liveTranscriptRef.current = `${finalText}${interimText}`.trim();
      setInterim(liveTranscriptRef.current);
      setHeard({ live: true, text: liveTranscriptRef.current });
    };
    recognition.onerror = (event) => {
      if (event.error === "aborted") {
        return;
      }
      setNotice(micErrorMessage(event.error));
      setStatus("error");
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      // Releasing quickly can end recognition before Chrome finalises the
      // last words; fall back to the latest interim text rather than drop it.
      const spoken = transcriptRef.current.trim() || liveTranscriptRef.current.trim();
      setInterim("");
      setHeard(spoken ? { live: false, text: spoken } : null);
      if (spoken) {
        void ask(spoken, "voice");
      } else {
        setNotice((current) => current ?? NO_SPEECH_HINT);
        setStatus((current) => (current === "listening" ? "idle" : current));
      }
    };
    recognitionRef.current = recognition;
    setNotice(null);
    setStatus("listening");
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      setNotice("Couldn't start the microphone. You can type your question instead.");
      setStatus("error");
    }
  }

  function stopListening() {
    recognitionRef.current?.stop();
  }

  // Spacebar = hold to talk, unless typing (or a button/link was reached with the keyboard,
  // where Space keeps its usual meaning).
  const micControlsRef = useRef({ start: startListening, stop: stopListening });
  useEffect(() => {
    micControlsRef.current = { start: startListening, stop: stopListening };
  });

  useEffect(() => {
    function keepsSpace(target: EventTarget | null): boolean {
      const element = target instanceof HTMLElement ? target : null;
      if (!element) return false;
      if (element.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)) return true;
      return ["A", "BUTTON"].includes(element.tagName) && element.matches(":focus-visible");
    }
    function isSpace(event: globalThis.KeyboardEvent) {
      return event.code === "Space" || event.key === " ";
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (!isSpace(event) || event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.repeat) {
        if (spaceHeldRef.current) event.preventDefault();
        return;
      }
      if (keepsSpace(event.target)) return;
      event.preventDefault();
      spaceHeldRef.current = true;
      micControlsRef.current.start();
    }
    function release() {
      if (!spaceHeldRef.current) return;
      spaceHeldRef.current = false;
      micControlsRef.current.stop();
    }
    function onKeyUp(event: globalThis.KeyboardEvent) {
      if (!isSpace(event) || !spaceHeldRef.current) return;
      event.preventDefault();
      release();
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", release);
    };
  }, []);

  function handleMicKey(event: KeyboardEvent<HTMLButtonElement>) {
    if ((event.key === " " || event.key === "Enter") && !event.repeat) {
      event.preventDefault();
      if (event.type === "keydown") {
        startListening();
      } else {
        stopListening();
      }
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft;
    setDraft("");
    void ask(text);
  }

  function handleNewConversation() {
    recognitionRef.current?.abort();
    if (support.speech) {
      window.speechSynthesis.cancel();
    }
    void resetAssistantConversation(conversationId);
    setPending(null);
    setConversationId(newConversationId());
    setMessages([]);
    setNotice(null);
    setInterim("");
    setHeard(null);
    setStatus("idle");
  }

  function stopSpeaking() {
    window.speechSynthesis.cancel();
    setStatus("idle");
  }

  const micHint =
    support.mic === "ok"
      ? pending
        ? "Hold and say “confirm” or “cancel”"
        : "Hold to talk, release to send"
      : support.mic === "insecure"
        ? "The mic needs https:// or localhost. Type below instead."
        : "Voice input isn't supported in this browser (try Chrome or Edge). Type below instead.";
  const micState: MicLook = support.mic === "ok" ? status : "unavailable";

  return (
    <section
      aria-labelledby="assistant-title"
      className={styles.console}
      ref={consoleRef}
      style={fitHeight ? { height: fitHeight } : undefined}
    >
      <header className={styles.header}>
        <div className={styles.titleBlock}>
          <span className={styles.titleIcon} aria-hidden="true">
            <Sparkles />
          </span>
          <div>
            <h1 id="assistant-title">Shift Assistant</h1>
            <p className={styles.eyebrow}>Beta · Actions need your confirmation</p>
          </div>
        </div>
        <div className={styles.headerActions}>
          <span className={`${styles.statusPill} ${styles[`pill_${status}`]}`}>
            <span className={`${styles.statusDot} ${styles[`status_${status}`]}`} aria-hidden="true" />
            {STATUS_LABELS[status]}
          </span>
          <label className={styles.toggle}>
            <input
              checked={speakReplies}
              disabled={!support.speech}
              onChange={(event) => setSpeakReplies(event.target.checked)}
              role="switch"
              type="checkbox"
            />
            <span aria-hidden="true" className={styles.switchTrack}>
              <span className={styles.switchThumb} />
            </span>
            Speak replies
          </label>
          <button className={styles.secondaryButton} onClick={handleNewConversation} type="button">
            <RotateCcw aria-hidden="true" />
            New conversation
          </button>
        </div>
      </header>

      <div className={styles.layout}>
        <div className={styles.panel}>
          <ol aria-live="polite" className={styles.log} ref={logRef}>
            {messages.length === 0 && !interim ? (
              <li className={styles.empty}>
                <span className={styles.emptyIcon} aria-hidden="true">
                  <Bot />
                </span>
                <h2>What can I help with?</h2>
                <p>Hold the mic button (or the Spacebar) and ask, or pick a question:</p>
                <div className={styles.suggestions}>
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      className={styles.suggestion}
                      disabled={busy}
                      key={suggestion}
                      onClick={() => void ask(suggestion)}
                      type="button"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </li>
            ) : null}
            {messages.map((message) => (
              <li className={message.role === "user" ? styles.userRow : styles.assistantRow} key={message.id}>
                {message.role === "assistant" ? (
                  <span aria-hidden="true" className={styles.replyIcon}>
                    <Sparkles />
                  </span>
                ) : null}
                <div className={message.role === "user" ? styles.userBubble : styles.assistantBubble}>
                  <span className={styles.speaker}>{message.role === "user" ? "You" : "Assistant"}</span>
                  <p>{message.text}</p>
                  {message.tools && message.tools.length > 0 ? (
                    <div className={styles.chips}>
                      {message.tools.map((tool) => (
                        <span className={styles.chip} key={tool}>
                          <Wrench aria-hidden="true" />
                          {toolChipText(tool)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {message.action ? (
                    <ActionCard
                      action={message.action}
                      disabled={busy || pending?.messageId !== message.id}
                      onCancel={() => void resolvePending("cancel")}
                      onConfirm={() => void resolvePending("confirm")}
                      outcome={message.outcome}
                      state={message.actionState ?? "pending"}
                    />
                  ) : null}
                  {message.error ? <span className={styles.errorDetail}>{message.error}</span> : null}
                </div>
                <time className={styles.time} dateTime={new Date(message.at).toISOString()}>
                  {shopTime(message.at)}
                </time>
              </li>
            ))}
            {interim ? (
              <li className={styles.userRow}>
                <div className={`${styles.userBubble} ${styles.interim}`}>
                  <span className={styles.speaker}>You</span>
                  <p>{interim}</p>
                </div>
              </li>
            ) : null}
          </ol>

          <div className={styles.controls}>
            {heard ? (
              <p aria-live="polite" className={`${styles.heard} ${heard.live ? styles.heardLive : ""}`}>
                <span className={styles.heardLabel}>{heard.live ? "Hearing" : "Heard"}</span>
                {heard.text ? `“${heard.text}”` : <span className={styles.heardWaiting}>(waiting for speech…)</span>}
              </p>
            ) : null}
            {notice ? (
              <div className={styles.notice} role="alert">
                <TriangleAlert aria-hidden="true" />
                <span>{notice}</span>
              </div>
            ) : null}

            <div className={styles.composer}>
              <div className={`${styles.micWrap} ${styles[`mic_${micState}`]}`}>
                <button
                  aria-label={status === "listening" ? "Listening. Release to send." : "Hold to talk"}
                  aria-pressed={status === "listening"}
                  className={`${styles.micButton} ${status === "listening" ? styles.micActive : ""}`}
                  disabled={support.mic !== "ok" || busy}
                  onContextMenu={(event) => event.preventDefault()}
                  onKeyDown={handleMicKey}
                  onKeyUp={handleMicKey}
                  onPointerCancel={stopListening}
                  onPointerDown={(event) => {
                    event.currentTarget.setPointerCapture(event.pointerId);
                    startListening();
                  }}
                  onPointerUp={stopListening}
                  type="button"
                >
                  <MicStateIcon state={micState} />
                </button>
              </div>

              <div className={styles.composerMain}>
                <div className={styles.statusRow}>
                  <span aria-live="polite" className={styles.statusLabel}>
                    {STATUS_LABELS[status]}
                  </span>
                  {status === "thinking" && liveTools.length > 0 ? (
                    <span className={styles.liveTools}>
                      {liveTools.map((tool) => (
                        <span className={styles.chip} key={tool}>
                          <Wrench aria-hidden="true" />
                          {liveToolText(tool)}
                        </span>
                      ))}
                    </span>
                  ) : null}
                  {status === "speaking" ? (
                    <button className={styles.linkButton} onClick={stopSpeaking} type="button">
                      Stop
                    </button>
                  ) : null}
                  <span className={styles.micHint}>
                    {micHint}
                    {support.mic === "ok" ? <kbd className={styles.kbd}>Space</kbd> : null}
                  </span>
                </div>

                <form className={styles.textForm} onSubmit={handleSubmit}>
                  <input
                    aria-label="Type a question"
                    disabled={busy}
                    maxLength={MAX_MESSAGE_LENGTH}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder={pending ? "Type “confirm” or “cancel”, or ask something else…" : "Or type a question…"}
                    value={draft}
                  />
                  <button disabled={busy || !draft.trim()} type="submit">
                    {busy ? "Thinking…" : "Ask"}
                  </button>
                </form>
              </div>
            </div>
          </div>
        </div>

        <aside aria-label="Shift information" className={styles.side}>
          {handover.enabled ? (
            <HandoverPanel error={handoverError} notes={handover.notes} onDone={(id) => void markNoteDone(id)} />
          ) : null}
          {shift !== undefined ? <ShiftCard shift={shift} /> : null}
          <section aria-labelledby="capabilities-title" className={styles.sideCard}>
            <div className={styles.sideHeader}>
              <h2 id="capabilities-title">What I can do</h2>
            </div>
            <ul className={styles.capabilities}>
              {CAPABILITIES.map((item) => (
                <li key={item.text}>
                  {item.icon}
                  <span>{item.text}</span>
                  {item.confirm ? <span className={styles.confirmTag}>you confirm</span> : null}
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </section>
  );
}

function MicStateIcon({ state }: { state: MicLook }) {
  switch (state) {
    case "thinking":
      return <LoaderCircle aria-hidden="true" className={styles.spin} />;
    case "speaking":
      return <Volume2 aria-hidden="true" />;
    case "error":
    case "unavailable":
      return <MicOff aria-hidden="true" />;
    default:
      return <Mic aria-hidden="true" />;
  }
}

function ShiftCard({ shift }: { shift: CashierShiftDetail | null }) {
  if (!shift || shift.shift.status !== "open") {
    return (
      <section aria-labelledby="shift-title" className={styles.sideCard}>
        <div className={styles.sideHeader}>
          <h2 id="shift-title">Current shift</h2>
        </div>
        <p className={styles.sideEmpty}>No shift open.</p>
      </section>
    );
  }
  const { totals } = shift;
  const blockers = totals.settlement_pending_sessions + totals.review_required_payments;
  // The expected drawer amount is deliberately not shown: the close check asks for a blind count.
  return (
    <section aria-labelledby="shift-title" className={styles.sideCard}>
      <div className={styles.sideHeader}>
        <h2 id="shift-title">Current shift</h2>
        <span className={styles.openBadge}>Open</span>
      </div>
      <dl className={styles.shiftFacts}>
        <div>
          <dt>Cashier</dt>
          <dd>{shift.shift.cashier_name ?? "—"}</dd>
        </div>
        <div>
          <dt>Since</dt>
          <dd>{shopTime(shift.shift.opened_at)}</dd>
        </div>
        <div>
          <dt>Payments</dt>
          <dd>{totals.payment_count}</dd>
        </div>
        <div>
          <dt>Sessions</dt>
          <dd>{totals.sessions_handled}</dd>
        </div>
      </dl>
      {blockers > 0 ? (
        <p className={styles.shiftWarning}>
          <TriangleAlert aria-hidden="true" />
          {`${totals.settlement_pending_sessions} to settle · ${totals.review_required_payments} to review`}
        </p>
      ) : null}
    </section>
  );
}

function HandoverPanel({
  notes,
  error,
  onDone
}: {
  notes: HandoverNote[];
  error: string | null;
  onDone: (noteId: string) => void;
}) {
  return (
    <section aria-labelledby="handover-title" className={`${styles.sideCard} ${styles.handover}`}>
      <div className={styles.sideHeader}>
        <h2 id="handover-title">
          <StickyNote aria-hidden="true" />
          Handover notes
        </h2>
        <span>{notes.length} open</span>
      </div>
      {error ? <p className={styles.handoverError}>{error}</p> : null}
      {notes.length === 0 && !error ? <p className={styles.sideEmpty}>No open notes.</p> : null}
      <ul className={styles.handoverList}>
        {notes.map((note) => (
          <li key={note.note_id}>
            <div>
              <p>{note.text}</p>
              <span>{`${note.author} · ${note.age}`}</span>
            </div>
            <button className={styles.secondaryButton} onClick={() => onDone(note.note_id)} type="button">
              <Check aria-hidden="true" />
              Done
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ActionCard({
  action,
  state,
  outcome,
  disabled,
  onConfirm,
  onCancel
}: {
  action: PendingAction;
  state: ActionState;
  outcome?: ActionOutcome;
  disabled: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const results = outcome?.targets ?? [];
  const skipped = action.details.skipped_offline ?? [];
  return (
    <div
      aria-label="Prepared action"
      className={`${styles.actionCard} ${action.kind === "shutdown_pcs" ? styles.tone_rose : styles.tone_violet} ${styles[`action_${state}`]}`}
      role="group"
    >
      <div className={styles.actionHeader}>
        <span className={styles.actionKind}>
          {ACTION_KIND_ICONS[action.kind] ?? <Sparkles aria-hidden="true" />}
          {ACTION_KIND_LABELS[action.kind] ?? "Action"}
        </span>
        <span className={styles.actionState}>
          {state === "running" ? <LoaderCircle aria-hidden="true" className={styles.spin} /> : null}
          {ACTION_STATE_LABELS[state]}
        </span>
      </div>
      <p className={styles.actionSummary}>{action.summary}</p>
      {results.length > 0 ? (
        <div className={styles.targetGrid}>
          {results.map((result) => (
            <span
              className={`${styles.targetTile} ${styles[`target_${result.status}`]}`}
              key={result.pc}
              title={result.detail ?? undefined}
            >
              {TARGET_ICONS[result.status] ?? <Monitor aria-hidden="true" />}
              <span className={styles.targetName}>{result.pc}</span>
              <span className={styles.targetWord}>{TARGET_WORDS[result.status] ?? result.status}</span>
            </span>
          ))}
        </div>
      ) : (
        <div className={styles.targetGrid}>
          {action.targets.map((target) => (
            <span className={styles.targetTile} key={target}>
              <Monitor aria-hidden="true" />
              <span className={styles.targetName}>{target}</span>
            </span>
          ))}
        </div>
      )}
      {action.kind === "shutdown_pcs" && results.length > 0 ? (
        <ul className={styles.targetDetails}>
          {results.map((result) => (
            <li key={result.pc}>
              {`${result.pc}: ${TARGET_WORDS[result.status] ?? result.status}${result.detail ? ` (${result.detail})` : ""}`}
            </li>
          ))}
        </ul>
      ) : null}
      {action.details.admin_only && state === "pending" ? (
        <p className={styles.actionNote}>Admin account only. Idle PCs only; never forced, so a PC that starts a session first is skipped.</p>
      ) : null}
      {action.kind === "shutdown_pcs" && (action.details.skipped ?? []).length > 0 && results.length === 0 ? (
        <p className={styles.actionNote}>
          {`Not included: ${(action.details.skipped ?? []).map((item) => `${item.pc} (${item.reason})`).join(", ")}`}
        </p>
      ) : null}
      {action.kind === "add_time" && action.details.price ? (
        <p className={styles.actionNote}>
          {`Billed later: ₱${action.details.price} added to this session's bill${
            action.details.new_end_time ? ` · new end about ${action.details.new_end_time}` : ""
          }`}
        </p>
      ) : null}
      {skipped.length > 0 && results.length === 0 ? (
        <p className={styles.actionNote}>Offline, will be skipped: {skipped.join(", ")}</p>
      ) : null}
      {state === "pending" ? (
        <div className={styles.actionButtons}>
          <button className={styles.confirmButton} disabled={disabled} onClick={onConfirm} type="button">
            <Check aria-hidden="true" />
            Confirm
          </button>
          <button className={styles.cancelButton} disabled={disabled} onClick={onCancel} type="button">
            <X aria-hidden="true" />
            Cancel
          </button>
          <span className={styles.actionHint}>or say “confirm” / “cancel”</span>
        </div>
      ) : null}
    </div>
  );
}
