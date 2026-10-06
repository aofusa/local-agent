// local-agent: the response time under a chat-tab reply (additional_kwargs.response_time, measured on the host
// from when the message's work began; the time spent waiting for an approval is not counted).
import { Timer } from "lucide-react";

export interface ResponseTime {
  seconds: number;
}

export function isResponseTime(value: unknown): value is ResponseTime {
  return (
    !!value &&
    typeof value === "object" &&
    typeof (value as ResponseTime).seconds === "number" &&
    Number.isFinite((value as ResponseTime).seconds)
  );
}

/** 0.4 秒 / 12.3 秒 / 2 分 05 秒 / 1 時間 02 分 */
export function formatSeconds(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} 秒`;
  const total = Math.round(s);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours > 0) return `${hours} 時間 ${String(minutes).padStart(2, "0")} 分`;
  return `${minutes} 分 ${String(rest).padStart(2, "0")} 秒`;
}

export function ResponseTimeLine({ time }: { time: ResponseTime }) {
  return (
    <span
      className="text-muted-foreground inline-flex items-center gap-1 text-xs whitespace-nowrap"
      title={`応答時間 ${time.seconds} 秒`}
    >
      <Timer className="size-3" />
      応答時間 {formatSeconds(time.seconds)}
    </span>
  );
}
