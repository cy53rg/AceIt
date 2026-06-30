const DEFAULT_BASE = "http://127.0.0.1:17847";

export function daemonBaseUrl(): string {
  const fromEnv = (import.meta.env.VITE_ATLAS_DAEMON_URL as string | undefined)?.trim();
  return (fromEnv || DEFAULT_BASE).replace(/\/$/, "");
}

export function daemonWsUrl(): string {
  return daemonBaseUrl().replace(/^http/, "ws") + "/ws";
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${daemonBaseUrl()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const text = await res.text();
  if (!text) return {} as T;
  return JSON.parse(text) as T;
}

export async function healthCheck(): Promise<boolean> {
  try {
    const data = await request<{ ok?: boolean }>("/health");
    return Boolean(data.ok);
  } catch {
    return false;
  }
}

export async function getSessionSnapshot(): Promise<SessionSnapshot> {
  return request<SessionSnapshot>("/api/session_snapshot");
}

export async function handleInput(text: string, source = "user"): Promise<void> {
  await request("/api/handle_input", {
    method: "POST",
    body: JSON.stringify({ text, source }),
  });
}

export async function cancelQuery(): Promise<void> {
  await request("/api/cancel", { method: "POST", body: "{}" });
}

export async function engageKillswitch(): Promise<void> {
  await request("/api/killswitch", { method: "POST", body: "{}" });
}

export async function setFocusMode(enabled: boolean): Promise<void> {
  await request("/api/set_focus_mode", {
    method: "POST",
    body: JSON.stringify({ enabled }),
  });
}

export async function getGlassStatus(): Promise<GlassStatus> {
  return request<GlassStatus>("/api/glass/status");
}

export async function invoke(method: string, args: unknown[] = [], kwargs: Record<string, unknown> = {}): Promise<unknown> {
  const data = await request<{ result?: unknown; ok?: boolean }>("/api/invoke", {
    method: "POST",
    body: JSON.stringify({ method, args, kwargs }),
  });
  return data.result;
}

export async function getUserPrefs(): Promise<Record<string, unknown>> {
  const result = await invoke("get_user_prefs");
  return (result as Record<string, unknown>) || {};
}

export async function setUserPref(key: string, value: unknown): Promise<void> {
  await invoke("set_user_pref", [key, value]);
}

export async function listConnectors(): Promise<ConnectorInfo[]> {
  const data = await request<{ connectors?: ConnectorInfo[] }>("/api/connectors");
  return data.connectors || [];
}

export async function connectConnector(id: string): Promise<string> {
  const data = await request<{ message?: string; ok?: boolean }>(
    `/api/connectors/${encodeURIComponent(id)}/connect`,
    { method: "POST", body: "{}" },
  );
  return data.message || (data.ok ? "Connected" : "Failed");
}

export async function disconnectConnector(id: string): Promise<string> {
  const data = await request<{ message?: string }>(
    `/api/connectors/${encodeURIComponent(id)}/disconnect`,
    { method: "POST", body: "{}" },
  );
  return data.message || "Disconnected";
}

export async function getFileIndexRoots(): Promise<string[]> {
  const data = await request<{ roots?: string[] }>("/api/files/index/roots");
  return data.roots || [];
}

export async function setFileIndexRoots(roots: string[]): Promise<void> {
  await request("/api/files/index/roots", {
    method: "POST",
    body: JSON.stringify({ roots }),
  });
}

export type AuditEntry = {
  id?: number;
  category?: string;
  summary?: string;
  created?: number;
  detail?: Record<string, unknown>;
};

export async function listAudit(limit = 50): Promise<AuditEntry[]> {
  const data = await request<{ entries?: AuditEntry[] }>(
    `/api/audit?limit=${encodeURIComponent(String(limit))}`,
  );
  return data.entries || [];
}

export async function getWeeklyRecap(days = 7): Promise<string> {
  const data = await request<{ recap?: string }>(`/api/recap?days=${days}`);
  return String(data.recap || "");
}

export type ConnectorInfo = {
  id: string;
  display_name: string;
  connected: boolean;
  boundary_text?: string;
};

export type SessionSnapshot = {
  is_active?: boolean;
  response_style?: string;
  turn_count?: number;
  summary?: string;
  user_id?: number;
  safety_mode?: string;
  focus_mode?: boolean;
  is_learning?: boolean;
  mode?: string;
};

export type GlassStatus = {
  glass?: {
    active?: boolean;
    profile?: string;
    chunk_count?: number;
    duration_s?: number;
  };
  focus_mode?: boolean;
};

export type WsMessage =
  | { type: "chunk"; text: string }
  | { type: "complete"; text: string }
  | { type: "error"; text: string }
  | { type: "state_event"; event_type: string; payload: Record<string, unknown> }
  | { type: "permission_request"; action: string; path: string; id?: string }
  | { type: "typed_confirm_request"; path: string; reason: string; confirm_phrase: string; id?: string }
  | { type: "safety_prompt"; message: string; id?: string }
  | { type: "task_confirm"; message: string; id?: string };

export type WsListener = (msg: WsMessage) => void;

export class DaemonSocket {
  private ws: WebSocket | null = null;
  private listeners: WsListener[] = [];
  private reconnectTimer: number | null = null;

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket(daemonWsUrl());
    this.ws = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(String(ev.data)) as WsMessage;
        for (const fn of this.listeners) fn(msg);
      } catch {
        /* ignore */
      }
    };
    ws.onclose = () => {
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      ws.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 1500);
  }

  onMessage(fn: WsListener) {
    this.listeners.push(fn);
    return () => {
      this.listeners = this.listeners.filter((x) => x !== fn);
    };
  }

  respond(reqId: string, payload: Record<string, unknown>) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ ...payload, id: reqId }));
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }
}
