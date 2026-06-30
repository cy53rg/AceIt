import { useCallback, useEffect, useRef, useState } from "react";
import {
  DaemonSocket,
  cancelQuery,
  engageKillswitch,
  getGlassStatus,
  getSessionSnapshot,
  handleInput,
  healthCheck,
  setFocusMode,
  type WsMessage,
} from "./api/daemon";
import SettingsPanel from "./components/SettingsPanel";
import { useClipboardHighlight, useHighlightHotkey } from "./hooks/useClipboardHighlight";
import { usePushToTalk } from "./hooks/usePushToTalk";

type ChatLine = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
};

type PendingPrompt = {
  id: string;
  title: string;
  body: string;
  approvePayload: Record<string, unknown>;
};

let lineId = 0;
function nextId() {
  lineId += 1;
  return String(lineId);
}

export default function App() {
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState("Connecting to Atlas daemon…");
  const [focusMode, setFocus] = useState(false);
  const [glassLabel, setGlassLabel] = useState("");
  const [lines, setLines] = useState<ChatLine[]>([
    { id: nextId(), role: "system", text: "Atlas Electron shell — brain runs in atlas_daemon." },
  ]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [pending, setPending] = useState<PendingPrompt | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [highlightActive, setHighlightActive] = useState(false);
  const socketRef = useRef<DaemonSocket | null>(null);
  const streamBuf = useRef("");

  const appendLine = useCallback((role: ChatLine["role"], text: string) => {
    const body = (text || "").trim();
    if (!body) return;
    setLines((prev) => [...prev, { id: nextId(), role, text: body }]);
  }, []);

  const onHighlight = useCallback(
    (text: string) => {
      if (text.startsWith("[highlight error]")) {
        appendLine("system", text);
        return;
      }
      appendLine("user", `[highlight] ${text.slice(0, 200)}${text.length > 200 ? "…" : ""}`);
      streamBuf.current = "";
      setStreaming(true);
      setLines((prev) => [
        ...prev.filter((l) => l.id !== "streaming"),
        { id: "streaming", role: "assistant", text: "" },
      ]);
    },
    [appendLine],
  );

  useClipboardHighlight(highlightActive, onHighlight);
  useHighlightHotkey(() => setHighlightActive((v) => !v));

  const voice = usePushToTalk(connected);

  const refreshMeta = useCallback(async () => {
    try {
      const snap = await getSessionSnapshot();
      setFocus(Boolean(snap.focus_mode));
      setStatus(snap.summary || `Mode: ${snap.mode || "ACTIVE"}`);
      const glass = await getGlassStatus();
      if (glass.glass?.active) {
        const dur = Math.floor((glass.glass.duration_s || 0) / 60);
        setGlassLabel(`Glass · ${glass.glass.profile} · ${dur}m`);
      } else {
        setGlassLabel("");
      }
    } catch {
      /* snapshot optional */
    }
  }, []);

  useEffect(() => {
    const socket = new DaemonSocket();
    socketRef.current = socket;

    const onMsg = (msg: WsMessage) => {
      if (msg.type === "chunk") {
        streamBuf.current += msg.text;
        setStreaming(true);
        setLines((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === "assistant" && last.id === "streaming") {
            return [
              ...prev.slice(0, -1),
              { ...last, text: streamBuf.current },
            ];
          }
          return [
            ...prev,
            { id: "streaming", role: "assistant", text: streamBuf.current },
          ];
        });
      } else if (msg.type === "complete") {
        streamBuf.current = "";
        setStreaming(false);
        setLines((prev) =>
          prev.map((l) => (l.id === "streaming" ? { ...l, id: nextId() } : l)),
        );
        void refreshMeta();
      } else if (msg.type === "error") {
        streamBuf.current = "";
        setStreaming(false);
        appendLine("system", `Error: ${msg.text}`);
      } else if (msg.type === "state_event") {
        const et = msg.event_type;
        const p = msg.payload || {};
        if (et === "query_started") {
          setStatus(`Processing (${String(p.source || "user")})…`);
        } else if (et === "glass_changed" || et === "focus_changed") {
          void refreshMeta();
        } else if (et === "glass_question_detected") {
          appendLine("system", `Question detected (${String(p.source)}): ${String(p.question || "").slice(0, 80)}…`);
        } else if (et === "killswitch") {
          appendLine("system", "Killswitch engaged — all actions stopped.");
          setStreaming(false);
        } else if (et === "provider_fallback") {
          appendLine("system", `Using fallback provider: ${String(p.provider)}`);
        } else if (typeof p.text === "string" && p.text) {
          setStatus(p.text);
        }
      } else if (msg.type === "permission_request" && msg.id) {
        setPending({
          id: msg.id,
          title: "Permission required",
          body: `${msg.action}\n${msg.path}`,
          approvePayload: { approved: true, type: "permission_response" },
        });
      } else if (msg.type === "typed_confirm_request" && msg.id) {
        setPending({
          id: msg.id,
          title: "Confirm action",
          body: `${msg.reason}\nType: ${msg.confirm_phrase}`,
          approvePayload: { approved: true, type: "typed_confirm_response" },
        });
      } else if (msg.type === "safety_prompt" && msg.id) {
        setPending({
          id: msg.id,
          title: "Safety check",
          body: msg.message,
          approvePayload: { approved: true, type: "safety_response" },
        });
      } else if (msg.type === "task_confirm" && msg.id) {
        setPending({
          id: msg.id,
          title: "Run task?",
          body: msg.message,
          approvePayload: { approved: true, type: "task_confirm_response" },
        });
      }
    };

    const off = socket.onMessage(onMsg);
    socket.connect();

    (async () => {
      const ok = await healthCheck();
      setConnected(ok);
      if (!ok) {
        setStatus("Daemon offline — run: python -m atlas_daemon");
        return;
      }
      setStatus("Connected");
      await refreshMeta();
    })();

    const poll = window.setInterval(refreshMeta, 15000);
    return () => {
      clearInterval(poll);
      off();
      socket.disconnect();
    };
  }, [appendLine, refreshMeta]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || !connected) return;
    setDraft("");
    appendLine("user", text);
    streamBuf.current = "";
    setStreaming(true);
    setLines((prev) => [
      ...prev.filter((l) => l.id !== "streaming"),
      { id: "streaming", role: "assistant", text: "" },
    ]);
    try {
      await handleInput(text, "user");
    } catch (err) {
      setStreaming(false);
      appendLine("system", String(err));
    }
  };

  const toggleFocus = async () => {
    const next = !focusMode;
    try {
      await setFocusMode(next);
      setFocus(next);
      appendLine("system", next ? "Focus / Glass mode enabled." : "Focus mode off.");
      await refreshMeta();
    } catch (err) {
      appendLine("system", String(err));
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void submit();
    }
  };

  const respondPending = (approved: boolean) => {
    if (!pending || !socketRef.current) return;
    socketRef.current.respond(pending.id, {
      ...pending.approvePayload,
      approved,
    });
    setPending(null);
  };

  return (
    <div className="app">
      <header className="toolbar">
        <h1>Atlas</h1>
        {glassLabel ? <span className="badge on">{glassLabel}</span> : null}
        {highlightActive ? <span className="badge on">Highlight</span> : null}
        {voice.listening ? <span className="badge on">Listening</span> : null}
        <button type="button" onClick={() => setSettingsOpen(true)}>Settings</button>
        <button
          type="button"
          className={highlightActive ? "active" : ""}
          onClick={() => setHighlightActive((v) => !v)}
          title="Ctrl+Shift+H"
        >
          {highlightActive ? "Highlight on" : "Highlight"}
        </button>
        <button
          type="button"
          className={focusMode ? "active" : ""}
          onClick={() => void toggleFocus()}
        >
          {focusMode ? "Focus on" : "Focus"}
        </button>
        {voice.supported ? (
          <button
            type="button"
            className={voice.listening ? "active" : ""}
            onMouseDown={() => voice.start()}
            onMouseUp={() => voice.stop()}
            onMouseLeave={() => voice.stop()}
          >
            Hold to talk
          </button>
        ) : null}
        <button type="button" onClick={() => void cancelQuery()}>
          Cancel
        </button>
        <button type="button" className="danger" onClick={() => void engageKillswitch()}>
          Stop all
        </button>
      </header>

      <div className={`status-bar ${connected ? "connected" : "error"}`}>{status}</div>

      <main className="chat">
        {lines.map((line) => (
          <div key={line.id} className={`bubble ${line.role}`}>
            {line.text}
          </div>
        ))}
      </main>

      <footer className="composer">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask Atlas… (Ctrl+Enter to send)"
          disabled={!connected || streaming}
          rows={2}
        />
        <button type="button" onClick={() => void submit()} disabled={!connected || !draft.trim()}>
          Send
        </button>
      </footer>

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />

      {pending ? (
        <div className="modal-backdrop">
          <div className="modal">
            <h3>{pending.title}</h3>
            <p>{pending.body}</p>
            <div className="modal-actions">
              <button type="button" onClick={() => respondPending(false)}>
                Deny
              </button>
              <button type="button" onClick={() => respondPending(true)}>
                Allow
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
