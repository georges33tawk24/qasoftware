"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { mediaUrl } from "@/lib/api";
import type { Box, Evidence, EvidencePane } from "@/lib/api";

const PANE_HEIGHT = 340;
const PADDING = 56;

/**
 * The signature element — SPEC §16.
 *
 * A slider-wipe between live and design with the measured deltas overlaid as thin
 * annotated leader lines: a technical drawing rather than a photo filter. Both panes are
 * scaled so the element under discussion is the same size in each, which is the only way
 * a wipe tells you anything.
 */
export function EvidenceViewer({ evidence }: { evidence: Evidence }) {
  const [wipe, setWipe] = useState(50);
  const [width, setWidth] = useState(720);
  const frame = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const anchor = evidence.deltas[0]?.box ?? evidence.live.box;
  const live = useMemo(() => place(evidence.live, anchor, width), [evidence.live, anchor, width]);
  const designPane = evidence.design;
  const design = useMemo(
    () => (designPane ? place(designPane, designPane.box, width) : null),
    [designPane, width],
  );

  return (
    <div className="mt-3">
      <div
        ref={frame}
        className="relative overflow-hidden rounded border border-border bg-raised"
        style={{ height: PANE_HEIGHT }}
      >
        <Pane pane={evidence.live} view={live} deltas={evidence.deltas} label="Live" />

        {designPane && design && (
          <div
            className="absolute inset-0 border-r border-accent"
            style={{ clipPath: `inset(0 ${100 - wipe}% 0 0)` }}
          >
            <Pane
              pane={designPane}
              view={design}
              deltas={[]}
              label={designPane.frame ? `Design · ${designPane.frame}` : "Design"}
              alignLabel="right"
            />
          </div>
        )}
      </div>

      {designPane && (
        <label className="mt-2 flex items-center gap-3 text-xs text-text-3">
          <span className="w-10">Design</span>
          <input
            type="range"
            min={0}
            max={100}
            value={wipe}
            onChange={(event) => setWipe(Number(event.target.value))}
            aria-label="Wipe between the design and the live page"
            className="h-1 flex-1 cursor-ew-resize appearance-none rounded bg-border accent-accent"
          />
          <span className="w-8 text-right">Live</span>
        </label>
      )}
    </div>
  );
}

interface View {
  imageWidth: number;
  zoom: number;
  offsetX: number;
  offsetY: number;
}

/** Scale and offset so `box` lands in the middle of a `width`-wide pane. */
function place(pane: EvidencePane | null, box: Box | undefined, width: number): View {
  if (!pane) return { imageWidth: width, zoom: 1, offsetX: 0, offsetY: 0 };
  const target = Math.max(1, width - PADDING * 2);
  const region = box ?? { x: 0, y: 0, w: target, h: PANE_HEIGHT };
  const zoom = Math.min(
    4,
    Math.max(0.2, Math.min(target / Math.max(region.w, 1), (PANE_HEIGHT - PADDING) / Math.max(region.h, 1))),
  );
  return {
    zoom,
    imageWidth: 0,
    offsetX: PADDING + (target - region.w * zoom) / 2 - region.x * zoom,
    offsetY: (PANE_HEIGHT - region.h * zoom) / 2 - region.y * zoom,
  };
}

function Pane({
  pane,
  view,
  deltas,
  label,
  alignLabel = "left",
}: {
  pane: EvidencePane | null;
  view: View;
  deltas: Evidence["deltas"];
  label: string;
  alignLabel?: "left" | "right";
}) {
  const [natural, setNatural] = useState({ width: 0, height: 0 });
  if (!pane) return null;

  const cssWidth = natural.width / (pane.scale || 1);
  return (
    <div className="absolute inset-0 bg-white">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={mediaUrl(pane.src)}
        alt=""
        onLoad={(event) =>
          setNatural({
            width: event.currentTarget.naturalWidth,
            height: event.currentTarget.naturalHeight,
          })
        }
        style={{
          position: "absolute",
          left: view.offsetX,
          top: view.offsetY,
          width: cssWidth * view.zoom || undefined,
          maxWidth: "none",
          imageRendering: view.zoom > 1.5 ? "pixelated" : "auto",
        }}
      />
      {deltas.map((delta, index) => (
        <LeaderLine key={index} delta={delta} view={view} index={index} />
      ))}
      <span
        className={`absolute top-0 bg-surface/85 px-2 py-1 font-mono text-[11px] text-text-2 ${
          alignLabel === "right" ? "right-0" : "left-0"
        }`}
      >
        {label}
      </span>
    </div>
  );
}

/**
 * A hairline ring on the element and a leader out to its measurement. Thin, straight and
 * annotated — the way a drawing dimensions a part.
 */
function LeaderLine({
  delta,
  view,
  index,
}: {
  delta: Evidence["deltas"][number];
  view: View;
  index: number;
}) {
  const [open, setOpen] = useState(index === 0);
  const left = view.offsetX + delta.box.x * view.zoom;
  const top = view.offsetY + delta.box.y * view.zoom;
  const width = Math.max(2, delta.box.w * view.zoom);
  const height = Math.max(2, delta.box.h * view.zoom);
  const above = top > 64;

  return (
    <div
      className="absolute"
      style={{ left, top, width, height }}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(index === 0)}
    >
      <div className="absolute inset-0 border border-accent" />
      {open && (
        <>
          <div
            className="absolute left-1/2 w-px bg-accent"
            style={above ? { bottom: "100%", height: 22 } : { top: "100%", height: 22 }}
          />
          <div
            className="absolute left-1/2 w-max max-w-[280px] -translate-x-1/2 whitespace-nowrap rounded border border-border bg-surface px-2 py-1 font-mono text-[11px] leading-4 text-text"
            style={above ? { bottom: "calc(100% + 22px)" } : { top: "calc(100% + 22px)" }}
          >
            {delta.expected && <span className="text-text-3">{delta.expected} → </span>}
            <span className="text-blocker">{delta.actual ?? "differs"}</span>
          </div>
        </>
      )}
    </div>
  );
}
