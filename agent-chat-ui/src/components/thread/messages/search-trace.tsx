// local-agent: the chat tab's search trace (message.additional_kwargs.search_trace).
// Like Grok's multi-agent search, only the tool calls (search intents and queries), the hit URLs and the
// models per role are shown; the readers' drafts and exchanges are not returned by the graph.
import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Globe,
  Loader2,
  Newspaper,
  Link2,
} from "lucide-react";

type Hit = { title: string; url: string };
type TraceIntent = {
  id: number;
  tool: "web" | "news" | "browse";
  q: string;
  why?: string;
  round?: number;
  hits?: Hit[];
  opened?: string[];
  cards?: number;
  error?: string | null;
  status?: "running" | "done";
};
export type SearchTrace = {
  mode?: "resident" | "proxy" | null;
  roles?: Record<string, string | null | undefined>;
  width?: number | null;
  intents?: TraceIntent[];
  seconds?: number;
  cards_total?: number;
  claims_confirmed?: number;
};

const ROLE_LABELS: Record<string, string> = {
  router: "ルータ",
  planner: "計画",
  filter: "フィルタ",
  reader: "reader",
  critic: "批評",
  leader: "リーダー",
  synthesizer: "統合",
};
const TOOL_ICONS = { web: Globe, news: Newspaper, browse: Link2 };
const TOOL_LABELS = {
  web: "web_search",
  news: "news_search",
  browse: "open_url",
};

export function isSearchTrace(value: unknown): value is SearchTrace {
  return (
    !!value &&
    typeof value === "object" &&
    Array.isArray((value as SearchTrace).intents)
  );
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function SearchTraceView({ trace }: { trace: SearchTrace }) {
  const [open, setOpen] = useState(false);
  const intents = trace.intents ?? [];
  const running = intents.some((i) => i.status === "running");
  const hits = intents.reduce((n, i) => n + (i.hits?.length ?? 0), 0);
  const roles = Object.entries(trace.roles ?? {}).filter(([, v]) => !!v);
  const summary = [
    `検索 ${intents.length} 本`,
    `ヒット ${hits} 件`,
    trace.cards_total != null ? `カード ${trace.cards_total}` : null,
    trace.claims_confirmed != null
      ? `引用確認 ${trace.claims_confirmed}`
      : null,
    trace.seconds != null ? `${Math.round(trace.seconds)} 秒` : null,
    trace.mode === "proxy" ? "代理リーダー" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="bg-muted/40 w-full rounded-xl border text-sm">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-muted-foreground flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left"
      >
        {running ? (
          <Loader2 className="size-4 animate-spin" />
        ) : open ? (
          <ChevronDown className="size-4" />
        ) : (
          <ChevronRight className="size-4" />
        )}
        <span className="text-foreground font-medium">Tor 経由の検索</span>
        <span className="truncate">{summary}</span>
      </button>
      {(open || running) && (
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          {intents.map((intent) => {
            const Icon = TOOL_ICONS[intent.tool] ?? Globe;
            return (
              <div
                key={intent.id}
                className="flex flex-col gap-1"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Icon className="text-muted-foreground size-4 shrink-0" />
                  <code className="bg-background rounded px-1.5 py-0.5 text-xs">
                    {TOOL_LABELS[intent.tool] ?? intent.tool}
                  </code>
                  <span className="font-medium break-all">{intent.q}</span>
                  {intent.round ? (
                    <span className="text-muted-foreground text-xs">
                      （追加検索）
                    </span>
                  ) : null}
                  {intent.status === "running" && (
                    <Loader2 className="text-muted-foreground size-3 animate-spin" />
                  )}
                </div>
                {intent.why && (
                  <p className="text-muted-foreground pl-6 text-xs">
                    {intent.why}
                  </p>
                )}
                {intent.error && (
                  <p className="pl-6 text-xs text-amber-700">{intent.error}</p>
                )}
                {!!intent.hits?.length && (
                  <ul className="flex flex-col gap-0.5 pl-6">
                    {intent.hits.map((hit) => (
                      <li
                        key={hit.url}
                        className="flex items-baseline gap-1.5 text-xs"
                      >
                        <span
                          className={
                            intent.opened?.includes(hit.url)
                              ? "text-emerald-700"
                              : "text-muted-foreground"
                          }
                        >
                          {intent.opened?.includes(hit.url) ? "●" : "○"}
                        </span>
                        <a
                          href={hit.url}
                          target="_blank"
                          rel="noopener noreferrer nofollow"
                          className="truncate underline-offset-2 hover:underline"
                          title={hit.url}
                        >
                          {hit.title || hit.url}
                        </a>
                        <span className="text-muted-foreground shrink-0">
                          {host(hit.url)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          {roles.length > 0 && (
            <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 border-t pt-2 text-xs">
              {roles.map(([role, model]) => (
                <span key={role}>
                  {ROLE_LABELS[role] ?? role}: {model}
                </span>
              ))}
            </div>
          )}
          <p className="text-muted-foreground text-xs">
            ● は reader が開いたページ。検索の通信は Tor 経由です。
          </p>
        </div>
      )}
    </div>
  );
}
