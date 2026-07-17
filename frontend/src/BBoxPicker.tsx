import { useRef, useState } from 'react';

export type Box = { cx: number; cy: number; w: number; h: number }; // YOLO, normalized 0-1

// Draw a single bounding box on an image by dragging (touch or mouse).
// Reports the box in YOLO format (normalized center-x/y + width/height).
export default function BBoxPicker({ src, onChange }: { src: string; onChange: (b: Box | null) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  // rect in normalized top-left form while drawing/preview
  const [rect, setRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  function norm(e: React.PointerEvent) {
    const el = ref.current!;
    const r = el.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  }

  function down(e: React.PointerEvent) {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    start.current = norm(e);
    setRect({ x: start.current.x, y: start.current.y, w: 0, h: 0 });
  }

  function move(e: React.PointerEvent) {
    if (!start.current) return;
    const p = norm(e);
    setRect({
      x: Math.min(start.current.x, p.x),
      y: Math.min(start.current.y, p.y),
      w: Math.abs(p.x - start.current.x),
      h: Math.abs(p.y - start.current.y),
    });
  }

  function up() {
    if (!start.current || !rect) return;
    start.current = null;
    if (rect.w < 0.02 || rect.h < 0.02) { setRect(null); onChange(null); return; }
    onChange({ cx: rect.x + rect.w / 2, cy: rect.y + rect.h / 2, w: rect.w, h: rect.h });
  }

  return <div className="bbox-wrap" ref={ref} onPointerDown={down} onPointerMove={move} onPointerUp={up}>
    <img src={src} alt="asset" draggable={false}/>
    {rect && <div className="bbox-rect" style={{
      left: `${rect.x * 100}%`, top: `${rect.y * 100}%`,
      width: `${rect.w * 100}%`, height: `${rect.h * 100}%`,
    }}/>}
    <div className="bbox-hint">{rect ? 'תיבה סומנה — אפשר לצייר מחדש' : 'גרור אצבע/עכבר סביב הנכס'}</div>
  </div>;
}
