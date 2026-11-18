// local-agent: 「この画像を動画にする」 under a generated image. Thread provides it on the image tab only: it opens
// the video tab on a new thread with that image as the attachment (docs/image-to-video-design.md §8).
import { createContext, useContext } from "react";

export interface HandoffImage {
  mimeType: string;
  data: string; // base64 without the data: prefix (the upload hook's shape)
  name: string;
}

export const VideoHandoffContext = createContext<
  ((image: HandoffImage) => void) | null
>(null);

/** The hand-off, or null where it does not apply (any tab but the image tab). */
export function useVideoHandoff() {
  return useContext(VideoHandoffContext);
}
