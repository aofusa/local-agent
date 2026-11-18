// local-agent: 画像 / チャット / 動画 tabs (Grok-style split). A tab selects the LangGraph graph:
// 画像 = graph "agent" (the image generation graph), チャット = graph "chat" (conversation and Tor web search),
// 動画 = graph "video" (one image -> a short video, docs/image-to-video-design.md).
// Threads stay separate because the history list is filtered by graph_id.
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { useQueryState } from "nuqs";
import { Film, Image as ImageIcon, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import { useThreads } from "@/providers/Thread";
import type { VideoOptions } from "./model-picker";

export const IMAGE_GRAPH = process.env.NEXT_PUBLIC_ASSISTANT_ID || "agent";
export const CHAT_GRAPH = "chat";
export const VIDEO_GRAPH = "video";

const TABS = [
  { id: IMAGE_GRAPH, label: "画像", icon: ImageIcon },
  { id: CHAT_GRAPH, label: "チャット", icon: MessageSquare },
  { id: VIDEO_GRAPH, label: "動画", icon: Film },
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

/** Switch tabs: back to that tab's last thread, or a new thread (``newThread``, e.g. the image -> video hand-off). */
export function useSwitchTab(): (
  tab: string,
  options?: { newThread?: boolean },
) => void {
  const [assistantId, setAssistantId] = useQueryState("assistantId");
  const [threadId, setThreadId] = useQueryState("threadId");
  const active = assistantId || IMAGE_GRAPH;
  return useCallback(
    (tab: string, options?: { newThread?: boolean }) => {
      if (tab === active && !options?.newThread) return;
      remember(active, threadId);
      setThreadId(options?.newThread ? null : recall(tab));
      setAssistantId(tab);
    },
    [active, threadId, setAssistantId, setThreadId],
  );
}

// The graph whose threads the history list shows. Module-level, not per instance: ModeTabs is mounted in the
// start screen and in the conversation header, and a switch that crosses the two would otherwise mount a fresh
// copy that skipped its first run and left the list on the previous graph.
let historyGraph: string | null = null;

export function ModeTabs({ className }: { className?: string }) {
  const [threadId] = useQueryState("threadId");
  const active = useActiveTab();
  const select = useSwitchTab();
  const { getThreads, setThreads, setThreadsLoading } = useThreads();

  useEffect(() => {
    remember(active, threadId);
  }, [active, threadId]);

  // The history list loads once on mount (ThreadHistory); reload it for the other graph after a switch.
  useEffect(() => {
    if (historyGraph === null || historyGraph === active) {
      historyGraph = active;
      return;
    }
    historyGraph = active;
    setThreadsLoading(true);
    getThreads()
      .then(setThreads)
      .catch(console.error)
      .finally(() => setThreadsLoading(false));
  }, [active, getThreads, setThreads, setThreadsLoading]);

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

// local-agent: the video tab's length (sent as config.configurable.video_seconds). The host offers the choices
// (GET /models "video_options", capped by its VIDEO_MAX_SECONDS); the pick is a per-browser convenience and falls
// back to the host's default when the host no longer offers it.
const SECONDS_KEY = "local-agent:video-seconds";
const secondsListeners = new Set<() => void>();
let memorySeconds: number | null = null; // used when the browser refuses storage

function readSeconds(): number | null {
  try {
    const value = Number(window.localStorage.getItem(SECONDS_KEY));
    return Number.isFinite(value) && value > 0 ? value : memorySeconds;
  } catch {
    return memorySeconds;
  }
}

function subscribeSeconds(listener: () => void) {
  secondsListeners.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    secondsListeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** The picked length in seconds, or null when the host sent no choices (nothing is sent then). */
export function useVideoSeconds(
  options: VideoOptions | null | undefined,
): [number | null, (seconds: number) => void] {
  const stored = useSyncExternalStore(subscribeSeconds, readSeconds, () => null);
  const choices = options?.choices ?? [];
  const value = !options
    ? null
    : stored !== null && choices.includes(stored)
      ? stored
      : options.default_seconds;
  const update = (seconds: number) => {
    memorySeconds = seconds;
    try {
      window.localStorage.setItem(SECONDS_KEY, String(seconds));
    } catch {
      // storage unavailable: the choice lasts for this page only
    }
    secondsListeners.forEach((listener) => listener());
  };
  return [value, update];
}

export function VideoLengthSwitch({
  choices,
  value,
  onChange,
  className,
}: {
  choices: number[];
  value: number | null;
  onChange: (seconds: number) => void;
  className?: string;
}) {
  if (!choices.length) return null;
  return (
    <div
      role="radiogroup"
      aria-label="動画の長さ"
      className={cn(
        "bg-background inline-flex shrink-0 items-center gap-0.5 rounded-full border p-0.5",
        className,
      )}
    >
      {choices.map((seconds) => (
        <button
          key={seconds}
          type="button"
          role="radio"
          aria-checked={value === seconds}
          title={`約 ${seconds} 秒の動画を作ります（長いほど時間がかかります）`}
          onClick={() => onChange(seconds)}
          className={cn(
            "cursor-pointer rounded-full px-3 py-1 text-xs whitespace-nowrap transition-colors",
            value === seconds
              ? "bg-foreground text-background font-semibold"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {seconds} 秒
        </button>
      ))}
    </div>
  );
}
