import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Project = { id: string; name: string; root_path: string };
type Session = { id: string; thread_id: string; title?: string; project_id?: string | null; created_at?: string; updated_at?: string };
type Block = { type: string; text?: string; content?: string };
type StoredMessage = { id: string; message: { role: string; content: Block[] } };
type Snapshot = { session?: Session; messages: StoredMessage[]; summary?: { summary: string } | null };
type LoginResponse = { access_token: string; user_id: string };
type ModelTarget = { endpoint_id: string; model: string };
type ModelEndpoint = {
  id: string;
  name: string;
  provider: "fake" | "openai" | "anthropic" | "coding_plan" | "ollama";
  base_url: string | null;
  effective_base_url: string | null;
  api_key?: string;
  api_key_configured: boolean;
  api_key_source: string;
  clear_api_key?: boolean;
  models: string[];
  effective_models: string[];
  enabled: boolean;
};
type ModelPolicy = { mode: "manual" | "auto"; allowed_models: ModelTarget[] };
type RuntimeSettings = {
  idle_shutdown_minutes: number;
  model_provider: string;
  model_name: string;
  model_endpoints: ModelEndpoint[];
  default_model: ModelTarget;
  agent_model_policy: ModelPolicy;
  users: { id: string; display_name: string }[];
};
type ModelOptions = {
  models: (ModelTarget & { endpoint_name: string })[];
  default_model: ModelTarget;
  agent_model_policy: ModelPolicy;
};
type DirectoryListing = { path: string; parent_path: string | null; directories: { name: string; path: string }[] };
type Theme = "light" | "dark";
type Language = "zh" | "en";
type SettingsSection = "appearance" | "runtime" | "account";
type IconName = "arrow-up" | "chevron-down" | "chevron-left" | "chevron-right" | "code" | "folder" | "globe" | "grid" | "menu" | "message" | "moon" | "more" | "paperclip" | "plus" | "refresh" | "search" | "settings" | "spark" | "sliders" | "sun" | "user" | "x";

const targetKey = (target: ModelTarget) => `${target.endpoint_id}\u0000${target.model}`;
const targetFromKey = (key: string): ModelTarget => {
  const [endpoint_id, model] = key.split("\u0000");
  return { endpoint_id, model };
};
const endpointModels = (endpoint: ModelEndpoint) => endpoint.models.length ? endpoint.models : endpoint.effective_models;

const copy = {
  zh: {
    workbench: "\\u5de5\\u4f5c\\u53f0", username: "\\u7528\\u6237\\u540d", password: "\\u5bc6\\u7801", signIn: "\\u8fdb\\u5165\\u5de5\\u4f5c\\u53f0", loginTitle: "\\u5c06\\u60f3\\u6cd5\\u53d8\\u6210\\u53ef\\u4ea4\\u4ed8\\u7684\\u4ee3\\u7801", loginDescription: "\\u5c06\\u9879\\u76ee\\u3001\\u5bf9\\u8bdd\\u4e0e Agent \\u5de5\\u4f5c\\u6d41\\u653e\\u5728\\u540c\\u4e00\\u4e2a\\u5b89\\u9759\\u7684\\u5f00\\u53d1\\u7a7a\\u95f4\\u3002", loginFootnote: "\\u672c\\u5730\\u4f18\\u5148 | \\u53ef\\u8ffd\\u6eaf | Windows first",
    newConversation: "\\u65b0\\u5efa\\u5bf9\\u8bdd", search: "\\u641c\\u7d22", projects: "\\u9879\\u76ee", allProjects: "\\u6240\\u6709\\u9879\\u76ee", addProject: "\\u6dfb\\u52a0\\u9879\\u76ee", recentConversations: "\\u6700\\u8fd1\\u5bf9\\u8bdd", noProjects: "\\u6682\\u65e0\\u9879\\u76ee\\uff0c\\u70b9\\u51fb + \\u6dfb\\u52a0", startConversation: "\\u4ece\\u65b0\\u5efa\\u5bf9\\u8bdd\\u5f00\\u59cb", noMatches: "\\u6ca1\\u6709\\u5339\\u914d\\u7684\\u5bf9\\u8bdd", personalSpace: "\\u4e2a\\u4eba\\u7a7a\\u95f4", localAccount: "\\u672c\\u5730\\u8d26\\u6237", settings: "\\u8bbe\\u7f6e", signOut: "\\u9000\\u51fa\\u767b\\u5f55", workspace: "\\u5de5\\u4f5c\\u533a", connected: "\\u5df2\\u8fde\\u63a5", context: "\\u4e0a\\u4e0b\\u6587", refresh: "\\u5237\\u65b0", light: "\\u6d45\\u8272", dark: "\\u6df1\\u8272", theme: "\\u4e3b\\u9898", language: "\\u8bed\\u8a00", chinese: "\\u4e2d\\u6587", english: "English",
    readyTitle: "\\u51c6\\u5907\\u597d\\u5f00\\u59cb\\u4e86\\u5417\\uff1f", readyDescription: "\\u544a\\u8bc9 DevPilot \\u4f60\\u60f3\\u5b8c\\u6210\\u4ec0\\u4e48\\uff0c\\u5b83\\u4f1a\\u7ed3\\u5408\\u5f53\\u524d\\u9879\\u76ee\\u4e0a\\u4e0b\\u6587\\u534f\\u52a9\\u4f60\\u3002", messagePlaceholder: "\\u63cf\\u8ff0\\u4f60\\u60f3\\u5b8c\\u6210\\u7684\\u4efb\\u52a1...", sendHint: "Enter \\u53d1\\u9001 | Shift + Enter \\u6362\\u884c", disclaimer: "DevPilot \\u53ef\\u80fd\\u4f1a\\u51fa\\u9519\\uff0c\\u8bf7\\u68c0\\u67e5\\u91cd\\u8981\\u7684\\u4ee3\\u7801\\u548c\\u547d\\u4ee4\\u3002", thinking: "\\u6b63\\u5728\\u601d\\u8003", you: "\\u4f60", agent: "Agent", untitled: "\\u672a\\u547d\\u540d\\u5bf9\\u8bdd",
    hello: "\\u4f60\\u597d", welcomeDescription: "\\u4ece\\u4e00\\u4e2a\\u95ee\\u9898\\u5f00\\u59cb\\uff0c\\u4e0e\\u4f60\\u7684\\u5f00\\u53d1 Agent \\u4e00\\u8d77\\u63a2\\u7d22\\u3001\\u6784\\u5efa\\u548c\\u4ea4\\u4ed8\\u3002", starterCards: [["\\u7406\\u89e3\\u4ee3\\u7801\\u5e93", "\\u626b\\u63cf\\u7ed3\\u6784\\u3001\\u4f9d\\u8d56\\u548c\\u5173\\u952e\\u5165\\u53e3"], ["\\u5f00\\u59cb\\u9879\\u76ee\\u4efb\\u52a1", "\\u76f4\\u63a5\\u4ece\\u9879\\u76ee\\u4e0a\\u4e0b\\u6587\\u5f00\\u59cb"], ["\\u8fd0\\u884c\\u6d4b\\u8bd5\\u548c\\u68c0\\u67e5", "\\u8c03\\u5ea6\\u6d4b\\u8bd5\\u5e76\\u8ffd\\u8e2a\\u7ed3\\u679c"], ["\\u7ee7\\u7eed\\u6700\\u8fd1\\u5bf9\\u8bdd", "\\u56de\\u5230\\u4e0a\\u6b21\\u505c\\u4e0b\\u6765\\u7684\\u5730\\u65b9"]],
    appearance: "\\u5916\\u89c2", appearanceTitle: "\\u5916\\u89c2\\u548c\\u8bed\\u8a00", appearanceDescription: "\\u66f4\\u6539\\u53ea\\u5e94\\u7528\\u4e8e\\u6b64\\u6d4f\\u89c8\\u5668\\uff0c\\u5e76\\u4f1a\\u81ea\\u52a8\\u4fdd\\u5b58\\u3002", themeTitle: "\\u4e3b\\u9898\\u98ce\\u683c", themeDescription: "\\u6d45\\u8272\\u4e3a\\u9ed8\\u8ba4\\uff0c\\u6df1\\u8272\\u6a21\\u5f0f\\u4f1a\\u4fdd\\u7559\\u5f53\\u524d\\u5de5\\u4f5c\\u533a\\u7684\\u98ce\\u683c\\u3002", languageTitle: "\\u754c\\u9762\\u8bed\\u8a00", languageDescription: "\\u53ef\\u968f\\u65f6\\u5728\\u4e2d\\u6587\\u548c English \\u4e4b\\u95f4\\u5207\\u6362\\u3002", runtime: "\\u8fd0\\u884c\\u65f6", account: "\\u8d26\\u6237", runtimeTitle: "模型连接与 Agent 策略", runtimeDescription: "配置多家 API、多个模型和 Agent 可选择的安全范围。", idleShutdown: "\\u7a7a\\u95f2\\u5173\\u95ed\\u65f6\\u95f4\\uff08\\u5206\\u949f\\uff09", modelProvider: "\\u6a21\\u578b\\u63d0\\u4f9b\\u5546", modelName: "\\u6a21\\u578b\\u540d\\u79f0", modelConnection: "API 连接", connectionName: "连接名称", connectionId: "连接 ID", protocol: "兼容协议", baseUrl: "Base URL", apiKey: "API Key", modelNames: "模型名称（每行一个）", addConnection: "添加 Coding Plan / API", removeConnection: "删除连接", defaultModel: "默认模型", agentSelection: "Agent 选模方式", manualSelection: "使用默认模型", automaticSelection: "让模型在允许范围内选择", allowedRange: "允许模型范围", keySaved: "已保存；留空保持不变", keyEnvironment: "未保存时读取环境变量", clearSavedKey: "清除已保存 Key", followPolicy: "遵循全局策略", autoModel: "自动选择（受允许范围限制）", cancel: "\\u53d6\\u6d88", saveChanges: "\\u4fdd\\u5b58\\u66f4\\u6539", saving: "\\u4fdd\\u5b58\\u4e2d...", localUsers: "\\u672c\\u5730\\u7528\\u6237", localUsersDescription: "\\u4fdd\\u7559\\u5728\\u672c\\u673a\\u6570\\u636e\\u5e93\\u4e2d\\u7684\\u56fa\\u5b9a\\u8d26\\u6237\\u3002", userId: "\\u7528\\u6237 ID", displayName: "\\u663e\\u793a\\u540d\\u79f0", initialPassword: "\\u521d\\u59cb\\u5bc6\\u7801", addUser: "\\u6dfb\\u52a0\\u7528\\u6237", accountMessage: "\\u53ea\\u6709\\u7ba1\\u7406\\u5458\\u53ef\\u4ee5\\u7ba1\\u7406\\u672c\\u5730\\u8d26\\u6237\\u3002",
    projectDescription: "\\u6ce8\\u518c\\u672c\\u5730\\u76ee\\u5f55\\uff0cDevPilot \\u4f1a\\u5728\\u9700\\u8981\\u65f6\\u8bfb\\u53d6\\u9879\\u76ee\\u89c4\\u5219\\u548c\\u4ed3\\u5e93\\u4e0a\\u4e0b\\u6587\\u3002", projectName: "\\u9879\\u76ee\\u540d\\u79f0", projectPath: "\\u9879\\u76ee\\u8def\\u5f84", browseWorkspace: "\\u5728\\u5de5\\u4f5c\\u533a\\u9009\\u62e9", directoryPicker: "\\u9009\\u62e9\\u9879\\u76ee\\u76ee\\u5f55", directoryPickerDescription: "\\u6d4f\\u89c8\\u4ec5\\u9650\\u4e8e DevPilot \\u5de5\\u4f5c\\u533a\\u3002\\u5982\\u679c\\u9879\\u76ee\\u4e0d\\u5728\\u6b64\\u5904\\uff0c\\u4ecd\\u53ef\\u624b\\u52a8\\u8f93\\u5165\\u8def\\u5f84\\u3002", useDirectory: "\\u4f7f\\u7528\\u6b64\\u76ee\\u5f55", upOneLevel: "\\u8fd4\\u56de\\u4e0a\\u7ea7", emptyFolder: "\\u8fd9\\u4e2a\\u76ee\\u5f55\\u6ca1\\u6709\\u53ef\\u9009\\u5b50\\u76ee\\u5f55\\u3002", loading: "\\u6b63\\u5728\\u52a0\\u8f7d...", close: "\\u5173\\u95ed", switchToDark: "\\u5207\\u6362\\u5230\\u6df1\\u8272\\u6a21\\u5f0f", switchToLight: "\\u5207\\u6362\\u5230\\u6d45\\u8272\\u6a21\\u5f0f"
  },
  en: {
    workbench: "Workbench", username: "Username", password: "Password", signIn: "Enter workspace", loginTitle: "Turn ideas into shippable code", loginDescription: "Projects, conversations, and agent workflows in one calm development workspace.", loginFootnote: "Local first | Auditable | Windows first",
    newConversation: "New conversation", search: "Search", projects: "Projects", allProjects: "All projects", addProject: "Add project", recentConversations: "Recent conversations", noProjects: "No projects yet. Add one above.", startConversation: "Start with a new conversation", noMatches: "No matching conversations", personalSpace: "Personal space", localAccount: "Local account", settings: "Settings", signOut: "Sign out", workspace: "Workspace", connected: "Connected", context: "Context", refresh: "Refresh", light: "Light", dark: "Dark", theme: "Theme", language: "Language", chinese: "Chinese", english: "English",
    readyTitle: "Ready when you are.", readyDescription: "Tell DevPilot what you want to accomplish and it will use the current project context to help.", messagePlaceholder: "Describe what you want to build...", sendHint: "Enter to send | Shift + Enter for a new line", disclaimer: "DevPilot can make mistakes. Check important code and commands.", thinking: "Thinking", you: "You", agent: "Agent", untitled: "Untitled conversation",
    hello: "Hello", welcomeDescription: "Start with a question and explore, build, and ship alongside your development agent.", starterCards: [["Understand a codebase", "Scan structure, dependencies, and entry points"], ["Start a project task", "Work directly from project context"], ["Run tests and checks", "Schedule tests and track results"], ["Continue a conversation", "Pick up where you left off"]],
    appearance: "Appearance", appearanceTitle: "Appearance and language", appearanceDescription: "Changes apply only in this browser and are saved automatically.", themeTitle: "Theme", themeDescription: "Light is the default. Dark preserves the original workbench style.", languageTitle: "Interface language", languageDescription: "Switch between Chinese and English at any time.", runtime: "Runtime", account: "Account", runtimeTitle: "Model connections and Agent policy", runtimeDescription: "Configure multiple APIs, models, and the range an Agent may choose from.", idleShutdown: "Idle shutdown (minutes)", modelProvider: "Model provider", modelName: "Model name", modelConnection: "API connection", connectionName: "Connection name", connectionId: "Connection ID", protocol: "Compatible protocol", baseUrl: "Base URL", apiKey: "API Key", modelNames: "Model names (one per line)", addConnection: "Add Coding Plan / API", removeConnection: "Remove connection", defaultModel: "Default model", agentSelection: "Agent model selection", manualSelection: "Use the default model", automaticSelection: "Let a model choose within the allowed range", allowedRange: "Allowed model range", keySaved: "Saved; leave blank to keep it", keyEnvironment: "Falls back to environment variables", clearSavedKey: "Clear saved key", followPolicy: "Follow global policy", autoModel: "Auto-select (bounded by allowed range)", cancel: "Cancel", saveChanges: "Save changes", saving: "Saving...", localUsers: "Local users", localUsersDescription: "Fixed accounts stored in the local database.", userId: "User ID", displayName: "Display name", initialPassword: "Initial password", addUser: "Add user", accountMessage: "Only administrators can manage local accounts.",
    projectDescription: "Register a local directory so DevPilot can use its rules and repository context.", projectName: "Project name", projectPath: "Project path", browseWorkspace: "Browse workspace", directoryPicker: "Choose project directory", directoryPickerDescription: "Browsing is limited to the DevPilot workspace. Enter a path manually when the project is elsewhere.", useDirectory: "Use this directory", upOneLevel: "Up one level", emptyFolder: "This directory has no selectable subdirectories.", loading: "Loading...", close: "Close", switchToDark: "Switch to dark theme", switchToLight: "Switch to light theme"
  }
} as const;

function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  const props = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "arrow-up": return <svg {...props}><path d="M12 19V5" /><path d="m6 11 6-6 6 6" /></svg>;
    case "chevron-down": return <svg {...props}><path d="m6 9 6 6 6-6" /></svg>;
    case "chevron-left": return <svg {...props}><path d="m15 18-6-6 6-6" /></svg>;
    case "chevron-right": return <svg {...props}><path d="m9 18 6-6-6-6" /></svg>;
    case "code": return <svg {...props}><path d="m8 9-4 3 4 3" /><path d="m16 9 4 3-4 3" /><path d="m14 5-4 14" /></svg>;
    case "folder": return <svg {...props}><path d="M3.5 7.5h6l1.7 2h9.3v8.8a1.7 1.7 0 0 1-1.7 1.7H5.2a1.7 1.7 0 0 1-1.7-1.7z" /><path d="M3.5 7.5V5.7A1.7 1.7 0 0 1 5.2 4h4l1.8 2h6.5" /></svg>;
    case "globe": return <svg {...props}><circle cx="12" cy="12" r="8" /><path d="M4 12h16M12 4a12 12 0 0 1 0 16M12 4a12 12 0 0 0 0 16" /></svg>;
    case "grid": return <svg {...props}><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></svg>;
    case "menu": return <svg {...props}><path d="M4 7h16M4 12h16M4 17h16" /></svg>;
    case "message": return <svg {...props}><path d="M20 11.5a7.4 7.4 0 0 1-7.8 7.5 8.2 8.2 0 0 1-3.2-.6L4 20l1.3-4.3A7.2 7.2 0 0 1 4 11.5 7.5 7.5 0 0 1 12 4a7.5 7.5 0 0 1 8 7.5Z" /><path d="M8 12h.01M12 12h.01M16 12h.01" /></svg>;
    case "moon": return <svg {...props}><path d="M20 15.2A8.5 8.5 0 0 1 8.8 4 8.5 8.5 0 1 0 20 15.2Z" /></svg>;
    case "more": return <svg {...props}><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></svg>;
    case "paperclip": return <svg {...props}><path d="m20.5 11.5-7.2 7.2a5 5 0 0 1-7-7l7.5-7.5a3.5 3.5 0 0 1 5 5l-7.6 7.6a2 2 0 0 1-2.8-2.8l6.8-6.8" /></svg>;
    case "plus": return <svg {...props}><path d="M12 5v14M5 12h14" /></svg>;
    case "refresh": return <svg {...props}><path d="M20 11a8 8 0 0 0-14.7-3.8L4 9" /><path d="M4 5v4h4" /><path d="M4 13a8 8 0 0 0 14.7 3.8L20 15" /><path d="M20 19v-4h-4" /></svg>;
    case "search": return <svg {...props}><circle cx="10.8" cy="10.8" r="6.6" /><path d="m16 16 4.2 4.2" /></svg>;
    case "settings": return <svg {...props}><path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z" /><path d="m19.4 15 .1.1a1.8 1.8 0 0 1-2.5 2.5l-.1-.1a1.8 1.8 0 0 0-3.1 1.3v.2a1.8 1.8 0 0 1-3.6 0v-.2a1.8 1.8 0 0 0-3.1-1.3l-.1.1a1.8 1.8 0 0 1-2.5-2.5l.1-.1a1.8 1.8 0 0 0-1.3-3.1h-.2a1.8 1.8 0 0 1 0-3.6h.2a1.8 1.8 0 0 0 1.3-3.1l-.1-.1A1.8 1.8 0 0 1 7 2.6l.1.1a1.8 1.8 0 0 0 3.1-1.3v-.2a1.8 1.8 0 0 1 3.6 0v.2a1.8 1.8 0 0 0 3.1 1.3l.1-.1a1.8 1.8 0 0 1 2.5 2.5l-.1.1a1.8 1.8 0 0 0 1.3 3.1h.2a1.8 1.8 0 0 1 0 3.6h-.2a1.8 1.8 0 0 0-1.3 3.1Z" /></svg>;
    case "sliders": return <svg {...props}><path d="M4 6h7M16 6h4M4 12h3M12 12h8M4 18h8M17 18h3" /><circle cx="14" cy="6" r="2" /><circle cx="9" cy="12" r="2" /><circle cx="15" cy="18" r="2" /></svg>;
    case "spark": return <svg {...props}><path d="m12 3 1.4 5.6L19 10l-5.6 1.4L12 17l-1.4-5.6L5 10l5.6-1.4z" /><path d="m19 16 .6 2.4L22 19l-2.4.6L19 22l-.6-2.4L16 19l2.4-.6z" /></svg>;
    case "sun": return <svg {...props}><circle cx="12" cy="12" r="3.5" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>;
    case "user": return <svg {...props}><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0 1 14 0" /></svg>;
    case "x": return <svg {...props}><path d="m6 6 12 12M18 6 6 18" /></svg>;
    default: return null;
  }
}

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const token = localStorage.getItem("devpilot_access_token") || "";
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers } });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText })) as { detail?: string };
    throw new Error(payload.detail || response.statusText);
  }
  return response.json() as Promise<T>;
};

function getInitials(value: string) { const parts = value.trim().split(/\s+/).filter(Boolean); return parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase() : value.slice(0, 2).toUpperCase() || "DP"; }
function blockText(block: Block) { return block.text || block.content || (block.type === "text" ? "" : `[${block.type}]`); }
function decodeEscapedText<T>(value: T): T {
  if (typeof value === "string") {
    return value.replace(/\\u([0-9a-f]{4})/gi, (_match, code: string) => String.fromCharCode(Number.parseInt(code, 16))) as T;
  }
  if (Array.isArray(value)) return value.map((item) => decodeEscapedText(item)) as T;
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, decodeEscapedText(item)])) as T;
  }
  return value;
}

type ModelSettingsLabels = {
  modelConnection: string; connectionName: string; connectionId: string; protocol: string;
  baseUrl: string; apiKey: string; modelNames: string; addConnection: string;
  removeConnection: string; defaultModel: string; agentSelection: string;
  manualSelection: string; automaticSelection: string; allowedRange: string;
  keySaved: string; keyEnvironment: string; clearSavedKey: string;
};

function ModelSettingsEditor({
  endpoints,
  defaultModelKey,
  policyMode,
  allowedModelKeys,
  labels,
  onEndpointsChange,
  onDefaultChange,
  onPolicyModeChange,
  onAllowedChange,
}: {
  endpoints: ModelEndpoint[];
  defaultModelKey: string;
  policyMode: "manual" | "auto";
  allowedModelKeys: string[];
  labels: ModelSettingsLabels;
  onEndpointsChange: (value: ModelEndpoint[]) => void;
  onDefaultChange: (value: string) => void;
  onPolicyModeChange: (value: "manual" | "auto") => void;
  onAllowedChange: (value: string[]) => void;
}) {
  const targets = endpoints.flatMap((endpoint) =>
    endpoint.enabled
      ? endpointModels(endpoint).map((model) => ({
          key: targetKey({ endpoint_id: endpoint.id, model }),
          label: `${endpoint.name} / ${model}`,
        }))
      : []
  );
  const updateEndpoint = (index: number, patch: Partial<ModelEndpoint>) => {
    onEndpointsChange(endpoints.map((endpoint, item) =>
      item === index ? { ...endpoint, ...patch } : endpoint
    ));
  };
  const addEndpoint = () => {
    let suffix = endpoints.length + 1;
    let id = "coding-plan";
    while (endpoints.some((endpoint) => endpoint.id === id)) id = `coding-plan-${suffix++}`;
    onEndpointsChange([...endpoints, {
      id,
      name: "Coding Plan",
      provider: "coding_plan",
      base_url: "",
      effective_base_url: null,
      api_key: "",
      api_key_configured: false,
      api_key_source: "none",
      models: ["coding-model"],
      effective_models: [],
      enabled: true,
    }]);
  };
  const removeEndpoint = (index: number) => {
    if (endpoints.length === 1) return;
    const removed = endpoints[index];
    const removedKeys = new Set(endpointModels(removed).map((model) =>
      targetKey({ endpoint_id: removed.id, model })
    ));
    const remaining = endpoints.filter((_endpoint, item) => item !== index);
    const remainingTargets = remaining.flatMap((endpoint) =>
      endpointModels(endpoint).map((model) =>
        targetKey({ endpoint_id: endpoint.id, model })
      )
    );
    onEndpointsChange(remaining);
    onAllowedChange(allowedModelKeys.filter((key) => !removedKeys.has(key)));
    if (removedKeys.has(defaultModelKey) && remainingTargets[0]) {
      onDefaultChange(remainingTargets[0]);
    }
  };
  const toggleAllowed = (key: string) => {
    onAllowedChange(allowedModelKeys.includes(key)
      ? allowedModelKeys.filter((item) => item !== key)
      : [...allowedModelKeys, key]);
  };

  return <div className="model-settings">
    <div className="model-endpoint-list">
      {endpoints.map((endpoint, index) => <section className="model-endpoint-card" key={`${endpoint.id}-${index}`}>
        <div className="model-endpoint-header">
          <div><strong>{labels.modelConnection} {index + 1}</strong><small>{endpoint.api_key_configured ? labels.keySaved : labels.keyEnvironment}</small></div>
          <button type="button" className="text-danger-button" disabled={endpoints.length === 1} onClick={() => removeEndpoint(index)}>{labels.removeConnection}</button>
        </div>
        <div className="form-grid">
          <label>{labels.connectionName}<input value={endpoint.name} onChange={(event) => updateEndpoint(index, { name: event.target.value })} /></label>
          <label>{labels.connectionId}<input value={endpoint.id} onChange={(event) => updateEndpoint(index, { id: event.target.value.toLowerCase() })} /></label>
        </div>
        <label>{labels.protocol}<select value={endpoint.provider} onChange={(event) => updateEndpoint(index, { provider: event.target.value as ModelEndpoint["provider"] })}>
          <option value="coding_plan">Coding Plan (OpenAI compatible)</option>
          <option value="openai">OpenAI / OpenAI compatible</option>
          <option value="anthropic">Anthropic Messages compatible</option>
          <option value="fake">Fake (local testing)</option>
          <option value="ollama">Ollama (declared, not implemented)</option>
        </select></label>
        <label>{labels.baseUrl}<input value={endpoint.base_url || ""} placeholder={endpoint.effective_base_url || "https://api.example.com/v1"} onChange={(event) => updateEndpoint(index, { base_url: event.target.value })} /></label>
        <label>{labels.apiKey}<input type="password" autoComplete="new-password" value={endpoint.api_key || ""} placeholder={endpoint.api_key_configured ? "••••••••••••" : "sk-..."} onChange={(event) => updateEndpoint(index, { api_key: event.target.value, clear_api_key: false })} /></label>
        {endpoint.api_key_configured && <label className="inline-check"><input type="checkbox" checked={Boolean(endpoint.clear_api_key)} onChange={(event) => updateEndpoint(index, { clear_api_key: event.target.checked, api_key: "" })} />{labels.clearSavedKey}</label>}
        <label>{labels.modelNames}<textarea rows={3} value={endpoint.models.join("\n")} placeholder={endpoint.effective_models.join("\n") || "model-name"} onChange={(event) => updateEndpoint(index, { models: event.target.value.split(/\r?\n|,/).map((value) => value.trim()).filter(Boolean) })} /></label>
      </section>)}
    </div>
    <button type="button" className="secondary-button align-start" onClick={addEndpoint}><Icon name="plus" size={15} />{labels.addConnection}</button>
    <div className="form-divider" />
    <label>{labels.defaultModel}<select value={defaultModelKey} onChange={(event) => onDefaultChange(event.target.value)}>
      {targets.map((target) => <option key={target.key} value={target.key}>{target.label}</option>)}
    </select></label>
    <label>{labels.agentSelection}<select value={policyMode} onChange={(event) => onPolicyModeChange(event.target.value as "manual" | "auto")}>
      <option value="manual">{labels.manualSelection}</option>
      <option value="auto">{labels.automaticSelection}</option>
    </select></label>
    <fieldset className="allowed-models"><legend>{labels.allowedRange}</legend>
      {targets.map((target) => <label className="inline-check" key={target.key}><input type="checkbox" checked={allowedModelKeys.includes(target.key)} onChange={() => toggleAllowed(target.key)} />{target.label}</label>)}
    </fieldset>
  </div>;
}

function relativeTime(value: string | undefined, language: Language) {
  if (!value) return decodeEscapedText(language === "zh" ? "\\u521a\\u521a" : "just now");
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return decodeEscapedText(language === "zh" ? "\\u521a\\u521a" : "just now");
  const minutes = Math.max(0, Math.round((Date.now() - timestamp) / 60_000));
  if (minutes < 1) return decodeEscapedText(language === "zh" ? "\\u521a\\u521a" : "just now");
  if (minutes < 60) return decodeEscapedText(language === "zh" ? `${minutes} \\u5206\\u949f\\u524d` : `${minutes}m ago`);
  const hours = Math.round(minutes / 60);
  if (hours < 24) return decodeEscapedText(language === "zh" ? `${hours} \\u5c0f\\u65f6\\u524d` : `${hours}h ago`);
  const days = Math.round(hours / 24);
  return decodeEscapedText(language === "zh" ? `${days} \\u5929\\u524d` : `${days}d ago`);
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem("devpilot_access_token") || "");
  const [currentUser, setCurrentUser] = useState(() => localStorage.getItem("devpilot_user_id") || "");
  const [language, setLanguage] = useState<Language>(() => localStorage.getItem("devpilot_language") === "en" ? "en" : "zh");
  const [theme, setTheme] = useState<Theme>(() => localStorage.getItem("devpilot_theme") === "dark" ? "dark" : "light");
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("appearance");
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedProject, setSelectedProject] = useState("all");
  const [selectedSession, setSelectedSession] = useState<string>();
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [message, setMessage] = useState("");
  const [settings, setSettings] = useState<RuntimeSettings>();
  const [modelOptions, setModelOptions] = useState<ModelOptions>();
  const [idleMinutes, setIdleMinutes] = useState("5");
  const [modelEndpoints, setModelEndpoints] = useState<ModelEndpoint[]>([]);
  const [defaultModelKey, setDefaultModelKey] = useState("");
  const [agentModelMode, setAgentModelMode] = useState<"manual" | "auto">("manual");
  const [allowedModelKeys, setAllowedModelKeys] = useState<string[]>([]);
  const [runModelKey, setRunModelKey] = useState("policy");
  const [newUserId, setNewUserId] = useState("");
  const [newUserName, setNewUserName] = useState("");
  const [newUserPassword, setNewUserPassword] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectPath, setNewProjectPath] = useState("");
  const [directoryListing, setDirectoryListing] = useState<DirectoryListing>();
  const [isDirectoryPickerOpen, setIsDirectoryPickerOpen] = useState(false);
  const [isDirectoryLoading, setIsDirectoryLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string>();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isProjectModalOpen, setIsProjectModalOpen] = useState(false);
  const [isInspectorOpen, setIsInspectorOpen] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const text = useMemo(() => decodeEscapedText(copy[language]), [language]);

  const activeSession = useMemo(() => sessions.find((item) => item.id === selectedSession), [sessions, selectedSession]);
  const activeProject = useMemo(() => projects.find((item) => item.id === activeSession?.project_id), [projects, activeSession]);
  const displayName = settings?.users.find((user) => user.id === currentUser)?.display_name || currentUser || "User";
  const allowedRunModels = useMemo(() => {
    const allowed = new Set(modelOptions?.agent_model_policy.allowed_models.map(targetKey) || []);
    return modelOptions?.models.filter((model) => allowed.has(targetKey(model))) || [];
  }, [modelOptions]);
  const filteredSessions = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return sessions.filter((session) => (selectedProject === "all" || session.project_id === selectedProject) && (!query || (session.title || session.thread_id).toLowerCase().includes(query)));
  }, [searchQuery, selectedProject, sessions]);

  useEffect(() => { document.documentElement.dataset.theme = theme; document.documentElement.lang = language === "zh" ? "zh-CN" : "en"; localStorage.setItem("devpilot_theme", theme); localStorage.setItem("devpilot_language", language); }, [language, theme]);
  useEffect(() => { if (isSearchOpen) searchInputRef.current?.focus(); }, [isSearchOpen]);

  const refresh = async () => {
    try {
      const [nextProjects, nextSessions, nextModelOptions] = await Promise.all([
        api<Project[]>("/api/v1/projects"),
        api<Session[]>("/api/v1/sessions"),
        api<ModelOptions>("/api/v1/model-options"),
      ]);
      setProjects(nextProjects); setSessions(nextSessions); setModelOptions(nextModelOptions);
      if (!selectedSession && nextSessions[0]) setSelectedSession(nextSessions[0].id);
      if (selectedSession && !nextSessions.some((session) => session.id === selectedSession)) setSelectedSession(nextSessions[0]?.id);
      if (currentUser === "admin") {
        const next = await api<RuntimeSettings>("/api/v1/settings");
        setSettings(next);
        setIdleMinutes(String(next.idle_shutdown_minutes));
        setModelEndpoints(next.model_endpoints.map((endpoint) => ({ ...endpoint, api_key: "", clear_api_key: false })));
        setDefaultModelKey(targetKey(next.default_model));
        setAgentModelMode(next.agent_model_policy.mode);
        setAllowedModelKeys(next.agent_model_policy.allowed_models.map(targetKey));
      }
      setError(undefined);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to connect to API"); }
  };

  useEffect(() => { if (token) void refresh(); }, [token, currentUser]);
  useEffect(() => { if (!selectedSession) { setSnapshot(undefined); return; } void api<Snapshot>(`/api/v1/sessions/${selectedSession}`).then(setSnapshot).catch((cause: Error) => setError(cause.message)); }, [selectedSession]);

  const login = async (event: FormEvent) => { event.preventDefault(); try { const result = await api<LoginResponse>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }); localStorage.setItem("devpilot_access_token", result.access_token); localStorage.setItem("devpilot_user_id", result.user_id); setToken(result.access_token); setCurrentUser(result.user_id); setPassword(""); setError(undefined); } catch (cause) { setError(cause instanceof Error ? cause.message : "Login failed"); } };
  const createSession = async () => { try { const projectId = selectedProject === "all" ? projects[0]?.id : selectedProject; const session = await api<Session>("/api/v1/sessions", { method: "POST", body: JSON.stringify({ thread_id: crypto.randomUUID(), title: text.newConversation, project_id: projectId }) }); setSessions((items) => [session, ...items]); setSelectedSession(session.id); setSnapshot({ messages: [], session }); setError(undefined); } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not create conversation"); } };
  const selectProject = (projectId: string) => { setSelectedProject(projectId); const nextSession = sessions.find((session) => projectId === "all" || session.project_id === projectId); if (nextSession) setSelectedSession(nextSession.id); };
  const send = (event: FormEvent) => {
    event.preventDefault();
    if (!activeSession || !message.trim() || isSending) return;
    const content = message.trim();
    const modelRequest = runModelKey === "auto"
      ? { model_mode: "auto" }
      : runModelKey === "policy"
        ? {}
        : { model_mode: "manual", ...targetFromKey(runModelKey) };
    setMessage("");
    setIsSending(true);
    const url = new URL(`/api/v1/sessions/${activeSession.id}/events`, window.location.href);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("access_token", token);
    const socket = new WebSocket(url);
    socket.onopen = () => socket.send(JSON.stringify({
      message: { role: "user", content: [{ type: "text", text: content }] },
      ...modelRequest,
    }));
    socket.onmessage = (item) => {
      const eventData = JSON.parse(item.data) as { type: string; detail?: string };
      if (eventData.type === "error") setError(eventData.detail || "Run failed");
      if (["run.completed", "run.failed", "run.cancelled", "error"].includes(eventData.type)) {
        setIsSending(false);
        socket.close();
        void api<Snapshot>(`/api/v1/sessions/${activeSession.id}`).then(setSnapshot);
      }
    };
    socket.onerror = () => {
      setIsSending(false);
      setError("Conversation connection failed");
    };
  };
  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    setIsSavingSettings(true);
    try {
      const updated = await api<RuntimeSettings>("/api/v1/settings", {
        method: "PUT",
        body: JSON.stringify({
          idle_shutdown_minutes: Number(idleMinutes),
          model_endpoints: modelEndpoints.map((endpoint) => ({
            id: endpoint.id,
            name: endpoint.name,
            provider: endpoint.provider,
            base_url: endpoint.base_url || null,
            api_key: endpoint.api_key || null,
            clear_api_key: Boolean(endpoint.clear_api_key),
            models: endpoint.models,
            enabled: endpoint.enabled,
          })),
          default_model: targetFromKey(defaultModelKey),
          agent_model_policy: {
            mode: agentModelMode,
            allowed_models: allowedModelKeys.map(targetFromKey),
          },
        }),
      });
      setSettings(updated);
      setModelEndpoints(updated.model_endpoints.map((endpoint) => ({ ...endpoint, api_key: "", clear_api_key: false })));
      setDefaultModelKey(targetKey(updated.default_model));
      setAgentModelMode(updated.agent_model_policy.mode);
      setAllowedModelKeys(updated.agent_model_policy.allowed_models.map(targetKey));
      setModelOptions(await api<ModelOptions>("/api/v1/model-options"));
      setError(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to save settings");
    } finally {
      setIsSavingSettings(false);
    }
  };
  const addUser = async (event: FormEvent) => { event.preventDefault(); try { await api("/api/v1/settings/users", { method: "POST", body: JSON.stringify({ id: newUserId, display_name: newUserName, password: newUserPassword }) }); setNewUserId(""); setNewUserName(""); setNewUserPassword(""); await refresh(); setError(undefined); } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to add user"); } };
  const createProject = async (event: FormEvent) => { event.preventDefault(); try { const project = await api<Project>("/api/v1/projects", { method: "POST", body: JSON.stringify({ name: newProjectName.trim(), root_path: newProjectPath.trim() }) }); setProjects((items) => [project, ...items.filter((item) => item.id !== project.id)]); setSelectedProject(project.id); setNewProjectName(""); setNewProjectPath(""); setIsProjectModalOpen(false); setError(undefined); } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to add project"); } };
  const loadDirectories = async (path?: string) => { setIsDirectoryLoading(true); try { const query = path ? `?path=${encodeURIComponent(path)}` : ""; setDirectoryListing(await api<DirectoryListing>(`/api/v1/project-directories${query}`)); setError(undefined); } catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to list workspace directories"); } finally { setIsDirectoryLoading(false); } };
  const openDirectoryPicker = () => { setIsDirectoryPickerOpen(true); void loadDirectories(); };
  const chooseDirectory = () => { if (!directoryListing) return; setNewProjectPath(directoryListing.path); if (!newProjectName.trim()) setNewProjectName(directoryListing.path.split(/[\\/]/).filter(Boolean).pop() || ""); setIsDirectoryPickerOpen(false); };
  const openSettings = (section: SettingsSection = "appearance") => { setSettingsSection(section); setIsSettingsOpen(true); setIsUserMenuOpen(false); };
  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } };
  const logout = () => { localStorage.removeItem("devpilot_access_token"); localStorage.removeItem("devpilot_user_id"); setToken(""); setCurrentUser(""); setIsUserMenuOpen(false); };
  const starterIcons: IconName[] = ["code", "folder", "sliders", "message"];

  if (!token) return <main className="login-page"><div className="login-glow login-glow-one" /><div className="login-glow login-glow-two" /><section className="login-card"><div className="brand-lockup"><span className="brand-mark brand-mark-large"><Icon name="spark" size={22} /></span><span>DevPilot</span></div><div className="login-copy"><p className="eyebrow">DEVELOPMENT WORKBENCH</p><h1>{text.loginTitle}</h1><p>{text.loginDescription}</p></div><form className="login-form" onSubmit={login}><label>{text.username}<input aria-label={text.username} value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label><label>{text.password}<input aria-label={text.password} type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label><button className="primary-button login-submit">{text.signIn} <Icon name="arrow-up" size={16} /></button></form>{error && <p className="error login-error">{error}</p>}<p className="login-footnote">{text.loginFootnote}</p></section></main>;

  return <main className="app-shell">
    <aside className={`sidebar ${isSidebarCollapsed ? "collapsed" : ""}`}>
      <div className="sidebar-top"><div className="brand-row"><span className="brand-mark"><Icon name="spark" size={17} /></span>{!isSidebarCollapsed && <div className="brand-copy"><strong>DevPilot</strong><span>{text.workbench}</span></div>}<button className="icon-button sidebar-collapse" onClick={() => setIsSidebarCollapsed((value) => !value)} aria-label={isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}><Icon name={isSidebarCollapsed ? "chevron-right" : "chevron-left"} size={17} /></button></div><div className="sidebar-actions"><button className="new-chat-button" onClick={() => void createSession()}><Icon name="plus" size={17} />{!isSidebarCollapsed && <span>{text.newConversation}</span>}{!isSidebarCollapsed && <kbd>Ctrl N</kbd>}</button><button className="sidebar-action-button" onClick={() => { setIsSearchOpen((value) => !value); setSearchQuery(""); }}><Icon name="search" size={17} />{!isSidebarCollapsed && <span>{text.search}</span>}{!isSidebarCollapsed && <kbd>Ctrl K</kbd>}</button>{isSearchOpen && !isSidebarCollapsed && <div className="sidebar-search"><Icon name="search" size={15} /><input ref={searchInputRef} aria-label={text.search} placeholder={`${text.search}...`} value={searchQuery} onChange={(event) => setSearchQuery(event.target.value)} /></div>}</div></div>
      <div className="sidebar-scroll"><section className="sidebar-section project-section">{!isSidebarCollapsed && <div className="section-heading"><span>{text.projects}</span><button className="mini-icon-button" onClick={() => setIsProjectModalOpen(true)} aria-label={text.addProject}><Icon name="plus" size={15} /></button></div>}<button className={`nav-row ${selectedProject === "all" ? "active" : ""}`} onClick={() => selectProject("all")}><span className="nav-icon"><Icon name="grid" size={16} /></span>{!isSidebarCollapsed && <><span className="nav-label">{text.allProjects}</span><span className="nav-count">{projects.length}</span></>}</button>{projects.map((project, index) => <button className={`nav-row ${selectedProject === project.id ? "active" : ""}`} key={project.id} onClick={() => selectProject(project.id)} title={project.name}><span className={`project-dot project-color-${index % 5}`}><Icon name="folder" size={14} /></span>{!isSidebarCollapsed && <><span className="nav-label">{project.name}</span><span className="nav-more"><Icon name="more" size={15} /></span></>}</button>)}{!projects.length && !isSidebarCollapsed && <p className="sidebar-empty">{text.noProjects}</p>}</section><section className="sidebar-section recent-section">{!isSidebarCollapsed && <div className="section-heading"><span>{text.recentConversations}</span><span className="section-meta">{filteredSessions.length}</span></div>}{filteredSessions.map((session) => { const sessionProject = projects.find((project) => project.id === session.project_id); return <button className={`chat-row ${session.id === selectedSession ? "active" : ""}`} key={session.id} onClick={() => setSelectedSession(session.id)}><span className="chat-row-icon"><Icon name="message" size={15} /></span>{!isSidebarCollapsed && <span className="chat-row-body"><strong>{session.title || text.untitled}</strong><small>{sessionProject?.name || text.personalSpace} | {relativeTime(session.updated_at || session.created_at, language)}</small></span>}</button>; })}{!filteredSessions.length && !isSidebarCollapsed && <div className="sidebar-empty recent-empty"><Icon name="message" size={17} /><span>{searchQuery ? text.noMatches : text.startConversation}</span></div>}</section></div>
      <div className="sidebar-bottom">{isUserMenuOpen && !isSidebarCollapsed && <div className="user-popover"><div className="popover-user"><span className="avatar avatar-small">{getInitials(displayName)}</span><div><strong>{displayName}</strong><small>{currentUser}</small></div></div><div className="popover-divider" /><button className="popover-row" onClick={() => openSettings()}><Icon name="settings" size={16} />{text.settings}</button><button className="popover-row danger" onClick={logout}><Icon name="x" size={16} />{text.signOut}</button></div>}<button className={`user-card ${isUserMenuOpen ? "active" : ""}`} onClick={() => setIsUserMenuOpen((value) => !value)}><span className="avatar">{getInitials(displayName)}</span>{!isSidebarCollapsed && <><span className="user-card-copy"><strong>{displayName}</strong><small>{text.localAccount}</small></span><Icon name="chevron-down" size={16} /></>}</button></div>
    </aside>
    <section className="workspace"><header className="topbar"><div className="topbar-context"><button className="mobile-menu-button icon-button" onClick={() => setIsSidebarCollapsed((value) => !value)} aria-label="Toggle sidebar"><Icon name="menu" size={19} /></button><div className="breadcrumb"><span className="breadcrumb-muted">{text.workspace}</span><Icon name="chevron-right" size={14} /><strong>{activeProject?.name || (selectedProject === "all" ? text.allProjects : projects.find((project) => project.id === selectedProject)?.name || text.projects)}</strong></div></div><div className="topbar-actions"><span className="connection-status"><i />{text.connected}</span><button className="topbar-button" onClick={() => setTheme((value) => value === "light" ? "dark" : "light")} title={theme === "light" ? text.switchToDark : text.switchToLight}><Icon name={theme === "light" ? "moon" : "sun"} size={17} /><span>{theme === "light" ? text.dark : text.light}</span></button><button className={`topbar-button ${isInspectorOpen ? "active" : ""}`} onClick={() => setIsInspectorOpen((value) => !value)}><Icon name="sliders" size={17} /><span>{text.context}</span></button><button className="icon-button" onClick={() => void refresh()} aria-label={text.refresh}><Icon name="refresh" size={17} /></button></div></header>
      {error && <div className="error-banner"><span>{error}</span><button className="icon-button" onClick={() => setError(undefined)} aria-label={text.close}><Icon name="x" size={15} /></button></div>}
      <div className="workspace-body"><section className="conversation-pane">{activeSession ? <><div className="conversation-header content-width"><div><p className="eyebrow">{activeProject?.name || text.personalSpace}</p><h1>{activeSession.title || text.untitled}</h1></div><button className="icon-button" aria-label="More conversation actions"><Icon name="more" size={19} /></button></div><div className="messages-scroll"><div className="messages content-width">{!snapshot?.messages.length && <div className="conversation-start"><span className="brand-mark brand-mark-soft"><Icon name="spark" size={18} /></span><h2>{text.readyTitle}</h2><p>{text.readyDescription}</p></div>}{snapshot?.messages.map((item) => <article className="message-row" key={item.id}><span className={`message-avatar ${item.message.role === "user" ? "message-avatar-user" : "message-avatar-agent"}`}>{item.message.role === "user" ? getInitials(displayName) : <Icon name="spark" size={15} />}</span><div className="message-content"><div className="message-meta"><strong>{item.message.role === "user" ? displayName : "DevPilot"}</strong><span>{item.message.role === "user" ? text.you : text.agent}</span></div>{item.message.content.map((block, index) => <p key={`${item.id}-${index}`}>{blockText(block)}</p>)}</div></article>)}{isSending && <article className="message-row"><span className="message-avatar message-avatar-agent"><Icon name="spark" size={15} /></span><div className="message-content"><div className="message-meta"><strong>DevPilot</strong><span>{text.thinking}</span></div><div className="thinking-dots"><i /><i /><i /></div></div></article>}</div></div><form className="composer-wrap content-width" onSubmit={send}><div className="composer"><textarea aria-label="Message" placeholder={text.messagePlaceholder} value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={handleComposerKeyDown} rows={1} /><div className="composer-toolbar"><button type="button" className="composer-tool" aria-label="Add attachment"><Icon name="paperclip" size={17} /></button><select className="composer-model-select" aria-label={text.modelName} value={runModelKey} onChange={(event) => setRunModelKey(event.target.value)}><option value="policy">{text.followPolicy}</option><option value="auto">{text.autoModel}</option>{allowedRunModels.map((target) => <option key={targetKey(target)} value={targetKey(target)}>{target.endpoint_name} / {target.model}</option>)}</select><span className="composer-hint">{text.sendHint}</span><button className="send-button" disabled={!message.trim() || isSending} aria-label="Send message"><Icon name="arrow-up" size={17} /></button></div></div><p className="composer-disclaimer">{text.disclaimer}</p></form></> : <div className="empty-workspace content-width"><div className="welcome-mark"><Icon name="spark" size={25} /></div><p className="eyebrow">DEV PILOT WORKSPACE</p><h1>{text.hello}, {displayName}</h1><p className="welcome-copy">{text.welcomeDescription}</p><div className="starter-grid">{text.starterCards.map((item, index) => <button className="starter-card" key={item[0]} onClick={() => { if (index === 3 && sessions[0]) setSelectedSession(sessions[0].id); else void createSession(); }}><span className="starter-icon"><Icon name={starterIcons[index]} size={18} /></span><span><strong>{item[0]}</strong><small>{item[1]}</small></span><Icon name="chevron-right" size={16} /></button>)}</div><button className="primary-button welcome-button" onClick={() => void createSession()}><Icon name="plus" size={17} />{text.newConversation}</button></div>}</section>
        {isInspectorOpen && <aside className="inspector"><div className="inspector-header"><div><p className="eyebrow">WORKSPACE</p><h2>{text.context}</h2></div><button className="icon-button" onClick={() => setIsInspectorOpen(false)} aria-label={text.close}><Icon name="x" size={17} /></button></div><div className="inspector-body"><section className="inspector-section"><div className="inspector-section-title"><Icon name="folder" size={16} /><span>{text.projects}</span></div>{activeProject ? <div className="context-card"><strong>{activeProject.name}</strong><small>{activeProject.root_path}</small><span className="context-status"><i />{text.connected}</span></div> : <div className="context-card context-empty">{text.projectDescription}</div>}</section><section className="inspector-section"><div className="inspector-section-title"><Icon name="spark" size={16} /><span>{text.modelName}</span></div><div className="context-row"><span>{text.modelProvider}</span><strong>{settings?.model_provider || "Default"}</strong></div><div className="context-row"><span>{text.modelName}</span><strong>{settings?.model_name || "Auto"}</strong></div></section><section className="inspector-section"><div className="inspector-section-title"><Icon name="code" size={16} /><span>{text.context}</span></div><div className="context-row"><span>Messages</span><strong>{snapshot?.messages.length || 0}</strong></div><div className="context-row"><span>Thread ID</span><strong className="mono">{activeSession?.thread_id.slice(0, 12) || "-"}</strong></div></section></div></aside>}
      </div>
    </section>
    {isSettingsOpen && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setIsSettingsOpen(false); }}><section className="modal settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"><div className="modal-header"><div><p className="eyebrow">DEVPILOT</p><h2 id="settings-title">{text.settings}</h2></div><button className="icon-button" onClick={() => setIsSettingsOpen(false)} aria-label={text.close}><Icon name="x" size={18} /></button></div><div className="settings-content"><nav className="settings-nav"><button className={`settings-nav-item ${settingsSection === "appearance" ? "active" : ""}`} onClick={() => setSettingsSection("appearance")}><Icon name="sun" size={16} />{text.appearance}</button><button className={`settings-nav-item ${settingsSection === "runtime" ? "active" : ""}`} onClick={() => setSettingsSection("runtime")}><Icon name="sliders" size={16} />{text.runtime}</button><button className={`settings-nav-item ${settingsSection === "account" ? "active" : ""}`} onClick={() => setSettingsSection("account")}><Icon name="user" size={16} />{text.account}</button></nav><div className="settings-panel">{settingsSection === "appearance" && <div className="appearance-panel"><div className="form-section-heading"><strong>{text.appearanceTitle}</strong><span>{text.appearanceDescription}</span></div><section className="appearance-section"><div><strong>{text.themeTitle}</strong><p>{text.themeDescription}</p></div><div className="choice-grid"><button className={`choice-button ${theme === "light" ? "active" : ""}`} onClick={() => setTheme("light")}><Icon name="sun" size={18} /><span>{text.light}</span></button><button className={`choice-button ${theme === "dark" ? "active" : ""}`} onClick={() => setTheme("dark")}><Icon name="moon" size={18} /><span>{text.dark}</span></button></div></section><section className="appearance-section"><div><strong>{text.languageTitle}</strong><p>{text.languageDescription}</p></div><div className="choice-grid"><button className={`choice-button ${language === "zh" ? "active" : ""}`} onClick={() => setLanguage("zh")}><Icon name="globe" size={18} /><span>{text.chinese}</span></button><button className={`choice-button ${language === "en" ? "active" : ""}`} onClick={() => setLanguage("en")}><Icon name="globe" size={18} /><span>{text.english}</span></button></div></section></div>}{settingsSection === "runtime" && (currentUser === "admin" ? <form className="settings-form" onSubmit={saveSettings}><div className="form-section-heading"><strong>{text.runtimeTitle}</strong><span>{text.runtimeDescription}</span></div><label>{text.idleShutdown}<input aria-label={text.idleShutdown} type="number" min="1" max="1440" value={idleMinutes} onChange={(event) => setIdleMinutes(event.target.value)} /></label><ModelSettingsEditor endpoints={modelEndpoints} defaultModelKey={defaultModelKey} policyMode={agentModelMode} allowedModelKeys={allowedModelKeys} labels={text} onEndpointsChange={setModelEndpoints} onDefaultChange={setDefaultModelKey} onPolicyModeChange={setAgentModelMode} onAllowedChange={setAllowedModelKeys} /><div className="form-actions"><button type="button" className="secondary-button" onClick={() => setIsSettingsOpen(false)}>{text.cancel}</button><button className="primary-button" disabled={isSavingSettings}>{isSavingSettings ? text.saving : text.saveChanges}</button></div></form> : <p className="settings-empty">{text.accountMessage}</p>)}{settingsSection === "account" && (currentUser === "admin" ? <><form className="settings-form" onSubmit={addUser}><div className="form-section-heading"><strong>{text.localUsers}</strong><span>{text.localUsersDescription}</span></div><div className="form-grid"><label>{text.userId}<input aria-label={text.userId} value={newUserId} onChange={(event) => setNewUserId(event.target.value)} placeholder="designer" /></label><label>{text.displayName}<input aria-label={text.displayName} onChange={(event) => setNewUserName(event.target.value)} value={newUserName} placeholder="Designer" /></label></div><label>{text.initialPassword}<input aria-label={text.initialPassword} type="password" value={newUserPassword} onChange={(event) => setNewUserPassword(event.target.value)} /></label><button className="secondary-button align-start">{text.addUser}</button></form><div className="user-list">{settings?.users.map((user) => <div className="user-list-row" key={user.id}><span className="avatar avatar-small">{getInitials(user.display_name)}</span><span><strong>{user.display_name}</strong><small>{user.id}</small></span></div>)}</div></> : <p className="settings-empty">{text.accountMessage}</p>)}</div></div></section></div>}
    {isProjectModalOpen && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setIsProjectModalOpen(false); }}><section className="modal project-modal" role="dialog" aria-modal="true" aria-labelledby="project-title"><div className="modal-header"><div><p className="eyebrow">PROJECTS</p><h2 id="project-title">{text.addProject}</h2></div><button className="icon-button" onClick={() => setIsProjectModalOpen(false)} aria-label={text.close}><Icon name="x" size={18} /></button></div><form className="settings-form" onSubmit={createProject}><p className="modal-description">{text.projectDescription}</p><label>{text.projectName}<input aria-label={text.projectName} required value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="DevPilot" /></label><label>{text.projectPath}<div className="path-control"><input aria-label={text.projectPath} required value={newProjectPath} onChange={(event) => setNewProjectPath(event.target.value)} placeholder="C:\\Users\\you\\Projects\\my-app" /><button type="button" className="secondary-button browse-button" onClick={openDirectoryPicker}><Icon name="folder" size={15} />{text.browseWorkspace}</button></div></label><div className="form-actions"><button type="button" className="secondary-button" onClick={() => setIsProjectModalOpen(false)}>{text.cancel}</button><button className="primary-button">{text.addProject}</button></div></form></section></div>}
    {isDirectoryPickerOpen && <div className="modal-backdrop directory-picker-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setIsDirectoryPickerOpen(false); }}><section className="modal directory-picker" role="dialog" aria-modal="true" aria-labelledby="directory-picker-title"><div className="modal-header"><div><p className="eyebrow">WORKSPACE</p><h2 id="directory-picker-title">{text.directoryPicker}</h2></div><button className="icon-button" onClick={() => setIsDirectoryPickerOpen(false)} aria-label={text.close}><Icon name="x" size={18} /></button></div><div className="directory-picker-body"><p className="modal-description">{text.directoryPickerDescription}</p><div className="directory-current"><Icon name="folder" size={17} /><code>{directoryListing?.path || text.loading}</code></div>{directoryListing?.parent_path && <button className="directory-up" onClick={() => void loadDirectories(directoryListing.parent_path || undefined)}><Icon name="chevron-left" size={16} />{text.upOneLevel}</button>}<div className="directory-list">{isDirectoryLoading ? <p className="directory-empty">{text.loading}</p> : directoryListing?.directories.length ? directoryListing.directories.map((directory) => <button className="directory-row" key={directory.path} onClick={() => void loadDirectories(directory.path)}><span><Icon name="folder" size={17} />{directory.name}</span><Icon name="chevron-right" size={16} /></button>) : <p className="directory-empty">{text.emptyFolder}</p>}</div><div className="form-actions"><button type="button" className="secondary-button" onClick={() => setIsDirectoryPickerOpen(false)}>{text.cancel}</button><button className="primary-button" disabled={!directoryListing} onClick={chooseDirectory}>{text.useDirectory}</button></div></div></section></div>}
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
