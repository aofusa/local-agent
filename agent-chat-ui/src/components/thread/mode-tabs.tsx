// local-agent: 画像 / チャット tabs (Grok-style split). A tab selects the LangGraph graph:
// 画像 = graph "agent" (the image generation graph), チャット = graph "chat" (conversation and Tor web search).
// Threads stay separate because the history list is filtered by graph_id.
import { useEffect, useRef, useSyncExternalStore } from "react";
import { useQueryState } from "nuqs";
import { Image as ImageIcon, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThreads } from "@/providers/Thread";

export const IMAGE_GRAPH = process.env.NEXT_PUBLIC_ASSISTANT_ID || "agent";
export const CHAT_GRAPH = "chat";

const TABS = [
  { id: IMAGE_GRAPH, label: "画像", icon: ImageIcon },
  { id: CHAT_GRAPH, label: "チャット", icon: MessageSquare },
];

const STORAGE_KEY = "local-agent:last-thread:";

function remember(tab: string, threadId: string | null) {
  try {
    if (threadId) window.sessionStorage.setItem(STORAGE_KEY + tab, threadId);
  } catch {
    // storage unavailable (private mode): the tab just opens a new thread
  }
}

function recall(tab: string): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY + tab);
  } catch {
    return null;
  }
}

export function useActiveTab(): string {
  const [assistantId] = useQueryState("assistantId");
  return assistantId || IMAGE_GRAPH;
}

export function ModeTabs({ className }: { className?: string }) {
  const [assistantId, setAssistantId] = useQueryState("assistantId");
  const [threadId, setThreadId] = useQueryState("threadId");
  const { getThreads, setThreads, setThreadsLoading } = useThreads();
  const active = assistantId || IMAGE_GRAPH;
  const first = useRef(true);

  useEffect(() => {
    remember(active, threadId);
  }, [active, threadId]);

  // The history list loads once on mount; reload it for the other graph after a switch.
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    setThreadsLoading(true);
    getThreads()
      .then(setThreads)
      .catch(console.error)
      .finally(() => setThreadsLoading(false));
  }, [getThreads, setThreads, setThreadsLoading]);

  const select = (tab: string) => {
    if (tab === active) return;
    remember(active, threadId);
    setThreadId(recall(tab));
    setAssistantId(tab);
  };

  return (
    <div
      role="tablist"
      aria-label="モード"
      className={cn(
        "bg-muted inline-flex shrink-0 items-center gap-1 rounded-full p-1",
        className,
      )}
    >
      {TABS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          role="tab"
          type="button"
          aria-selected={id === active}
          onClick={() => select(id)}
          className={cn(
            "flex cursor-pointer items-center gap-1.5 rounded-full px-3 py-1.5 text-sm whitespace-nowrap transition-colors sm:px-4",
            id === active
              ? "bg-background text-foreground font-semibold shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className="size-4" />
          {label}
        </button>
      ))}
    </div>
  );
}

// local-agent: 自動 / 速い / 思考 for the chat tab (sent as config.configurable.mode; the image tab ignores it).
// 速い = one search round, one-shot writing, code is not run, no thinking tokens. 思考 = deeper search rounds,
// outline -> draft -> revise, code runs in Docker after approval, thinking tokens shown apart from the answer.
// 自動 = the graph picks one per message (like Grok's auto). The choice is a per-browser convenience.
export type ChatMode = "auto" | "fast" | "think";

const CHAT_MODES: { id: ChatMode; label: string; title: string }[] = [
  {
    id: "auto",
    label: "自動",
    title: "内容に応じて速い / 思考を自動で選びます",
  },
  {
    id: "fast",
    label: "速い",
    title: "すばやく答えます（検索 1 回、実行なし）",
  },
  {
    id: "think",
    label: "思考",
    title: "深く考えます（追加検索、推敲、承認後のコード実行）",
  },
];
const MODE_KEY = "local-agent:chat-mode";
const modeListeners = new Set<() => void>();
let memoryMode: ChatMode = "auto"; // used when the browser refuses storage

function readMode(): ChatMode {
  try {
    const value = window.localStorage.getItem(MODE_KEY);
    return value === "fast" || value === "think" || value === "auto"
      ? value
      : memoryMode;
  } catch {
    return memoryMode;
  }
}

function subscribeMode(listener: () => void) {
  modeListeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    modeListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

export function useChatMode(): [ChatMode, (mode: ChatMode) => void] {
  const mode = useSyncExternalStore(
    subscribeMode,
    readMode,
    () => "auto" as ChatMode,
  );
  const update = (next: ChatMode) => {
    memoryMode = next;
    try {
      window.localStorage.setItem(MODE_KEY, next);
    } catch {
      // storage unavailable: the choice lasts for this page only
    }
    modeListeners.forEach((listener) => listener());
  };
  return [mode, update];
}

export function ChatModeSwitch({
  mode,
  onChange,
  className,
}: {
  mode: ChatMode;
  onChange: (mode: ChatMode) => void;
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label="応答モード"
      className={cn(
        "bg-background inline-flex shrink-0 items-center gap-0.5 rounded-full border p-0.5",
        className,
      )}
    >
      {CHAT_MODES.map(({ id, label, title }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={mode === id}
          title={title}
          onClick={() => onChange(id)}
          className={cn(
            "cursor-pointer rounded-full px-3 py-1 text-xs whitespace-nowrap transition-colors",
            mode === id
              ? "bg-foreground text-background font-semibold"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
