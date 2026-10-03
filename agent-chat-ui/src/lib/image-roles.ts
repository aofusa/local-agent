// Reference image roles understood by the local-agent graph (block.metadata.role / metadata.strength).
// Ranges and defaults mirror src/furry_agent/planner.py; the server clamps again.

export const MAX_IMAGES = 4;

export type ImageRole =
  "auto" | "style" | "pose" | "character" | "base" | "mask";

export interface ImageRoleInfo {
  value: ImageRole;
  label: string;
  strength?: { label: string; min: number; max: number; default: number };
}

export const IMAGE_ROLES: ImageRoleInfo[] = [
  { value: "auto", label: "自動（指示から判断）" },
  {
    value: "character",
    label: "キャラクター",
    strength: { label: "強度", min: 0.4, max: 1.2, default: 0.85 },
  },
  {
    value: "pose",
    label: "ポーズ・構図",
    strength: { label: "強度", min: 0.3, max: 1.2, default: 0.8 },
  },
  {
    value: "style",
    label: "画風",
    strength: { label: "強度", min: 0.2, max: 1.0, default: 0.55 },
  },
  {
    value: "base",
    label: "修正する元画像",
    strength: { label: "denoise", min: 0.15, max: 0.85, default: 0.45 },
  },
  { value: "mask", label: "マスク（白=変更）" },
];

export function roleLabel(role: unknown): string | undefined {
  return IMAGE_ROLES.find((r) => r.value === role)?.label;
}
