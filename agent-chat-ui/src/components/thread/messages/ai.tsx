import { parsePartialJson } from "@langchain/core/output_parsers";
import { useStreamContext } from "@/providers/Stream";
import { AIMessage, Checkpoint, Message } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/langgraph-sdk/react";
import { getContentString } from "../utils";
import { BranchSwitcher, CommandBar } from "./shared";
import { MarkdownText } from "../markdown-text";
import { LoadExternalComponent } from "@langchain/langgraph-sdk/react-ui";
import { cn } from "@/lib/utils";
import { ModelInfoLine, isModelInfo } from "../model-picker";
import { ResponseTimeLine, isResponseTime } from "./response-time";
import { ToolCalls, ToolResult } from "./tool-calls";
import { MessageContentComplex } from "@langchain/core/messages";
import { Fragment } from "react/jsx-runtime";
import { isAgentInboxInterruptSchema } from "@/lib/agent-inbox-interrupt";
import { ThreadView } from "../agent-inbox";
import { useQueryState, parseAsBoolean } from "nuqs";
import { GenericInterruptView } from "./generic-interrupt";
import { useArtifact } from "../artifact";
import {
  isBase64ContentBlock,
  isVideoBlock,
  resolveHostUrl,
} from "@/lib/multimodal-utils";
import { useVideoHandoff } from "../video-handoff";
import { Film } from "lucide-react";
import { useState } from "react";
import {
  ChatModeBadge,
  ClaimTraceView,
  SearchTraceView,
  TaskTraceView,
  ThinkingView,
  isChatModeInfo,
  isClaimTrace,
  isSearchTrace,
  isTaskTrace,
  isThinking,
} from "./search-trace";

// local-agent: render base64 image blocks returned by the graph (stock UI renders text only).
function AIImageBlocks({ content }: { content: Message["content"] }) {
  if (!Array.isArray(content)) return null;
  type ImageBlock = {
    type: string;
    mimeType: string;
    data: string;
    metadata?: { name?: string };
  };
  const images = (content as unknown[]).filter(
    (block): block is ImageBlock =>
      isBase64ContentBlock(block) &&
      (block as ImageBlock).type === "image" &&
      typeof (block as ImageBlock).data === "string",
  );
  if (!images.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {images.map((image, i) => (
        <AIImage
          key={`${image.metadata?.name || "image"}-${i}`}
          image={image}
          index={i}
        />
      ))}
    </div>
  );
}

function AIImage({
  image,
  index,
}: {
  image: { mimeType: string; data: string; metadata?: { name?: string } };
  index: number;
}) {
  // local-agent: on the image tab, hand the picture to the video tab (not the pose preview: it is an input).
  const animate = useVideoHandoff();
  const src = `data:${image.mimeType};base64,${image.data}`;
  const name = image.metadata?.name || `image-${index + 1}.png`;
  return (
    <figure className="flex flex-col items-start gap-1">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={name}
        className="max-h-[80vh] max-w-full rounded-md border object-contain"
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <a
          href={src}
          download={name}
          className="text-muted-foreground text-xs underline [overflow-wrap:anywhere]"
        >
          {name} を保存
        </a>
        {animate && !name.startsWith("pose_") && (
          <button
            type="button"
            onClick={() =>
              animate({ mimeType: image.mimeType, data: image.data, name })
            }
            className="text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-1 text-xs underline"
          >
            <Film className="size-3.5" />
            この画像を動画にする
          </button>
        )}
      </div>
    </figure>
  );
}

// local-agent: render the video graph's mp4. The host serves it (GET /media/video/<id>.mp4) and the block carries
// only the URL, resolved here under the LangGraph URL the UI already talks to.
function AIVideoBlocks({ content }: { content: Message["content"] }) {
  const { apiUrl } = useStreamContext();
  if (!Array.isArray(content)) return null;
  const videos = (content as unknown[]).filter(isVideoBlock);
  if (!videos.length) return null;
  return (
    <div className="flex flex-col gap-2">
      {videos.map((video, i) => {
        let src: string;
        try {
          src = resolveHostUrl(video.url, apiUrl);
        } catch {
          src = video.url;
        }
        return (
          <AIVideo
            key={`${src}-${i}`}
            src={src}
            mimeType={video.mimeType}
            name={video.metadata?.name || `video-${i + 1}.mp4`}
            width={video.metadata?.width}
            height={video.metadata?.height}
          />
        );
      })}
    </div>
  );
}

function AIVideo({
  src,
  mimeType,
  name,
  width,
  height,
}: {
  src: string;
  mimeType: string;
  name: string;
  width?: number;
  height?: number;
}) {
  const [failed, setFailed] = useState(false);
  const download = `${src}${src.includes("?") ? "&" : "?"}download=1`;
  if (failed) {
    return (
      <p className="text-muted-foreground text-sm">
        動画が見つかりません（outputs/video が消された可能性があります）
      </p>
    );
  }
  return (
    <figure className="flex w-full min-w-0 flex-col items-start gap-1">
      {/* The size is known before the file loads: keep the box (and the page) from jumping, never wider than the message. */}
      <video
        controls
        loop
        playsInline
        preload="metadata"
        onError={() => setFailed(true)}
        style={
          width && height
            ? { aspectRatio: `${width} / ${height}`, width: `min(100%, ${width}px)` }
            : undefined
        }
        className="h-auto max-h-[80vh] max-w-full rounded-md border bg-black object-contain"
      >
        <source
          src={src}
          type={mimeType}
          onError={() => setFailed(true)}
        />
      </video>
      <a
        href={download}
        download={name}
        className="text-muted-foreground text-xs underline [overflow-wrap:anywhere]"
      >
        {name} を保存
      </a>
    </figure>
  );
}

function CustomComponent({
  message,
  thread,
}: {
  message: Message;
  thread: ReturnType<typeof useStreamContext>;
}) {
  const artifact = useArtifact();
  const { values } = useStreamContext();
  const customComponents = values.ui?.filter(
    (ui) => ui.metadata?.message_id === message.id,
  );

  if (!customComponents?.length) return null;
  return (
    <Fragment key={message.id}>
      {customComponents.map((customComponent) => (
        <LoadExternalComponent
          key={customComponent.id}
          stream={thread as unknown as ReturnType<typeof useStream>}
          message={customComponent}
          meta={{ ui: customComponent, artifact }}
        />
      ))}
    </Fragment>
  );
}

function parseAnthropicStreamedToolCalls(
  content: MessageContentComplex[],
): AIMessage["tool_calls"] {
  const toolCallContents = content.filter((c) => c.type === "tool_use" && c.id);

  return toolCallContents.map((tc) => {
    const toolCall = tc as Record<string, any>;
    let json: Record<string, any> = {};
    if (toolCall?.input) {
      try {
        json = parsePartialJson(toolCall.input) ?? {};
      } catch {
        // Pass
      }
    }
    return {
      name: toolCall.name ?? "",
      id: toolCall.id ?? "",
      args: json,
      type: "tool_call",
    };
  });
}

interface InterruptProps {
  interrupt?: unknown;
  isLastMessage: boolean;
  hasNoAIOrToolMessages: boolean;
}

function Interrupt({
  interrupt,
  isLastMessage,
  hasNoAIOrToolMessages,
}: InterruptProps) {
  const fallbackValue = Array.isArray(interrupt)
    ? (interrupt as Record<string, any>[])
    : (((interrupt as { value?: unknown } | undefined)?.value ??
        interrupt) as Record<string, any>);

  return (
    <>
      {isAgentInboxInterruptSchema(interrupt) &&
        (isLastMessage || hasNoAIOrToolMessages) && (
          <ThreadView interrupt={interrupt} />
        )}
      {interrupt &&
      !isAgentInboxInterruptSchema(interrupt) &&
      (isLastMessage || hasNoAIOrToolMessages) ? (
        <GenericInterruptView interrupt={fallbackValue} />
      ) : null}
    </>
  );
}

export function AssistantMessage({
  message,
  isLoading,
  handleRegenerate,
}: {
  message: Message | undefined;
  isLoading: boolean;
  handleRegenerate: (parentCheckpoint: Checkpoint | null | undefined) => void;
}) {
  const content = message?.content ?? [];
  const contentString = getContentString(content);
  const [hideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(false),
  );

  const thread = useStreamContext();
  const isLastMessage =
    thread.messages[thread.messages.length - 1].id === message?.id;
  const hasNoAIOrToolMessages = !thread.messages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );
  const meta = message ? thread.getMessagesMetadata(message) : undefined;
  const threadInterrupt = thread.interrupt;

  const parentCheckpoint = meta?.firstSeenState?.parent_checkpoint;
  const anthropicStreamedToolCalls = Array.isArray(content)
    ? parseAnthropicStreamedToolCalls(content)
    : undefined;

  const hasToolCalls =
    message &&
    "tool_calls" in message &&
    message.tool_calls &&
    message.tool_calls.length > 0;
  const toolCallsHaveContents =
    hasToolCalls &&
    message.tool_calls?.some(
      (tc) => tc.args && Object.keys(tc.args).length > 0,
    );
  const hasAnthropicToolCalls = !!anthropicStreamedToolCalls?.length;
  const isToolResult = message?.type === "tool";

  if (isToolResult && hideToolCalls) {
    return null;
  }

  return (
    <div className="group mr-auto flex w-full min-w-0 items-start gap-2">
      <div className="flex w-full min-w-0 flex-col gap-2">
        {isToolResult ? (
          <>
            <ToolResult message={message} />
            <Interrupt
              interrupt={threadInterrupt}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
            />
          </>
        ) : (
          <>
            {/* local-agent: the chat tab's thinking, folded and apart from the answer (think mode only) */}
            {isThinking(message?.additional_kwargs?.thinking) && (
              <ThinkingView thoughts={message.additional_kwargs.thinking} />
            )}

            {contentString.length > 0 && (
              <div className="py-1">
                <MarkdownText>{contentString}</MarkdownText>
              </div>
            )}

            <AIImageBlocks content={content} />
            <AIVideoBlocks content={content} />

            {/* local-agent: the chat tab's search trace */}
            {isSearchTrace(message?.additional_kwargs?.search_trace) && (
              <SearchTraceView trace={message.additional_kwargs.search_trace} />
            )}
            {isClaimTrace(message?.additional_kwargs?.claim_trace) && (
              <ClaimTraceView trace={message.additional_kwargs.claim_trace} />
            )}
            {isTaskTrace(message?.additional_kwargs?.task_trace) && (
              <TaskTraceView trace={message.additional_kwargs.task_trace} />
            )}
            {(isChatModeInfo(message?.additional_kwargs?.chat_mode) ||
              isModelInfo(message?.additional_kwargs?.model_info) ||
              isResponseTime(message?.additional_kwargs?.response_time)) && (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                {isModelInfo(message?.additional_kwargs?.model_info) && (
                  <ModelInfoLine info={message.additional_kwargs.model_info} />
                )}
                {isChatModeInfo(message?.additional_kwargs?.chat_mode) && (
                  <ChatModeBadge info={message.additional_kwargs.chat_mode} />
                )}
                {/* local-agent: how long the reply took (measured on the host) */}
                {isResponseTime(message?.additional_kwargs?.response_time) && (
                  <ResponseTimeLine
                    time={message.additional_kwargs.response_time}
                  />
                )}
              </div>
            )}

            {!hideToolCalls && (
              <>
                {(hasToolCalls && toolCallsHaveContents && (
                  <ToolCalls toolCalls={message.tool_calls} />
                )) ||
                  (hasAnthropicToolCalls && (
                    <ToolCalls toolCalls={anthropicStreamedToolCalls} />
                  )) ||
                  (hasToolCalls && (
                    <ToolCalls toolCalls={message.tool_calls} />
                  ))}
              </>
            )}

            {message && (
              <CustomComponent
                message={message}
                thread={thread}
              />
            )}
            <Interrupt
              interrupt={threadInterrupt}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
            />
            <div
              className={cn(
                "mr-auto flex items-center gap-2 transition-opacity",
                "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100",
                // local-agent: a touch screen has no hover; keep copy / regenerate reachable there.
                "[@media(hover:none)]:opacity-100",
              )}
            >
              <BranchSwitcher
                branch={meta?.branch}
                branchOptions={meta?.branchOptions}
                onSelect={(branch) => thread.setBranch(branch)}
                isLoading={isLoading}
              />
              <CommandBar
                content={contentString}
                isLoading={isLoading}
                isAiMessage={true}
                handleRegenerate={() => handleRegenerate(parentCheckpoint)}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function AssistantMessageLoading() {
  return (
    <div className="mr-auto flex items-start gap-2">
      <div className="bg-muted flex h-8 items-center gap-1 rounded-2xl px-4 py-2">
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_infinite] rounded-full"></div>
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_0.5s_infinite] rounded-full"></div>
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_1s_infinite] rounded-full"></div>
      </div>
    </div>
  );
}
