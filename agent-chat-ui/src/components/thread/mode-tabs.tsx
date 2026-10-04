// local-agent: 画像 / チャット tabs (Grok-style split). A tab selects the LangGraph graph:
// 画像 = graph "agent" (the image generation graph), チャット = graph "chat" (conversation and Tor web search).
// Threads stay separate because the history list is filtered by graph_id.
import { useEffect, useRef } from "react";
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
        "bg-muted inline-flex items-center gap-1 rounded-full p-1",
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
            "flex cursor-pointer items-center gap-1.5 rounded-full px-4 py-1.5 text-sm transition-colors",
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
