import { useEffect, useRef } from "react";

// 4x4 Bayer ordered-dither matrix, normalized to (0,1).
const BAYER = [
  [0, 8, 2, 10],
  [12, 4, 14, 6],
  [3, 11, 1, 9],
  [15, 7, 13, 5],
].map((row) => row.map((v) => (v + 0.5) / 16));

type DitherOrbProps = {
  className?: string;
  cell?: number;
};

/**
 * Generative dithered sphere. A lit sphere is sampled on a grid and ordered-
 * dithered into halftone dots; the light source eases toward the pointer, so
 * the orb "looks" at the cursor. Auto-drifts when idle. Pure canvas 2D.
 */
export function DitherOrb({ className, cell = 6 }: DitherOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = 0;
    let height = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    // Light direction target, driven by pointer (normalized to viewport).
    const light = { x: -0.4, y: -0.5, tx: -0.4, ty: -0.5 };
    const onMove = (e: PointerEvent) => {
      light.tx = (e.clientX / window.innerWidth - 0.5) * 1.8;
      light.ty = (e.clientY / window.innerHeight - 0.5) * 1.8;
    };
    window.addEventListener("pointermove", onMove, { passive: true });

    let raf = 0;
    let t = 0;
    const render = () => {
      t += 0.006;
      // ease light toward pointer, with a slow idle orbit
      const driftX = Math.cos(t * 0.7) * 0.25;
      const driftY = Math.sin(t * 0.9) * 0.2;
      light.x += (light.tx + driftX - light.x) * 0.05;
      light.y += (light.ty + driftY - light.y) * 0.05;

      // light vector (z fixed toward viewer)
      const lz = 0.85;
      const ll = Math.hypot(light.x, light.y, lz) || 1;
      const lx = light.x / ll;
      const ly = light.y / ll;
      const lzn = lz / ll;

      ctx.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height / 2;
      const radius = Math.min(width, height) / 2 - cell;

      for (let py = 0; py < height; py += cell) {
        for (let px = 0; px < width; px += cell) {
          const u = (px + cell / 2 - cx) / radius;
          const v = (py + cell / 2 - cy) / radius;
          const r2 = u * u + v * v;

          if (r2 > 1.0) {
            // sparse outer field — faint scattered specks
            const bx = ((px / cell) | 0) % 4;
            const by = ((py / cell) | 0) % 4;
            if (r2 < 1.9 && BAYER[by]![bx]! > 0.86) {
              const fade = Math.max(0, 1 - (r2 - 1) / 0.9);
              ctx.fillStyle = `rgba(140,134,201,${0.10 * fade})`;
              ctx.fillRect(px + cell / 2 - 0.6, py + cell / 2 - 0.6, 1.2, 1.2);
            }
            continue;
          }

          const z = Math.sqrt(1 - r2);
          // diffuse term
          let intensity = u * lx + v * ly + z * lzn;
          intensity = Math.max(0, intensity);
          // rim light + ambient + slow shimmer
          const rim = Math.pow(1 - z, 2.4) * 0.35;
          const shimmer = 0.04 * Math.sin(t * 2.2 + u * 6 + v * 6);
          let bright = intensity * 0.92 + rim + 0.07 + shimmer;
          bright = Math.min(1, Math.max(0, bright));

          const bx = ((px / cell) | 0) % 4;
          const by = ((py / cell) | 0) % 4;
          const threshold = BAYER[by]![bx]!;
          if (bright <= threshold * 0.9) continue;

          // halftone: dot radius scales with brightness
          const rad = (cell / 2) * (0.35 + bright * 0.72);
          // colour: violet body, white-hot core, occasional signal speck
          let color: string;
          if (bright > 0.93) {
            color = "rgba(245,244,255,0.95)";
          } else if (bright > 0.62) {
            color = `rgba(183,177,255,${0.55 + bright * 0.4})`;
          } else {
            const speck = (((px * 13 + py * 7) | 0) % 97) === 0;
            color = speck
              ? "rgba(201,242,78,0.6)"
              : `rgba(120,114,180,${0.28 + bright * 0.4})`;
          }
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(px + cell / 2, py + cell / 2, rad, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      if (!reduce) raf = requestAnimationFrame(render);
    };
    render();

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("pointermove", onMove);
    };
  }, [cell]);

  return <canvas ref={canvasRef} aria-hidden="true" className={className} />;
}
