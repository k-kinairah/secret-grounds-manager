"use client";

import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  type ActionOutcome,
  type PendingAction,
  MAX_MESSAGE_LENGTH,
  cancelAssistantAction,
  confirmAssistantAction,
  matchSpokenDecision,
  newConversationId,
  resetAssistantConversation,
  sendAssistantMessage,
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
type ActionState = "pending" | "running" | "done" | "failed" | "cancelled" | "expired";
type ChatMessage = {
  id: number;
  role: "user" | "assistant";
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

const TARGET_ICONS: Record<string, string> = { done: "✓", failed: "✕", offline: "○", received: "↯", skipped: "–" };
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
  pending: "Waiting for you",
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

  function addAssistantMessage(message: Omit<ChatMessage, "id" | "role">): number {
    const id = nextIdRef.current++;
    setMessages((current) => [...current, { ...message, id, role: "assistant" }]);
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
      setMessages((list) => [...list, { id: nextIdRef.current++, role: "user", text: spokenOrTyped }]);
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
    setMessages((current) => [...current, { id: nextIdRef.current++, role: "user", text }]);
    setStatus("thinking");
    const result = await sendAssistantMessage(conversationId, text);
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

  return (
    <section className={styles.console} aria-labelledby="assistant-title">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Beta · Actions need your confirmation</p>
          <h1 id="assistant-title">Shift Assistant</h1>
          <p>Ask about today&apos;s sales, active sessions, free PCs, members or the current shift.</p>
        </div>
        <div className={styles.headerActions}>
          <label className={styles.toggle}>
            <input
              checked={speakReplies}
              disabled={!support.speech}
              onChange={(event) => setSpeakReplies(event.target.checked)}
              type="checkbox"
            />
            Speak replies
          </label>
          <button className={styles.secondaryButton} onClick={handleNewConversation} type="button">
            New conversation
          </button>
        </div>
      </header>

      {handover.enabled && (handover.notes.length > 0 || handoverError) ? (
        <HandoverPanel error={handoverError} notes={handover.notes} onDone={(id) => void markNoteDone(id)} />
      ) : null}

      <div className={styles.panel}>
        <ol aria-live="polite" className={styles.log} ref={logRef}>
          {messages.length === 0 && !interim ? (
            <li className={styles.empty}>
              Try: &ldquo;How much have we made today?&rdquo; or &ldquo;Which PCs are free?&rdquo;
            </li>
          ) : null}
          {messages.map((message) => (
            <li className={message.role === "user" ? styles.userRow : styles.assistantRow} key={message.id}>
              <div className={message.role === "user" ? styles.userBubble : styles.assistantBubble}>
                <span className={styles.speaker}>{message.role === "user" ? "You" : "Assistant"}</span>
                <p>{message.text}</p>
                {message.tools && message.tools.length > 0 ? (
                  <div className={styles.chips}>
                    {message.tools.map((tool) => (
                      <span className={styles.chip} key={tool}>
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
          <div className={styles.statusRow}>
            <span className={`${styles.statusDot} ${styles[`status_${status}`]}`} aria-hidden="true" />
            <span aria-live="polite" className={styles.statusLabel}>
              {STATUS_LABELS[status]}
            </span>
            {status === "speaking" ? (
              <button className={styles.linkButton} onClick={stopSpeaking} type="button">
                Stop
              </button>
            ) : null}
          </div>
          {heard ? (
            <p aria-live="polite" className={styles.heard}>
              <span className={styles.heardLabel}>{heard.live ? "Hearing" : "Heard"}:</span>{" "}
              {heard.text ? `“${heard.text}”` : <span className={styles.heardWaiting}>(waiting for speech…)</span>}
            </p>
          ) : null}
          {notice ? (
            <div className={styles.notice} role="alert">
              {notice}
            </div>
          ) : null}

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
            <MicIcon />
          </button>
          <p className={styles.micHint}>{micHint}</p>

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
    <section aria-labelledby="handover-title" className={styles.handover}>
      <div className={styles.handoverHeader}>
        <h2 id="handover-title">Handover notes</h2>
        <span>{notes.length} open</span>
      </div>
      {error ? <p className={styles.handoverError}>{error}</p> : null}
      <ul className={styles.handoverList}>
        {notes.map((note) => (
          <li key={note.note_id}>
            <div>
              <p>{note.text}</p>
              <span>{`${note.author} · ${note.age}`}</span>
            </div>
            <button className={styles.secondaryButton} onClick={() => onDone(note.note_id)} type="button">
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
    <div className={`${styles.actionCard} ${styles[`action_${state}`]}`} role="group" aria-label="Prepared action">
      <div className={styles.actionHeader}>
        <span className={styles.actionKind}>{ACTION_KIND_LABELS[action.kind] ?? "Action"}</span>
        <span className={styles.actionState}>{ACTION_STATE_LABELS[state]}</span>
      </div>
      <p className={styles.actionSummary}>{action.summary}</p>
      {results.length > 0 ? (
        <div className={styles.chips}>
          {results.map((result) => (
            <span
              className={`${styles.targetChip} ${styles[`target_${result.status}`]}`}
              key={result.pc}
              title={result.detail ?? undefined}
            >
              {TARGET_ICONS[result.status] ?? "•"} {result.pc}
            </span>
          ))}
        </div>
      ) : (
        <div className={styles.chips}>
          {action.targets.map((target) => (
            <span className={styles.targetChip} key={target}>
              {target}
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
            Confirm
          </button>
          <button className={styles.cancelButton} disabled={disabled} onClick={onCancel} type="button">
            Cancel
          </button>
        </div>
      ) : null}
    </div>
  );
}

function MicIcon() {
  return (
    <svg aria-hidden="true" fill="none" viewBox="0 0 24 24">
      <path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z" />
      <path d="M5 11a7 7 0 0 0 14 0" />
      <path d="M12 18v3" />
      <path d="M8.5 21h7" />
    </svg>
  );
}
