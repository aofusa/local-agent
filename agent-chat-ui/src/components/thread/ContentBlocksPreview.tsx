import React from "react";
import { MultimodalPreview } from "./MultimodalPreview";
import { cn } from "@/lib/utils";
import { ContentBlock } from "@langchain/core/messages";
import { IMAGE_ROLES, ImageRole } from "@/lib/image-roles";

interface ContentBlocksPreviewProps {
  blocks: ContentBlock.Multimodal.Data[];
  onRemove: (idx: number) => void;
  /** Merge metadata (role / strength) into the block at idx. Omit for a read-only preview. */
  onUpdate?: (idx: number, metadata: Record<string, unknown>) => void;
  size?: "sm" | "md" | "lg";
  className?: string;
}

function RoleControls({
  block,
  index,
  onUpdate,
}: {
  block: ContentBlock.Multimodal.Data;
  index: number;
  onUpdate: (metadata: Record<string, unknown>) => void;
}) {
  const role = (block.metadata?.role as ImageRole | undefined) ?? "auto";
  const strength = block.metadata?.strength;
  const roleInfo = IMAGE_ROLES.find((r) => r.value === role);
  return (
    <div className="flex w-28 flex-col gap-1 text-xs">
      <span className="text-gray-500">画像{index + 1}</span>
      <select
        aria-label={`画像${index + 1}の役割`}
        className="rounded border bg-white px-1 py-0.5"
        value={role}
        onChange={(e) => onUpdate({ role: e.target.value })}
      >
        {IMAGE_ROLES.map((r) => (
          <option
            key={r.value}
            value={r.value}
          >
            {r.label}
          </option>
        ))}
      </select>
      {roleInfo?.strength && (
        <input
          aria-label={`画像${index + 1}の${roleInfo.strength.label}`}
          className="rounded border px-1 py-0.5"
          type="number"
          step={0.05}
          min={roleInfo.strength.min}
          max={roleInfo.strength.max}
          placeholder={`${roleInfo.strength.label} ${roleInfo.strength.default}`}
          value={typeof strength === "number" ? strength : ""}
          onChange={(e) =>
            onUpdate({
              strength:
                e.target.value === "" ? undefined : Number(e.target.value),
            })
          }
        />
      )}
    </div>
  );
}

/**
 * Renders a preview of content blocks with optional remove functionality.
 * Image blocks get a role select and an optional strength (sent as block metadata).
 */
export const ContentBlocksPreview: React.FC<ContentBlocksPreviewProps> = ({
  blocks,
  onRemove,
  onUpdate,
  size = "md",
  className,
}) => {
  if (!blocks.length) return null;
  return (
    <div className={cn("flex flex-wrap gap-3 p-3.5 pb-0", className)}>
      {blocks.map((block, idx) => (
        <div
          key={idx}
          className="flex items-start gap-2"
        >
          <MultimodalPreview
            block={block}
            removable
            onRemove={() => onRemove(idx)}
            size={size}
          />
          {onUpdate && block.type === "image" && (
            <RoleControls
              block={block}
              index={idx}
              onUpdate={(metadata) => onUpdate(idx, metadata)}
            />
          )}
        </div>
      ))}
    </div>
  );
};
