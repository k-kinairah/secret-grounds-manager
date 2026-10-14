"use client";

import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  MAX_MESSAGE_LENGTH,
  newConversationId,
  resetAssistantConversation,
  sendAssistantMessage,
  toolLabel
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
type ChatMessage = { id: number; role: "user" | "assistant"; text: string; tools?: string[]; error?: string };
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
      return "No microphone was found. Plug one in, or type your question.";
    case "no-speech":
      return "I didn't hear anything. Hold the button and speak, or type your question.";
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
  const [speakReplies, setSpeakReplies] = useState(true);
  const [conversationId, setConversationId] = useState(newConversationId);
  const recognitionRef = useRef<Recognition | null>(null);
  const transcriptRef = useRef("");
  const nextIdRef = useRef(1);
  const logRef = useRef<HTMLOListElement | null>(null);
  const busy = status === "thinking";

  useEffect(() => {
    logRef.current?.scrollTo({ behavior: "smooth", top: logRef.current.scrollHeight });
  }, [messages, interim]);

  useEffect(() => {
    return () => {
      recognitionRef.current?.abort();
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

  const ask = useCallback(
    async (rawText: string) => {
      const text = rawText.trim().slice(0, MAX_MESSAGE_LENGTH);
      if (!text || busy) {
        return;
      }
      setNotice(null);
      setMessages((current) => [...current, { id: nextIdRef.current++, role: "user", text }]);
      setStatus("thinking");
      const result = await sendAssistantMessage(conversationId, text);
      setMessages((current) => [
        ...current,
        { error: result.error, id: nextIdRef.current++, role: "assistant", text: result.reply, tools: result.tools_used }
      ]);
      if (result.error) {
        setNotice(result.error);
      }
      speak(result.reply);
      if (result.error && !(speakReplies && support.speech)) {
        setStatus("error");
      }
    },
    [busy, conversationId, speak, speakReplies, support.speech]
  );

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
      setInterim(`${finalText}${interimText}`.trim());
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
      const spoken = transcriptRef.current.trim();
      setInterim("");
      if (spoken) {
        void ask(spoken);
      } else {
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
    setConversationId(newConversationId());
    setMessages([]);
    setNotice(null);
    setInterim("");
    setStatus("idle");
  }

  function stopSpeaking() {
    window.speechSynthesis.cancel();
    setStatus("idle");
  }

  const micHint =
    support.mic === "ok"
      ? "Hold to talk, release to send"
      : support.mic === "insecure"
        ? "The mic needs https:// or localhost. Type below instead."
        : "Voice input isn't supported in this browser (try Chrome or Edge). Type below instead.";

  return (
    <section className={styles.console} aria-labelledby="assistant-title">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Read-only · Beta</p>
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
                        Checked: {toolLabel(tool)}
                      </span>
                    ))}
                  </div>
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
              placeholder="Or type a question…"
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
