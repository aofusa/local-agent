// local-agent: the model picker next to the send button (docs/host-model-selection-design.md §10), like the model
// menu of Claude / Gemini. The list comes from the LangGraph host (GET <apiUrl>/models): only the models the host
// offers can be picked, the run sends the catalog id (chat tab: configurable.inference_model, image tab:
// configurable.image_model) and nothing when the user kept the host's default.
// The choice belongs to one conversation: kept per thread in sessionStorage, a new chat starts on the default,
// a change applies from the next message and never stops the running one.
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronDown, LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type ModelKind = "inference" | "image";

export interface HostModel {
  id: string;
  label: string;
  available: boolean;
  reason: string;
  context?: number;
  thinking?: boolean;
  family?: string;
  family_label?: string;
  ckpt_name?: string;
}

export interface HostModels {
  defaults: { inference: string; image: string };
  inference: HostModel[];
  image: HostModel[];
}

const STORAGE_PREFIX = "cirka.";
const listeners = new Set<() => void>();

function storageKey(kind: ModelKind, threadId: string | null): string {
  const field = kind === "inference" ? "inference_model" : "image_model";
  return threadId
    ? `${STORAGE_PREFIX}thread.${threadId}.${field}`
    : `${STORAGE_PREFIX}draft.${field}`;
}

function readChoice(kind: ModelKind, threadId: string | null): string | null {
  try {
    return window.sessionStorage.getItem(storageKey(kind, threadId));
  } catch {
    return null; // storage refused (private mode): the host's default
  }
}

function writeChoice(
  kind: ModelKind,
  threadId: string | null,
  value: string | null,
) {
  try {
    const key = storageKey(kind, threadId);
    if (value) window.sessionStorage.setItem(key, value);
    else window.sessionStorage.removeItem(key);
  } catch {
    // storage unavailable: the choice lasts while the page stays open
  }
  listeners.forEach((listener) => listener());
}

/** The model picked for this conversation (null = the host's default). */
export function useModelChoice(
  kind: ModelKind,
  threadId: string | null,
): [string | null, (id: string | null) => void] {
  const [value, setValue] = useState<string | null>(null);
  const previousThread = useRef<string | null>(threadId);

  useEffect(() => {
    const was = previousThread.current;
    previousThread.current = threadId;
    if (!was && threadId) {
      // The first message of a new chat created its thread: the draft choice now belongs to that thread.
      const draft = readChoice(kind, null);
      if (draft && !readChoice(kind, threadId))
        writeChoice(kind, threadId, draft);
      writeChoice(kind, null, null);
    } else if (was && !threadId) {
      // "New chat": start on the host's default, not on the previous chat's model.
      writeChoice(kind, null, null);
    }
    setValue(readChoice(kind, threadId));
    const listener = () => setValue(readChoice(kind, threadId));
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, [kind, threadId]);

  const update = useCallback(
    (id: string | null) => writeChoice(kind, threadId, id),
    [kind, threadId],
  );
  return [value, update];
}

/** GET <apiUrl>/models, fetched on mount and whenever ``refresh`` is called (opening the picker). */
export function useHostModels(apiUrl: string | undefined) {
  const [models, setModels] = useState<HostModels | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!apiUrl) return;
    setLoading(true);
    try {
      const response = await fetch(`${apiUrl.replace(/\/$/, "")}/models`, {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setModels((await response.json()) as HostModels);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [apiUrl]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { models, error, loading, refresh };
}

function detail(kind: ModelKind, model: HostModel): string {
  if (kind === "inference") {
    const parts = [];
    if (model.context) parts.push(`context ${model.context}`);
    if (model.thinking === false) parts.push("思考なし");
    return parts.join(" ・ ");
  }
  return [model.family_label || model.family, model.ckpt_name]
    .filter(Boolean)
    .join(" ・ ");
}

export function ModelPicker({
  kind,
  apiUrl,
  value,
  onChange,
  className,
}: {
  kind: ModelKind;
  apiUrl: string | undefined;
  value: string | null;
  onChange: (id: string | null) => void;
  className?: string;
}) {
  const { models, error, loading, refresh } = useHostModels(apiUrl);
  const [open, setOpen] = useState(false);
  // Fixed position from the button: the input area scrolls and would clip an absolutely placed list.
  const [place, setPlace] = useState<{
    right: number;
    bottom?: number;
    top?: number;
    maxHeight: number;
  } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node))
        setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const list = models ? models[kind] : [];
  const defaultId = models?.defaults[kind] ?? "";
  const currentId = value || defaultId;
  const current = list.find((m) => m.id === currentId);
  const name = current?.label || (error ? "モデル" : value || "…");

  const toggle = () => {
    if (!open) {
      void refresh();
      const rect = button.current?.getBoundingClientRect();
      if (rect) {
        const right = Math.max(8, window.innerWidth - rect.right);
        const above = rect.top - 16;
        const below = window.innerHeight - rect.bottom - 16;
        setPlace(
          above >= below
            ? {
                right,
                bottom: window.innerHeight - rect.top + 8,
                maxHeight: above,
              }
            : { right, top: rect.bottom + 8, maxHeight: below },
        );
      }
    }
    setOpen(!open);
  };

  return (
    <div
      ref={root}
      className={cn("relative", className)}
    >
      <button
        ref={button}
        type="button"
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={kind === "inference" ? "推論モデルを選ぶ" : "画像モデルを選ぶ"}
        className="text-muted-foreground hover:text-foreground flex max-w-56 cursor-pointer items-center gap-1 rounded-md px-2 py-1 text-sm"
      >
        <span className="truncate">{name}</span>
        <ChevronDown className="size-4 shrink-0" />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={kind === "inference" ? "推論モデル" : "画像モデル"}
          style={
            place
              ? {
                  right: place.right,
                  bottom: place.bottom,
                  top: place.top,
                  maxHeight: place.maxHeight,
                }
              : undefined
          }
          className="bg-background fixed z-50 w-80 max-w-[90vw] overflow-y-auto rounded-xl border p-1 shadow-lg"
        >
          <div className="text-muted-foreground flex items-center gap-2 px-3 pt-2 pb-1 text-xs">
            {kind === "inference" ? "推論モデル" : "画像モデル"}
            {loading && <LoaderCircle className="size-3 animate-spin" />}
          </div>
          {error && !models && (
            <p className="text-muted-foreground px-3 py-2 text-sm">
              モデル一覧を取得できません（ホストの既定で送ります）
            </p>
          )}
          {list.map((model) => {
            const selected = model.id === currentId;
            return (
              <button
                key={model.id}
                type="button"
                role="option"
                aria-selected={selected}
                disabled={!model.available}
                title={model.available ? model.id : model.reason}
                onClick={() => {
                  onChange(model.id);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-start gap-2 rounded-lg px-3 py-2 text-left",
                  model.available
                    ? "hover:bg-muted cursor-pointer"
                    : "cursor-not-allowed opacity-50",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">
                    {model.label}
                    {model.id === defaultId && (
                      <span className="text-muted-foreground ml-1 text-xs">
                        （既定）
                      </span>
                    )}
                  </span>
                  <span className="text-muted-foreground block truncate text-xs">
                    {model.available ? detail(kind, model) : model.reason}
                  </span>
                </span>
                {selected && <Check className="mt-0.5 size-4 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export interface ModelInfo {
  kind: ModelKind;
  id: string;
  label: string;
}

export function isModelInfo(value: unknown): value is ModelInfo {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as ModelInfo).id === "string" &&
    typeof (value as ModelInfo).label === "string"
  );
}

/** Under a reply: which model wrote it (a mid-chat switch keeps every reply's model visible). */
export function ModelInfoLine({ info }: { info: ModelInfo }) {
  return (
    <span
      className="text-muted-foreground text-xs"
      title={info.id}
    >
      {info.label || info.id}
    </span>
  );
}
