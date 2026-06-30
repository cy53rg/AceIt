import { useCallback, useEffect, useState } from "react";
import {
  connectConnector,
  disconnectConnector,
  getFileIndexRoots,
  getUserPrefs,
  getWeeklyRecap,
  listAudit,
  listConnectors,
  setFileIndexRoots,
  setUserPref,
  type AuditEntry,
  type ConnectorInfo,
} from "../api/daemon";

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function SettingsPanel({ open, onClose }: Props) {
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [brief, setBrief] = useState("");
  const [autoAnswer, setAutoAnswer] = useState(true);
  const [screenWatch, setScreenWatch] = useState(true);
  const [safetyMode, setSafetyMode] = useState("off");
  const [indexRoots, setIndexRoots] = useState("");
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [recap, setRecap] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [prefs, conns, roots, auditRows] = await Promise.all([
        getUserPrefs(),
        listConnectors(),
        getFileIndexRoots(),
        listAudit(40),
      ]);
      setBrief(String(prefs.glass_interview_brief || ""));
      setAutoAnswer(prefs.glass_auto_answer !== false);
      setScreenWatch(prefs.glass_screen_watch !== false);
      setSafetyMode(String(prefs.safety_mode || "off"));
      setConnectors(conns);
      setIndexRoots(roots.join("\n"));
      setAudit(auditRows);
      setRecap("");
      setMsg("");
    } catch (err) {
      setMsg(String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const saveGlass = async () => {
    setBusy(true);
    try {
      await setUserPref("glass_interview_brief", brief.trim());
      await setUserPref("glass_auto_answer", autoAnswer);
      await setUserPref("glass_screen_watch", screenWatch);
      await setUserPref("safety_mode", safetyMode);
      const roots = indexRoots
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      if (roots.length) await setFileIndexRoots(roots);
      setMsg("Settings saved.");
    } catch (err) {
      setMsg(String(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleConnector = async (c: ConnectorInfo) => {
    setBusy(true);
    try {
      const text = c.connected
        ? await disconnectConnector(c.id)
        : await connectConnector(c.id);
      setMsg(text);
      await load();
    } catch (err) {
      setMsg(String(err));
    } finally {
      setBusy(false);
    }
  };

  const loadRecap = async () => {
    setBusy(true);
    try {
      setRecap(await getWeeklyRecap(7));
      setMsg("Weekly recap loaded.");
    } catch (err) {
      setMsg(String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!open) return null;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header className="drawer-header">
          <h2>Settings</h2>
          <button type="button" onClick={onClose}>Close</button>
        </header>

        <section className="drawer-section">
          <h3>Interview (Glass)</h3>
          <label className="field-label">How Atlas should answer</label>
          <textarea
            className="field-textarea"
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            placeholder="e.g. Senior backend role — concise, first-person, STAR for behavioral."
            rows={4}
          />
          <label className="field-check">
            <input
              type="checkbox"
              checked={autoAnswer}
              onChange={(e) => setAutoAnswer(e.target.checked)}
            />
            Auto-answer on heard or detected questions
          </label>
          <label className="field-check">
            <input
              type="checkbox"
              checked={screenWatch}
              onChange={(e) => setScreenWatch(e.target.checked)}
            />
            Watch screen for written questions
          </label>
        </section>

        <section className="drawer-section">
          <h3>Safety</h3>
          <select
            className="field-select"
            value={safetyMode}
            onChange={(e) => setSafetyMode(e.target.value)}
          >
            <option value="off">Auto (minimal prompts)</option>
            <option value="always">Confirm each action</option>
            <option value="trusted">Trusted apps only</option>
          </select>
        </section>

        <section className="drawer-section">
          <h3>File index roots</h3>
          <textarea
            className="field-textarea"
            value={indexRoots}
            onChange={(e) => setIndexRoots(e.target.value)}
            placeholder="One folder per line"
            rows={3}
          />
        </section>

        <section className="drawer-section">
          <h3>Connectors</h3>
          {connectors.length === 0 ? (
            <p className="muted">No connectors loaded.</p>
          ) : (
            <ul className="connector-list">
              {connectors.map((c) => (
                <li key={c.id}>
                  <div>
                    <strong>{c.display_name}</strong>
                    <span className={c.connected ? "pill on" : "pill"}>
                      {c.connected ? "Connected" : "Not connected"}
                    </span>
                  </div>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void toggleConnector(c)}
                  >
                    {c.connected ? "Disconnect" : "Connect"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="drawer-section">
          <h3>Activity log</h3>
          <p className="muted">What Atlas did while you were away.</p>
          <button type="button" disabled={busy} onClick={() => void load()}>
            Refresh log
          </button>
          <ul className="connector-list audit-list">
            {audit.length === 0 ? (
              <li className="muted">No activity yet.</li>
            ) : (
              audit.slice(0, 20).map((row, i) => (
                <li key={`${row.id ?? i}-${row.created ?? 0}`}>
                  <span className="muted">
                    {row.created
                      ? new Date(row.created * 1000).toLocaleString()
                      : ""}
                  </span>
                  {" "}
                  [{row.category || "general"}] {row.summary}
                </li>
              ))
            )}
          </ul>
          <button type="button" disabled={busy} onClick={() => void loadRecap()}>
            Weekly recap
          </button>
          {recap ? <pre className="recap-block">{recap}</pre> : null}
        </section>

        <footer className="drawer-footer">
          {msg ? <p className="drawer-msg">{msg}</p> : null}
          <button type="button" className="primary" disabled={busy} onClick={() => void saveGlass()}>
            Save settings
          </button>
        </footer>
      </aside>
    </div>
  );
}
