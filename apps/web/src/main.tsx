import { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Project = { id: string; name: string; root_path: string };
type Session = { id: string; thread_id: string; project_id?: string; title?: string };
type Block = { type: string; text?: string; content?: string; filename?: string };
type StoredMessage = { id: string; ordinal: number; message: { role: string; content: Block[] } };
type Snapshot = { session: Session; messages: StoredMessage[] };
type Workflow = { id: string; status: string; issue: { description: string }; evidence: unknown[]; events: { sequence: number; type: string }[]; pr_document?: { title: string; review_status: string } };
type LoginResponse = { access_token: string; user_id: string };

const api = async <T,>(path: string, init?: RequestInit, token?: string): Promise<T> => {
  const bearer = token || localStorage.getItem("codeassist_access_token") || "";
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}), ...init?.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => ({ detail: response.statusText }))).detail);
  return response.json() as Promise<T>;
};

function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedSession, setSelectedSession] = useState<string>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [message, setMessage] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projectPath, setProjectPath] = useState("");
  const [sessionTitle, setSessionTitle] = useState("");
  const [eventLog, setEventLog] = useState<string[]>([]);
  const [error, setError] = useState<string>();
  const [token, setToken] = useState(() => localStorage.getItem("codeassist_access_token") || "");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");

  const activeSession = useMemo(() => sessions.find((item) => item.id === selectedSession), [sessions, selectedSession]);
  const refresh = async () => {
    try {
      const [nextProjects, nextSessions, nextWorkflows] = await Promise.all([
        api<Project[]>("/api/v1/projects"), api<Session[]>("/api/v1/sessions"), api<Workflow[]>("/api/v1/workflows"),
      ]);
      setProjects(nextProjects); setSessions(nextSessions); setWorkflows(nextWorkflows);
      if (!selectedSession && nextSessions[0]) setSelectedSession(nextSessions[0].id);
      setError(undefined);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "无法连接 API"); }
  };
  useEffect(() => { if (token) void refresh(); }, [token]);
  useEffect(() => { if (selectedSession) void api<Snapshot>(`/api/v1/sessions/${selectedSession}`).then(setSnapshot).catch((cause: Error) => setError(cause.message)); }, [selectedSession]);

  const registerProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!projectName.trim() || !projectPath.trim()) return;
    try {
      await api<Project>("/api/v1/projects", {
        method: "POST",
        body: JSON.stringify({ name: projectName.trim(), root_path: projectPath.trim() }),
      });
      setProjectName(""); setProjectPath(""); await refresh();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "项目登记失败"); }
  };

  const login = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const result = await api<LoginResponse>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      localStorage.setItem("codeassist_access_token", result.access_token);
      setToken(result.access_token); setPassword(""); setError(undefined);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "登录失败"); }
  };

  const createSession = async (event: FormEvent) => {
    event.preventDefault();
    if (!sessionTitle.trim()) return;
    try {
      const created = await api<Session>("/api/v1/sessions", {
        method: "POST",
        body: JSON.stringify({
          thread_id: crypto.randomUUID(),
          title: sessionTitle.trim(),
          project_id: projects[0]?.id,
        }),
      });
      setSessionTitle(""); setSessions((current) => [created, ...current]); setSelectedSession(created.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "会话创建失败"); }
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    if (!activeSession || !message.trim()) return;
    const text = message.trim(); setMessage(""); setEventLog([]);
    try {
      const url = new URL(`/api/v1/sessions/${activeSession.id}/events`, window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.searchParams.set("access_token", token);
      const socket = new WebSocket(url);
      socket.onopen = () => socket.send(JSON.stringify({ provider: "fake", model: "fake-model", message: { role: "user", content: [{ type: "text", text }] } }));
      socket.onmessage = (item) => {
        const data = JSON.parse(item.data) as { sequence?: number; type: string; data?: { text?: string } };
        setEventLog((current) => [...current, `${data.sequence ?? ""} ${data.type}${data.data?.text ? ` · ${data.data.text}` : ""}`]);
        if (["run.completed", "run.failed", "run.cancelled"].includes(data.type)) { socket.close(); void api<Snapshot>(`/api/v1/sessions/${activeSession.id}`).then(setSnapshot); }
      };
      socket.onerror = () => setError("会话事件连接失败");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "发送失败"); }
  };

  if (!token) return <main className="login-page"><section className="login-card"><h1>CodeAssist</h1><p>使用已授权账号登录。</p><form className="compact-form" onSubmit={login}><input aria-label="账号" value={username} onChange={(event) => setUsername(event.target.value)} /><input aria-label="密码" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /><button type="submit">登录</button></form>{error && <p className="error">{error}</p>}</section></main>;

  return <main>
    <header><div><strong>CodeAssist</strong><span>研发工作台</span></div><button onClick={() => void refresh()}>刷新</button></header>
    {error && <p className="error">{error}</p>}
    <section className="layout">
      <aside>
        <h2>项目</h2>
        <form className="compact-form" onSubmit={registerProject}>
          <input aria-label="项目名称" value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="项目名称" />
          <input aria-label="项目路径" value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="绝对路径或工作区相对路径" />
          <button type="submit">登记项目</button>
        </form>
        {projects.map((item) => <div className="item" key={item.id}><b>{item.name}</b><small>{item.root_path}</small></div>)}
        <h2>会话</h2>
        <form className="compact-form" onSubmit={createSession}>
          <input aria-label="会话标题" value={sessionTitle} onChange={(event) => setSessionTitle(event.target.value)} placeholder="新会话标题" />
          <button type="submit">新建会话</button>
        </form>
        {sessions.map((item) => <button className={item.id === selectedSession ? "selected" : "item"} key={item.id} onClick={() => setSelectedSession(item.id)}>{item.title || item.thread_id}</button>)}
      </aside>
      <section className="conversation"><h1>{activeSession?.title || "选择一个会话"}</h1><div className="messages">{snapshot?.messages.map((item) => <article className={item.message.role} key={item.id}><label>{item.message.role}</label>{item.message.content.map((block, index) => <p key={index}>{block.text || block.content || `[${block.type}]`}</p>)}</article>)}</div><form onSubmit={send}><textarea aria-label="消息" value={message} onChange={(event) => setMessage(event.target.value)} placeholder="向 Agent 描述任务…" /><button type="submit" disabled={!activeSession}>发送</button></form>{eventLog.length > 0 && <details open><summary>实时事件</summary><pre>{eventLog.join("\n")}</pre></details>}</section>
      <aside className="workflow"><h2>研发工作流</h2>{workflows.map((item) => <article className="item" key={item.id}><b>{item.issue.description}</b><small>{item.status} · {item.evidence.length} 项证据</small><ol>{item.events.map((event) => <li key={event.sequence}>{event.type}</li>)}</ol>{item.pr_document && <small>PR: {item.pr_document.title} ({item.pr_document.review_status})</small>}</article>)}<h2>可用模型</h2><p className="item">本地演示：fake / fake-model<br />配置凭据后可通过 API 使用 OpenAI 或 Anthropic。</p></aside>
    </section>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
