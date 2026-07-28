import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Project = { id: string; name: string; root_path: string };
type Session = { id: string; thread_id: string; title?: string };
type Block = { type: string; text?: string; content?: string };
type Snapshot = { messages: { id: string; message: { role: string; content: Block[] } }[] };
type LoginResponse = { access_token: string; user_id: string };
type RuntimeSettings = {
  idle_shutdown_minutes: number; model_provider: string; model_name: string;
  users: { id: string; display_name: string }[];
};

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const token = localStorage.getItem("devpilot_access_token") || "";
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => ({ detail: response.statusText }))).detail);
  return response.json() as Promise<T>;
};

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("devpilot_access_token") || "");
  const [currentUser, setCurrentUser] = useState(() => localStorage.getItem("devpilot_user_id") || "");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSession, setSelectedSession] = useState<string>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [message, setMessage] = useState("");
  const [settings, setSettings] = useState<RuntimeSettings>();
  const [idleMinutes, setIdleMinutes] = useState("5");
  const [provider, setProvider] = useState("fake");
  const [modelName, setModelName] = useState("fake-model");
  const [newUserId, setNewUserId] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [error, setError] = useState<string>();
  const activeSession = useMemo(() => sessions.find((item) => item.id === selectedSession), [sessions, selectedSession]);

  const refresh = async () => {
    try {
      const [nextProjects, nextSessions] = await Promise.all([api<Project[]>("/api/v1/projects"), api<Session[]>("/api/v1/sessions")]);
      setProjects(nextProjects); setSessions(nextSessions);
      if (!selectedSession && nextSessions[0]) setSelectedSession(nextSessions[0].id);
      if (currentUser === "admin") {
        const next = await api<RuntimeSettings>("/api/v1/settings");
        setSettings(next); setIdleMinutes(String(next.idle_shutdown_minutes)); setProvider(next.model_provider); setModelName(next.model_name);
      }
      setError(undefined);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to connect to API"); }
  };
  useEffect(() => { if (token) void refresh(); }, [token, currentUser]);
  useEffect(() => { if (selectedSession) void api<Snapshot>(`/api/v1/sessions/${selectedSession}`).then(setSnapshot).catch((cause: Error) => setError(cause.message)); }, [selectedSession]);

  const login = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const result = await api<LoginResponse>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      localStorage.setItem("devpilot_access_token", result.access_token); localStorage.setItem("devpilot_user_id", result.user_id); setToken(result.access_token); setCurrentUser(result.user_id); setPassword("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Login failed"); }
  };
  const createSession = async () => {
    try { const session = await api<Session>("/api/v1/sessions", { method: "POST", body: JSON.stringify({ thread_id: crypto.randomUUID(), title: "New session", project_id: projects[0]?.id }) }); setSessions((items) => [session, ...items]); setSelectedSession(session.id); } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create session"); }
  };
  const send = (event: FormEvent) => {
    event.preventDefault(); if (!activeSession || !message.trim()) return;
    const text = message.trim(); setMessage("");
    const url = new URL(`/api/v1/sessions/${activeSession.id}/events`, window.location.href); url.protocol = url.protocol === "https:" ? "wss:" : "ws:"; url.searchParams.set("access_token", token);
    const socket = new WebSocket(url);
    socket.onopen = () => socket.send(JSON.stringify({ message: { role: "user", content: [{ type: "text", text }] } }));
    socket.onmessage = (item) => { const eventData = JSON.parse(item.data) as { type: string }; if (["run.completed", "run.failed", "run.cancelled"].includes(eventData.type)) { socket.close(); void api<Snapshot>(`/api/v1/sessions/${activeSession.id}`).then(setSnapshot); } };
    socket.onerror = () => setError("Conversation connection failed");
  };
  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    try { const updated = await api<RuntimeSettings>("/api/v1/settings", { method: "PUT", body: JSON.stringify({ idle_shutdown_minutes: Number(idleMinutes), model_provider: provider, model_name: modelName }) }); setSettings(updated); } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to save settings"); }
  };
  const addUser = async (event: FormEvent) => {
    event.preventDefault();
    try { await api("/api/v1/settings/users", { method: "POST", body: JSON.stringify({ id: newUserId, display_name: newUserName, password: newUserPassword }) }); setNewUserId(""); setNewUserName(""); setNewUserPassword(""); await refresh(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to add user"); }
  };

  if (!token) return <main className="login-page"><section className="login-card"><h1>DevPilot</h1><form className="compact-form" onSubmit={login}><input aria-label="Username" value={username} onChange={(event) => setUsername(event.target.value)} /><input aria-label="Password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /><button>Sign in</button></form>{error && <p className="error">{error}</p>}</section></main>;
  return <main><header><div><strong>DevPilot</strong><span>Development workbench</span></div><button onClick={() => void refresh()}>Refresh</button></header>{error && <p className="error">{error}</p>}<section className="layout"><aside><h2>Projects</h2>{projects.map((project) => <p className="item" key={project.id}>{project.name}</p>)}<h2>Sessions</h2><button onClick={() => void createSession()}>New session</button>{sessions.map((session) => <button className={session.id === selectedSession ? "selected" : "item"} key={session.id} onClick={() => setSelectedSession(session.id)}>{session.title || session.thread_id}</button>)}</aside><section className="conversation"><h1>{activeSession?.title || "Select a session"}</h1><div className="messages">{snapshot?.messages.map((item) => <article className={item.message.role} key={item.id}><label>{item.message.role}</label>{item.message.content.map((block, index) => <p key={index}>{block.text || block.content || `[${block.type}]`}</p>)}</article>)}</div><form onSubmit={send}><textarea aria-label="Message" value={message} onChange={(event) => setMessage(event.target.value)} /><button disabled={!activeSession}>Send</button></form></section><aside className="workflow"><h2>Model</h2><p className="item">{settings ? `${settings.model_provider} / ${settings.model_name}` : "Default model"}</p>{currentUser === "admin" && <><h2>Local settings</h2><form className="compact-form" onSubmit={saveSettings}><input aria-label="Idle shutdown minutes" type="number" min="1" max="1440" value={idleMinutes} onChange={(event) => setIdleMinutes(event.target.value)} /><input aria-label="Model provider" value={provider} onChange={(event) => setProvider(event.target.value)} placeholder="fake, openai, anthropic, ollama" /><input aria-label="Model name" value={modelName} onChange={(event) => setModelName(event.target.value)} /><button>Save settings</button></form><h2>Add local user</h2><form className="compact-form" onSubmit={addUser}><input aria-label="User ID" value={newUserId} onChange={(event) => setNewUserId(event.target.value)} /><input aria-label="Display name" value={newUserName} onChange={(event) => setNewUserName(event.target.value)} /><input aria-label="Password" type="password" value={newUserPassword} onChange={(event) => setNewUserPassword(event.target.value)} /><button>Add user</button></form>{settings?.users.map((user) => <small className="item" key={user.id}>{user.display_name} ({user.id})</small>)}</>}</aside></section></main>;
}

createRoot(document.getElementById("root")!).render(<App />);
