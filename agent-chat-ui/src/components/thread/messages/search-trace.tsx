// local-agent: the chat tab's search trace (message.additional_kwargs.search_trace).
// Like Grok's multi-agent search, only the tool calls (search intents and queries), the hit URLs and the
// models per role are shown; the readers' drafts and exchanges are not returned by the graph.
// Think mode adds the rounds, the sub-questions and their status, the adopted and rejected cards and the stop
// reason. The same file holds the folded thinking block (additional_kwargs.thinking, never part of the answer),
// the writing / code steps (additional_kwargs.task_trace), the claim table of claim verification
// (additional_kwargs.claim_trace).
import { ReactNode, useState } from "react";
import {
  Brain,
  ChevronDown,
  ChevronRight,
  Code2,
  Globe,
  Loader2,
  Newspaper,
  Link2,
  PenLine,
  ShieldCheck,
  Workflow,
} from "lucide-react";

type Hit = { title: string; url: string };
type TraceIntent = {
  id: number;
  tool: "web" | "news" | "browse";
  q: string;
  why?: string;
  round?: number;
  subquestion_id?: string;
  hits?: Hit[];
  opened?: string[];
  cards?: number;
  error?: string | null;
  status?: "running" | "done";
};
type SubQuestion = {
  id: string;
  question: string;
  status: "answered" | "partial" | "open";
  note?: string;
};
type AdoptedCard = {
  id: string;
  url: string;
  domain?: string;
  round?: number;
  subquestion_ids?: string[];
};
type Rejected = { url: string; title?: string; reason: string; round?: number };
export type SearchTrace = {
  mode?: "resident" | "proxy" | null;
  chat_mode?: "fast" | "think" | null;
  roles?: Record<string, string | null | undefined>;
  width?: number | null;
  intents?: TraceIntent[];
  seconds?: number;
  elapsed_s?: number | null;
  cards_total?: number;
  claims_confirmed?: number;
  round?: number;
  max_rounds?: number | null;
  goal?: string;
  subquestions?: SubQuestion[];
  contradictions?: { subquestion_id?: string; summary?: string }[];
  stop_reason?: string | null;
  pages_read?: number;
  max_pages?: number | null;
  adopted?: AdoptedCard[];
  rejected?: Rejected[];
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
const STATUS_LABELS: Record<string, string> = {
  answered: "回答済み",
  partial: "一部",
  open: "未回答",
};
const STATUS_COLORS: Record<string, string> = {
  answered: "text-emerald-700",
  partial: "text-amber-700",
  open: "text-muted-foreground",
};
const STOP_LABELS: Record<string, string> = {
  sufficient: "十分に答えられた",
  diminishing: "新しい根拠が増えなくなった",
  budget: "予算の上限",
  no_hits: "検索結果なし",
  error: "途中で失敗",
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

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 border-t pt-2">
      <p className="text-muted-foreground text-xs font-medium">{title}</p>
      {children}
    </div>
  );
}

export function SearchTraceView({ trace }: { trace: SearchTrace }) {
  const [open, setOpen] = useState(false);
  const intents = trace.intents ?? [];
  const running = intents.some((i) => i.status === "running");
  const hits = intents.reduce((n, i) => n + (i.hits?.length ?? 0), 0);
  const roles = Object.entries(trace.roles ?? {}).filter(([, v]) => !!v);
  const subs = trace.subquestions ?? [];
  const deep = trace.chat_mode === "think" || subs.length > 0;
  const rounds = Math.max(
    trace.round ?? 0,
    ...intents.map((i) => (i.round ?? 0) + 1),
  );
  const seconds = trace.seconds ?? trace.elapsed_s;
  const summary = [
    deep ? `${rounds} ラウンド` : null,
    `検索 ${intents.length} 本`,
    `ヒット ${hits} 件`,
    trace.pages_read != null ? `${trace.pages_read} ページ` : null,
    trace.cards_total != null ? `カード ${trace.cards_total}` : null,
    trace.claims_confirmed != null
      ? `引用確認 ${trace.claims_confirmed}`
      : null,
    seconds != null ? `${Math.round(seconds)} 秒` : null,
    trace.stop_reason
      ? `停止: ${STOP_LABELS[trace.stop_reason] ?? trace.stop_reason}`
      : null,
    trace.mode === "proxy" ? "代理リーダー" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const byRound = new Map<number, TraceIntent[]>();
  for (const intent of intents) {
    const r = intent.round ?? 0;
    byRound.set(r, [...(byRound.get(r) ?? []), intent]);
  }

  return (
    <div className="bg-muted/40 w-full min-w-0 rounded-xl border text-sm [overflow-wrap:anywhere]">
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
        <span className="text-foreground shrink-0 font-medium whitespace-nowrap">
          {deep ? "Tor 経由の深い検索" : "Tor 経由の検索"}
        </span>
        <span className="min-w-0 truncate">{summary}</span>
      </button>
      {(open || running) && (
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          {trace.goal && (
            <p className="text-xs">
              <span className="text-muted-foreground">目的: </span>
              {trace.goal}
            </p>
          )}
          {subs.length > 0 && (
            <ul className="flex flex-col gap-0.5 text-xs">
              {subs.map((s) => (
                <li
                  key={s.id}
                  className="flex items-baseline gap-2"
                >
                  <code className="bg-background rounded px-1">{s.id}</code>
                  <span className={STATUS_COLORS[s.status] ?? ""}>
                    {STATUS_LABELS[s.status] ?? s.status}
                  </span>
                  <span className="break-all">{s.question}</span>
                </li>
              ))}
            </ul>
          )}
          {[...byRound.entries()].map(([round, list]) => (
            <div
              key={round}
              className="flex flex-col gap-2"
            >
              {deep && (
                <p className="text-muted-foreground text-xs font-medium">
                  第 {round + 1} ラウンド
                </p>
              )}
              {list.map((intent) => {
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
                      {intent.subquestion_id ? (
                        <span className="text-muted-foreground text-xs">
                          （{intent.subquestion_id}）
                        </span>
                      ) : intent.round && !deep ? (
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
                      <p className="pl-6 text-xs text-amber-700">
                        {intent.error}
                      </p>
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
            </div>
          ))}
          {!!trace.adopted?.length && (
            <Section
              title={`採用したカード（引用確認済み）${trace.adopted.length} 件`}
            >
              <ul className="flex flex-col gap-0.5 text-xs">
                {trace.adopted.map((card) => (
                  <li
                    key={card.id}
                    className="flex items-baseline gap-1.5"
                  >
                    <code className="bg-background rounded px-1">
                      {card.id}
                    </code>
                    <span className="min-w-0 truncate">
                      {card.domain || host(card.url)}
                    </span>
                    {!!card.subquestion_ids?.length && (
                      <span className="text-muted-foreground">
                        → {card.subquestion_ids.join(", ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {!!trace.rejected?.length && (
            <Section title={`使わなかったもの ${trace.rejected.length} 件`}>
              <ul className="flex flex-col gap-0.5 text-xs">
                {trace.rejected.map((item, i) => (
                  <li
                    key={`${item.url}-${i}`}
                    className="flex items-baseline gap-1.5"
                  >
                    <span className="text-muted-foreground shrink-0">×</span>
                    <span className="min-w-0 truncate">
                      {item.title || host(item.url)}
                    </span>
                    <span className="text-muted-foreground shrink-0">
                      {item.reason}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          {!!trace.contradictions?.length && (
            <Section title="食い違い">
              <ul className="flex flex-col gap-0.5 text-xs">
                {trace.contradictions.map((c, i) => (
                  <li key={i}>
                    {c.subquestion_id ? `${c.subquestion_id}: ` : ""}
                    {c.summary}
                  </li>
                ))}
              </ul>
            </Section>
          )}
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
            {trace.max_pages
              ? ` ページ上限 ${trace.max_pages}、ラウンド上限 ${trace.max_rounds ?? 1}。`
              : ""}
          </p>
        </div>
      )}
    </div>
  );
}

// --- thinking (think mode only; folded by default, never mixed into the answer) ---------------------------

export type Thought = { stage?: string; text: string };

export function isThinking(value: unknown): value is Thought[] {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((t) => t && typeof (t as Thought).text === "string")
  );
}

export function ThinkingView({ thoughts }: { thoughts: Thought[] }) {
  const [open, setOpen] = useState(false);
  const chars = thoughts.reduce((n, t) => n + t.text.length, 0);
  return (
    <div className="bg-muted/40 w-full min-w-0 rounded-xl border text-sm [overflow-wrap:anywhere]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-muted-foreground flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left"
      >
        {open ? (
          <ChevronDown className="size-4" />
        ) : (
          <ChevronRight className="size-4" />
        )}
        <Brain className="size-4" />
        <span className="text-foreground shrink-0 font-medium whitespace-nowrap">
          思考
        </span>
        <span className="min-w-0 truncate">
          {thoughts
            .map((t) => t.stage)
            .filter(Boolean)
            .join("・")}{" "}
          · {chars} 字
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          {thoughts.map((t, i) => (
            <div
              key={i}
              className="flex flex-col gap-1"
            >
              {t.stage && (
                <p className="text-muted-foreground text-xs font-medium">
                  {t.stage}
                </p>
              )}
              <p className="text-muted-foreground text-xs whitespace-pre-wrap">
                {t.text}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- writing / code steps -----------------------------------------------------------------------------

export type TaskTrace = {
  kind: "write" | "code" | "control";
  steps?: { title: string; body: string }[];
  artifact_dir?: string;
  image?: string;
  status?: string;
  chapters?: number;
  chapter_index?: number;
  chars?: number;
  step?: number;
  max_steps?: number;
};

export function isTaskTrace(value: unknown): value is TaskTrace {
  return (
    !!value &&
    typeof value === "object" &&
    ((value as TaskTrace).kind === "write" ||
      (value as TaskTrace).kind === "code" ||
      (value as TaskTrace).kind === "control")
  );
}

export function TaskTraceView({ trace }: { trace: TaskTrace }) {
  const [open, setOpen] = useState(false);
  const steps = trace.steps ?? [];
  if (!steps.length) return null;
  const Icon =
    trace.kind === "code"
      ? Code2
      : trace.kind === "control"
        ? Workflow
        : PenLine;
  const summary =
    trace.kind === "control"
      ? `${trace.step ?? 0} / ${trace.max_steps ?? "?"} 手`
      : trace.kind === "code"
        ? [trace.image, trace.artifact_dir].filter(Boolean).join(" · ")
        : [
            trace.chapters
              ? `${Math.min(trace.chapter_index ?? 0, trace.chapters)} / ${trace.chapters} 章`
              : null,
            trace.chars ? `${trace.chars} 字` : null,
          ]
            .filter(Boolean)
            .join(" · ");
  return (
    <div className="bg-muted/40 w-full min-w-0 rounded-xl border text-sm [overflow-wrap:anywhere]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-muted-foreground flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left"
      >
        {open ? (
          <ChevronDown className="size-4" />
        ) : (
          <ChevronRight className="size-4" />
        )}
        <Icon className="size-4" />
        <span className="text-foreground shrink-0 font-medium whitespace-nowrap">
          {trace.kind === "code"
            ? "コードの手順"
            : trace.kind === "control"
              ? "自律の手順"
              : "執筆の手順"}
        </span>
        <span className="min-w-0 truncate">{summary}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          {steps.map((step, i) => (
            <div
              key={i}
              className="flex flex-col gap-1"
            >
              <p className="text-muted-foreground text-xs font-medium">
                {step.title}
              </p>
              <p className="text-xs break-all whitespace-pre-wrap">
                {step.body}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// --- claim verification (additional_kwargs.claim_trace): each claim against the cards ------------------------

type ClaimRow = {
  claim_id: string;
  status: "supported" | "partial" | "contradicted" | "unsupported" | "opinion";
  text: string;
  n?: number[];
  note?: string;
};
export type ClaimTrace = {
  claims?: ClaimRow[];
  error?: string;
  audit?: {
    checked?: number;
    dropped?: { text: string; status: string; note?: string }[];
  };
  evidence?: { n: number; locator: string; source_type?: string }[];
};

const CLAIM_LABELS: Record<string, string> = {
  supported: "支持",
  partial: "一部",
  contradicted: "矛盾",
  unsupported: "出典なし",
  opinion: "意見",
};
const CLAIM_COLORS: Record<string, string> = {
  supported: "text-emerald-700",
  partial: "text-amber-700",
  contradicted: "text-red-700",
  unsupported: "text-muted-foreground",
  opinion: "text-muted-foreground",
};
const CLAIM_ERRORS: Record<string, string> = {
  json: "突き合わせに失敗（JSON）",
  timeout: "制限時間で打ち切り",
  model: "モデルを起動できない",
};

export function isClaimTrace(value: unknown): value is ClaimTrace {
  return (
    !!value &&
    typeof value === "object" &&
    (Array.isArray((value as ClaimTrace).claims) ||
      !!(value as ClaimTrace).error)
  );
}

export function ClaimTraceView({ trace }: { trace: ClaimTrace }) {
  const [open, setOpen] = useState(false);
  const claims = trace.claims ?? [];
  const count = (s: string) => claims.filter((c) => c.status === s).length;
  const dropped = trace.audit?.dropped ?? [];
  const summary = trace.error
    ? (CLAIM_ERRORS[trace.error] ?? trace.error)
    : [
        `支持 ${count("supported")}`,
        `一部 ${count("partial")}`,
        count("contradicted") ? `矛盾 ${count("contradicted")}` : null,
        `出典なし ${count("unsupported")}`,
        trace.audit?.checked
          ? `監査 ${trace.audit.checked} 文・削除 ${dropped.length}`
          : null,
      ]
        .filter(Boolean)
        .join(" · ");
  return (
    <div className="bg-muted/40 w-full min-w-0 rounded-xl border text-sm [overflow-wrap:anywhere]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="text-muted-foreground flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left"
      >
        {open ? (
          <ChevronDown className="size-4" />
        ) : (
          <ChevronRight className="size-4" />
        )}
        <ShieldCheck className="size-4" />
        <span className="text-foreground shrink-0 font-medium whitespace-nowrap">
          主張の突き合わせ
        </span>
        <span className="min-w-0 truncate">{summary}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-3 border-t px-3 py-3">
          {claims.length > 0 && (
            <ul className="flex flex-col gap-0.5 text-xs">
              {claims.map((c) => (
                <li
                  key={c.claim_id}
                  className="flex items-baseline gap-2"
                >
                  <code className="bg-background rounded px-1">
                    {c.claim_id}
                  </code>
                  <span className={`shrink-0 ${CLAIM_COLORS[c.status] ?? ""}`}>
                    {CLAIM_LABELS[c.status] ?? c.status}
                  </span>
                  <span className="text-muted-foreground shrink-0">
                    {(c.n ?? []).map((n) => `[${n}]`).join(" ")}
                  </span>
                  <span className="break-all">
                    {c.text}
                    {c.note && c.status !== "supported" ? (
                      <span className="text-muted-foreground">
                        {" "}
                        （{c.note}）
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {dropped.length > 0 && (
            <Section title={`監査で本文から削除した文 ${dropped.length} 件`}>
              <ul className="flex flex-col gap-0.5 text-xs">
                {dropped.map((d, i) => (
                  <li key={i}>
                    <span className={CLAIM_COLORS[d.status] ?? ""}>
                      {CLAIM_LABELS[d.status] ?? d.status}
                    </span>{" "}
                    <span className="break-all">{d.text}</span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <p className="text-muted-foreground text-xs">
            判定は出典カードの抜粋だけを根拠にし、支持された主張だけで回答を書きます。
            抜粋と語句・数値が合わない主張は採用しません。
          </p>
        </div>
      )}
    </div>
  );
}

// --- the run's mode (速い / 思考, and why 自動 chose it) ------------------------------------------------------

export type ChatModeInfo = {
  mode: "fast" | "think";
  requested?: "fast" | "think" | "auto";
  reason?: string;
  label?: string;
  // e.g. "Bonsai 2 27B abliterated は思考を表示しません" (a model without visible thinking in think mode)
  note?: string;
};

export function isChatModeInfo(value: unknown): value is ChatModeInfo {
  return (
    !!value &&
    typeof value === "object" &&
    ((value as ChatModeInfo).mode === "fast" ||
      (value as ChatModeInfo).mode === "think")
  );
}

export function ChatModeBadge({ info }: { info: ChatModeInfo }) {
  const label = info.mode === "think" ? "思考" : "速い";
  return (
    <span
      className="text-muted-foreground text-xs"
      title={info.reason || undefined}
    >
      {info.requested === "auto"
        ? `自動 → ${label}${info.reason ? `（${info.reason}）` : ""}`
        : label}
      {info.note ? ` ・ ${info.note}` : ""}
    </span>
  );
}
