import { ContentBlock } from "@langchain/core/messages";
import { toast } from "sonner";

// Returns a Promise of a typed multimodal block for images or PDFs
export async function fileToContentBlock(
  file: File,
): Promise<ContentBlock.Multimodal.Data> {
  const supportedImageTypes = [
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
  ];
  const supportedFileTypes = [...supportedImageTypes, "application/pdf"];

  if (!supportedFileTypes.includes(file.type)) {
    toast.error(
      `Unsupported file type: ${file.type}. Supported types are: ${supportedFileTypes.join(", ")}`,
    );
    return Promise.reject(new Error(`Unsupported file type: ${file.type}`));
  }

  const data = await fileToBase64(file);

  if (supportedImageTypes.includes(file.type)) {
    return {
      type: "image",
      mimeType: file.type,
      data,
      metadata: { name: file.name },
    };
  }

  // PDF
  return {
    type: "file",
    mimeType: "application/pdf",
    data,
    metadata: { filename: file.name },
  };
}

// Helper to convert File to base64 string
export async function fileToBase64(file: File): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const result = reader.result as string;
      // Remove the data:...;base64, prefix
      resolve(result.split(",")[1]);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// local-agent: a video the video graph returns. The host serves the mp4 (GET /media/video/<id>.mp4) and the
// message carries only its URL, so threads stay small (docs/image-to-video-design.md §6.4).
export interface VideoBlock {
  type: "video";
  mimeType: string;
  url: string;
  metadata?: {
    name?: string;
    width?: number;
    height?: number;
    frames?: number;
    fps?: number;
    seconds?: number;
  };
}

export function isVideoBlock(block: unknown): block is VideoBlock {
  return (
    typeof block === "object" &&
    block !== null &&
    (block as { type?: unknown }).type === "video" &&
    typeof (block as { url?: unknown }).url === "string" &&
    typeof (block as { mimeType?: unknown }).mimeType === "string"
  );
}

/** A URL the host returned (e.g. "/media/video/<id>.mp4"), resolved under the LangGraph URL the UI talks to.
 * The API URL's own path is kept (a proxied "http://host/api" -> "http://host/api/media/..."). */
export function resolveHostUrl(url: string, apiUrl: string): string {
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) return url;
  const base =
    typeof window === "undefined"
      ? apiUrl
      : new URL(apiUrl, window.location.href).toString();
  return new URL(url.replace(/^\/+/, ""), base.replace(/\/*$/, "/")).toString();
}

// Type guard for Base64ContentBlock
export function isBase64ContentBlock(
  block: unknown,
): block is ContentBlock.Multimodal.Data {
  if (typeof block !== "object" || block === null || !("type" in block))
    return false;
  // file type (legacy)
  if (
    (block as { type: unknown }).type === "file" &&
    "mimeType" in block &&
    typeof (block as { mimeType?: unknown }).mimeType === "string" &&
    ((block as { mimeType: string }).mimeType.startsWith("image/") ||
      (block as { mimeType: string }).mimeType === "application/pdf")
  ) {
    return true;
  }
  // image type (new)
  if (
    (block as { type: unknown }).type === "image" &&
    "mimeType" in block &&
    typeof (block as { mimeType?: unknown }).mimeType === "string" &&
    (block as { mimeType: string }).mimeType.startsWith("image/")
  ) {
    return true;
  }
  return false;
}
