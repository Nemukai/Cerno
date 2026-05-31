import { useCallback, useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import { useUser, googleLoginUrl } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { ThemeProvider, ThemeToggle } from "@/components/Theme";
import { DitherOrb } from "./DitherOrb";
import { RequestAccessDialog } from "./RequestAccessDialog";

const BUILD = "BUILD 0xA17F · NODE EGH-9320";

export function LandingPage({ onEnter }: { onEnter: () => void }) {
  return (
    <ThemeProvider defaultTheme="dark">
      <LandingInner onEnter={onEnter} />
    </ThemeProvider>
  );
}

function LandingInner({ onEnter }: { onEnter: () => void }) {
  const { user, loading } = useUser();
  const [requestOpen, setRequestOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!loading && user && user.access_status === "granted") onEnter();
  }, [user, loading, onEnter]);

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const el = rootRef.current;
    if (!el) return;
    el.style.setProperty("--mx", `${e.clientX}px`);
    el.style.setProperty("--my", `${e.clientY}px`);
  }, []);

  // Only show the transition screen when actually redirecting a signed-in
  // user. The landing must never block on the /auth/me probe.
  if (!loading && user && user.access_status === "granted") {
    return (
      <div className="cerno-void flex h-screen items-center justify-center font-hud text-xs text-primary">
        <span className="animate-pulse">ENTERING SYSTEM…</span>
      </div>
    );
  }

  const openRequest = () => setRequestOpen(true);

  return (
    <div
      ref={rootRef}
      onPointerMove={onPointerMove}
      className="cerno-void cerno-noise relative min-h-screen overflow-x-hidden"
      style={{ ["--mx" as string]: "50vw", ["--my" as string]: "30vh" }}
    >
      <div
        className="pointer-events-none fixed inset-0 z-0"
        style={{
          background:
            "radial-gradient(420px circle at var(--mx) var(--my), hsl(var(--primary) / 0.10), transparent 70%)",
        }}
      />
      <div className="pointer-events-none fixed inset-0 z-0 bg-[radial-gradient(120%_80%_at_50%_-10%,transparent_40%,hsl(var(--background)/0.9)_100%)]" />

      <Nav onRequest={openRequest} />
      <Hero onRequest={openRequest} />
      <Ticker />
      <Capabilities onRequest={openRequest} />
      <UncoverBand />
      <PipelineSection />
      <AccessBand onRequest={openRequest} />
      <Footer onRequest={openRequest} />

      <RequestAccessDialog open={requestOpen} onOpenChange={setRequestOpen} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Brand sigil                                                        */
/* ------------------------------------------------------------------ */
function Sigil({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" stroke="currentColor" strokeOpacity="0.35" />
      <circle cx="16" cy="16" r="9.5" stroke="currentColor" strokeOpacity="0.55" />
      <circle cx="16" cy="16" r="3.4" fill="currentColor" />
      <path d="M16 1.5v5M16 25.5v5M1.5 16h5M25.5 16h5" stroke="currentColor" strokeOpacity="0.5" />
    </svg>
  );
}

function HudDot({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 font-hud text-[10px] text-foreground/55">
      <span className="h-1.5 w-1.5 animate-signal-blink bg-signal" />
      {label}
    </span>
  );
}

function Clock() {
  const [now, setNow] = useState("--:--:--");
  useEffect(() => {
    const tick = () =>
      setNow(new Date().toLocaleTimeString("en-GB", { hour12: false, timeZone: "UTC" }) + " UTC");
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);
  return <span className="font-hud text-[10px] text-foreground/45 tabular-nums">{now}</span>;
}

/* ------------------------------------------------------------------ */
/* Nav                                                                */
/* ------------------------------------------------------------------ */
function Nav({ onRequest }: { onRequest: () => void }) {
  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-border bg-background/60 backdrop-blur-xl">
      <div className="mx-auto flex h-14 max-w-[1400px] items-center justify-between px-5 sm:px-8">
        <div className="flex items-center gap-3 text-primary">
          <Sigil className="h-5 w-5" />
          <span className="font-display text-lg font-medium tracking-[0.2em] text-foreground">
            CERNO
          </span>
          <span className="hidden font-hud text-[9px] text-foreground/35 sm:inline">
            INTELLIGENCE SYSTEM
          </span>
        </div>
        <div className="flex items-center gap-4">
          <div className="hidden items-center gap-5 md:flex">
            <Clock />
            <HudDot label="ONLINE" />
          </div>
          <ThemeToggle />
          <a
            href={googleLoginUrl()}
            className="hidden font-hud text-[10px] text-foreground/55 transition-colors hover:text-primary sm:inline"
          >
            SIGN IN
          </a>
          <Button size="sm" onClick={onRequest}>
            REQUEST ACCESS
          </Button>
        </div>
      </div>
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Hero                                                               */
/* ------------------------------------------------------------------ */
function Hero({ onRequest }: { onRequest: () => void }) {
  return (
    <section className="relative isolate flex min-h-screen items-center overflow-hidden px-5 pt-14 sm:px-8">
      <div className="pointer-events-none absolute inset-0 cerno-grid opacity-[0.55]" />
      <div className="pointer-events-none absolute inset-0 cerno-scanlines opacity-60" />
      <CornerLabels />

      <div className="pointer-events-none absolute right-[-15%] top-1/2 hidden h-[80vmin] w-[80vmin] -translate-y-1/2 lg:block">
        <DitherOrb className="h-full w-full" />
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_52%,hsl(var(--background)/0.55)_86%)]" />
      </div>
      <div className="pointer-events-none absolute inset-0 z-[5] hidden bg-[linear-gradient(90deg,hsl(var(--background))_0%,hsl(var(--background))_36%,hsl(var(--background)/0.62)_54%,transparent_74%)] lg:block" />

      <div className="relative z-10 mx-auto w-full max-w-[1400px]">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 1 }}
          className="max-w-3xl"
        >
          <div className="flex items-center gap-3 font-hud text-[11px] text-primary">
            <span className="h-px w-8 bg-primary/60" />
            DATA INTELLIGENCE · CONTROLLED ACCESS
          </div>

          <h1 className="mt-7 font-display text-[clamp(4.5rem,16vw,15rem)] font-medium leading-[0.82] tracking-[-0.04em] text-foreground glow-iris">
            CERNO
          </h1>

          <p className="mt-8 max-w-2xl font-display text-3xl leading-[1.1] tracking-tight text-foreground sm:text-4xl">
            Nothing stays hidden in the data.
          </p>
          <p className="mt-6 max-w-xl text-base leading-7 text-foreground/60">
            Cerno is custom tuned to your business. It ingests any dataset, resolves
            the hidden links between records, surfaces the anomalies others miss, and
            answers investigative questions with computed, verifiable evidence.
          </p>

          <div className="mt-10 flex flex-col items-start gap-4 sm:flex-row sm:items-center">
            <Button size="lg" onClick={onRequest} className="group">
              REQUEST ACCESS
              <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
            </Button>
            <a
              href={googleLoginUrl()}
              className="group flex items-center gap-2 font-hud text-xs text-foreground/60 transition-colors hover:text-primary"
            >
              ENTER SYSTEM
              <ArrowUpRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
            </a>
          </div>

          <div className="mt-14 grid max-w-2xl grid-cols-2 gap-px border border-border bg-border sm:grid-cols-4">
            <Stat k="ENGINE" v="LINK-AWARE" />
            <Stat k="DETECTION" v="ADVANCED" />
            <Stat k="AI MODELS" v="STATE OF THE ART" />
            <Stat k="DEPLOYMENT" v="CUSTOM TUNED" />
          </div>
        </motion.div>
      </div>
    </section>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="bg-card px-4 py-3">
      <div className="font-hud text-[9px] text-foreground/40">{k}</div>
      <div className="mt-1 font-display text-base text-primary">{v}</div>
    </div>
  );
}

function CornerLabels() {
  return (
    <>
      <span className="pointer-events-none absolute left-5 top-20 hidden font-hud text-[9px] text-foreground/25 sm:left-8 md:block">
        LAT 22.7196 · LON 75.8577
      </span>
      <span className="pointer-events-none absolute right-5 top-20 hidden font-hud text-[9px] text-foreground/25 sm:right-8 md:block">
        SECTOR // INTEL
      </span>
      <span className="pointer-events-none absolute bottom-24 left-5 hidden font-hud text-[9px] text-foreground/25 sm:left-8 md:block">
        CERNŌ · LAT · TO DISCERN · TO PERCEIVE · TO DECIDE
      </span>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Ticker                                                             */
/* ------------------------------------------------------------------ */
function Ticker() {
  const tokens = [
    "LINK CONFIRMED",
    "ANOMALY FLAGGED",
    "SCHEMA APPROVED",
    "ENTITY RESOLVED",
    "PATTERN MATCHED",
    "REFERENTIAL INTEGRITY",
    "OUTLIER DETECTED",
    "QUERY COMPUTED",
    "EVIDENCE ATTACHED",
  ];
  const line = tokens.join("   ◇   ");
  return (
    <div className="relative z-10 overflow-hidden border-y border-border bg-card py-2.5">
      <div className="flex w-max animate-marquee whitespace-nowrap font-hud text-[10px] text-foreground/40">
        <span className="px-4">{line}</span>
        <span className="px-4">{line}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Capability micro-visualizations                                    */
/* ------------------------------------------------------------------ */
function LinkViz() {
  return (
    <svg viewBox="0 0 120 80" className="h-24 w-full" fill="none" aria-hidden="true">
      <line x1="24" y1="22" x2="60" y2="40" stroke="currentColor" strokeOpacity="0.35" />
      <line x1="24" y1="58" x2="60" y2="40" stroke="currentColor" strokeOpacity="0.35" />
      <line x1="60" y1="40" x2="96" y2="22" stroke="#C9F24E" strokeWidth="1.5" />
      <line x1="60" y1="40" x2="96" y2="58" stroke="currentColor" strokeOpacity="0.35" />
      {[
        [24, 22],
        [24, 58],
        [96, 22],
        [96, 58],
      ].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="5" stroke="currentColor" strokeOpacity="0.6" />
      ))}
      <circle cx="60" cy="40" r="6.5" fill="currentColor" />
      <circle cx="96" cy="22" r="5.5" stroke="#C9F24E" strokeWidth="1.5" />
    </svg>
  );
}

function AnomalyViz() {
  const bars = [26, 34, 30, 38, 64, 32, 28, 36, 30];
  return (
    <svg viewBox="0 0 120 80" className="h-24 w-full" fill="none" aria-hidden="true">
      {bars.map((h, i) => {
        const outlier = h > 50;
        return (
          <rect
            key={i}
            x={8 + i * 13}
            y={72 - h}
            width="7"
            height={h}
            fill={outlier ? "#C9F24E" : "currentColor"}
            fillOpacity={outlier ? 1 : 0.3}
          />
        );
      })}
      <circle cx="63.5" cy="6" r="4" stroke="#C9F24E" strokeWidth="1.5" />
      <line x1="63.5" y1="10" x2="63.5" y2="16" stroke="#C9F24E" strokeWidth="1.5" />
    </svg>
  );
}

function ChatViz() {
  return (
    <svg viewBox="0 0 120 80" className="h-24 w-full" fill="none" aria-hidden="true">
      <rect x="14" y="14" width="62" height="18" stroke="currentColor" strokeOpacity="0.4" />
      <line x1="22" y1="23" x2="58" y2="23" stroke="currentColor" strokeOpacity="0.5" />
      <rect x="44" y="40" width="62" height="26" fill="currentColor" fillOpacity="0.08" stroke="currentColor" strokeOpacity="0.5" />
      <rect x="52" y="58" width="6" height="6" fill="#C9F24E" />
      <rect x="61" y="54" width="6" height="10" fill="currentColor" fillOpacity="0.6" />
      <rect x="70" y="50" width="6" height="14" fill="currentColor" fillOpacity="0.6" />
      <rect x="79" y="56" width="6" height="8" fill="currentColor" fillOpacity="0.6" />
    </svg>
  );
}

function SchemaViz() {
  return (
    <svg viewBox="0 0 120 80" className="h-24 w-full" fill="none" aria-hidden="true">
      <rect x="46" y="10" width="28" height="14" stroke="currentColor" strokeOpacity="0.6" />
      <line x1="60" y1="24" x2="60" y2="34" stroke="currentColor" strokeOpacity="0.35" />
      <line x1="26" y1="42" x2="94" y2="42" stroke="currentColor" strokeOpacity="0.35" />
      {[26, 60, 94].map((x, i) => (
        <g key={x}>
          <line x1={x} y1="34" x2={x} y2="42" stroke="currentColor" strokeOpacity="0.35" />
          <rect
            x={x - 13}
            y="48"
            width="26"
            height="12"
            stroke={i === 1 ? "#C9F24E" : "currentColor"}
            strokeWidth={i === 1 ? 1.5 : 1}
            strokeOpacity={i === 1 ? 1 : 0.5}
          />
        </g>
      ))}
    </svg>
  );
}

const CAPABILITIES = [
  {
    id: "LNK-01",
    title: "Link Discovery",
    body: "Cerno reads every column across every file and resolves the keys that connect them, even when no one labeled the relationship.",
    Viz: LinkViz,
  },
  {
    id: "ANM-02",
    title: "Anomaly Detection",
    body: "Advanced statistical engines rank the records that do not belong, so the few that matter rise to the top.",
    Viz: AnomalyViz,
  },
  {
    id: "CHT-03",
    title: "Investigative Chat",
    body: "Ask in plain language. Every number is computed over your real data and returned with the evidence behind it.",
    Viz: ChatViz,
  },
  {
    id: "SCH-04",
    title: "Schema Intelligence",
    body: "Cerno builds a reviewed data guide of grains, glossary, and caveats, so every analysis stays grounded.",
    Viz: SchemaViz,
  },
];

function Capabilities({ onRequest }: { onRequest: () => void }) {
  return (
    <section className="relative z-10 mx-auto max-w-[1400px] px-5 py-28 sm:px-8">
      <SectionHead index="01" label="CAPABILITY MATRIX" title="Engineered to find what hides." />
      <p className="mt-5 max-w-xl text-sm leading-6 text-foreground/55">
        Advanced algorithms and state of the art AI models, custom tuned to your domain.
      </p>
      <div className="mt-12 grid gap-px border border-border bg-border md:grid-cols-2">
        {CAPABILITIES.map((c, i) => (
          <CapabilityCard key={c.id} {...c} delay={i * 0.07} />
        ))}
      </div>
      <div className="mt-10 flex justify-end">
        <button
          onClick={onRequest}
          className="group flex items-center gap-2 font-hud text-[11px] text-foreground/55 transition-colors hover:text-primary"
        >
          REQUEST ACCESS TO THE SYSTEM
          <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-1" />
        </button>
      </div>
    </section>
  );
}

function CapabilityCard({
  id,
  title,
  body,
  Viz,
  delay,
}: {
  id: string;
  title: string;
  body: string;
  Viz: () => JSX.Element;
  delay: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.6, delay, ease: [0.22, 1, 0.36, 1] }}
      className="group relative bg-card p-7 transition-colors duration-300 hover:bg-primary/[0.04]"
    >
      <div className="flex items-center justify-between">
        <span className="font-hud text-[10px] text-primary/70">{id}</span>
        <span className="font-hud text-[9px] text-foreground/30">ACTIVE</span>
      </div>
      <div className="mt-6 text-primary transition-transform duration-300 group-hover:scale-[1.03]">
        <Viz />
      </div>
      <h3 className="mt-7 font-display text-2xl tracking-tight text-foreground">{title}</h3>
      <p className="mt-3 max-w-md text-sm leading-6 text-foreground/55">{body}</p>
    </motion.div>
  );
}

function SectionHead({ index, label, title }: { index: string; label: string; title: string }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3 font-hud text-[10px] text-primary">
        <span className="text-foreground/30">{index}</span>
        <span className="h-px w-8 bg-primary/50" />
        {label}
      </div>
      <h2 className="max-w-2xl font-display text-4xl leading-[1.05] tracking-tight text-foreground sm:text-5xl">
        {title}
      </h2>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Uncover band                                                       */
/* ------------------------------------------------------------------ */
const SECTORS = ["Investigation", "Finance", "Compliance", "Trade", "Operations"];

function UncoverBand() {
  return (
    <section className="relative z-10 overflow-hidden border-y border-border">
      <div className="pointer-events-none absolute right-0 top-1/2 hidden h-[60vmin] w-[60vmin] -translate-y-1/2 opacity-70 md:block">
        <DitherOrb className="h-full w-full" cell={5} />
      </div>
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,hsl(var(--background))_38%,transparent_92%)]" />
      <div className="relative mx-auto max-w-[1400px] px-5 py-32 sm:px-8">
        <div className="font-hud text-[11px] text-primary">02 · FIELD PURPOSE</div>
        <h2 className="mt-6 max-w-3xl font-display text-5xl leading-[1.02] tracking-tight text-foreground sm:text-7xl">
          What will you
          <br />
          <span className="glow-iris text-primary">uncover?</span>
        </h2>
        <p className="mt-7 max-w-xl text-base leading-7 text-foreground/55">
          Built for deep analysis across investigation, finance, compliance, trade,
          and operations. Cerno adapts to your domain and exposes the structure
          others miss.
        </p>
        <div className="mt-9 flex flex-wrap gap-2">
          {SECTORS.map((s) => (
            <span
              key={s}
              className="border border-border bg-card px-3 py-1.5 font-hud text-[10px] text-foreground/60"
            >
              {s}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Pipeline section — UI / CLI tabs                                   */
/* ------------------------------------------------------------------ */
function PipelineSection() {
  const [tab, setTab] = useState<"ui" | "cli">("ui");
  return (
    <section className="relative z-10 mx-auto max-w-[1400px] px-5 py-28 sm:px-8">
      <div className="grid gap-12 lg:grid-cols-[0.8fr_1.2fr] lg:items-start">
        <div>
          <SectionHead index="03" label="THE PRODUCT" title="From raw data to verified answer." />
          <p className="mt-6 max-w-md text-sm leading-6 text-foreground/55">
            Work the way you want. A focused interface for analysts, or a command
            line for power users. Cerno shows its work at every step, so no number is
            a black box.
          </p>
        </div>
        <div>
          <div className="flex gap-px border border-border bg-border">
            <TabButton active={tab === "ui"} onClick={() => setTab("ui")} label="UI" hint="Interface" />
            <TabButton active={tab === "cli"} onClick={() => setTab("cli")} label="CLI" hint="Command line" />
          </div>
          <div className="mt-px border border-border border-t-0">
            {tab === "ui" ? <ChatMock /> : <CliMock />}
          </div>
        </div>
      </div>
    </section>
  );
}

function TabButton({
  active,
  onClick,
  label,
  hint,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  hint: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex flex-1 items-center justify-center gap-2 px-4 py-3 font-hud text-[11px] transition-colors ${
        active ? "bg-card text-primary" : "bg-card/40 text-foreground/45 hover:text-foreground/70"
      }`}
    >
      {label}
      <span className="hidden text-foreground/30 sm:inline">/ {hint}</span>
    </button>
  );
}

function ChatMock() {
  const bars = [
    { label: "ATLAS TRADING", v: 92 },
    { label: "MERIDIAN CO", v: 71 },
    { label: "NORTHGATE", v: 58 },
    { label: "KESTREL LTD", v: 34 },
  ];
  return (
    <div className="bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="font-hud text-[9px] text-foreground/40">CERNO / ASK</span>
        <span className="font-hud text-[9px] text-primary/70">GROUNDED</span>
      </div>
      <div className="space-y-4 px-5 py-6">
        <div className="flex justify-end">
          <p className="max-w-[80%] border border-border bg-background px-3.5 py-2.5 text-sm leading-6 text-foreground/80">
            Which consignees moved the most value last quarter, and is anything
            unusual?
          </p>
        </div>
        <div className="flex justify-start">
          <div className="max-w-[88%] border border-primary/25 bg-primary/[0.05] px-3.5 py-3">
            <p className="text-sm leading-6 text-foreground/80">
              Computed over 4,732 records. Top consignees by declared value:
            </p>
            <div className="mt-3 space-y-1.5">
              {bars.map((b) => (
                <div key={b.label} className="flex items-center gap-3">
                  <span className="w-28 shrink-0 font-mono text-[10px] text-foreground/50">
                    {b.label}
                  </span>
                  <span className="h-2.5 flex-1 bg-border">
                    <span
                      className="block h-full bg-primary"
                      style={{ width: `${b.v}%` }}
                    />
                  </span>
                </div>
              ))}
            </div>
            <p className="mt-3 flex items-center gap-2 font-mono text-[11px] text-foreground/60">
              <span className="h-1.5 w-1.5 bg-signal" />1 consignee flagged: rare route,
              unusually low declared value.
            </p>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 border-t border-border px-4 py-3">
        <span className="flex-1 font-mono text-xs text-foreground/35">
          Ask anything about your data
        </span>
        <span className="flex h-7 w-7 items-center justify-center bg-primary text-primary-foreground">
          <ArrowUpRight className="h-3.5 w-3.5" />
        </span>
      </div>
    </div>
  );
}

const LOG_LINES = [
  "$ cerno ingest  manifest_q3.xlsx",
  "  ↳ 4,732 rows · 18 columns · verified",
  "$ cerno profile",
  "  ↳ types inferred · 6 high-cardinality keys",
  "$ cerno link",
  "  ↳ consignee_id matched party.id   CONFIRMED",
  "  ↳ port_code matched ports.code    CONFIRMED",
  "$ cerno anomaly",
  "  ↳ rare route  ROUTE=KDL-X     FLAGGED",
  "  ↳ integrity   12 orphan refs   FLAGGED",
  '$ cerno ask "top importers by net weight"',
  "  ↳ computed over 4,732 records",
  "  ↳ answer grounded · evidence attached",
];

function CliMock() {
  const [shown, setShown] = useState<string[]>([]);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setShown(LOG_LINES);
      return;
    }
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      if (i > LOG_LINES.length) {
        i = 0;
        setShown([]);
        return;
      }
      setShown(LOG_LINES.slice(0, i));
    }, 700);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="bg-[#08070d]">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-2.5">
        <div className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 border border-white/20" />
          <span className="h-2.5 w-2.5 border border-white/20" />
          <span className="h-2.5 w-2.5 border border-iris/50" />
        </div>
        <span className="font-hud text-[9px] text-white/35">cerno://session/live</span>
      </div>
      <pre className="h-[320px] overflow-hidden px-5 py-4 font-mono text-[12px] leading-6">
        {shown.map((l, i) => (
          <div key={i} className={l.startsWith("$") ? "text-iris" : "text-signal/85"}>
            {l}
          </div>
        ))}
        <span className="inline-block h-3.5 w-2 animate-pulse bg-signal align-middle" />
      </pre>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Access band                                                        */
/* ------------------------------------------------------------------ */
function AccessBand({ onRequest }: { onRequest: () => void }) {
  return (
    <section className="relative z-10 overflow-hidden border-t border-border">
      <div className="pointer-events-none absolute inset-0 cerno-grid-fine opacity-50" />
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[36rem] w-[36rem] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(circle,hsl(var(--primary)/0.12),transparent_60%)]" />
      <div className="relative mx-auto flex max-w-[1400px] flex-col items-center px-5 py-32 text-center sm:px-8">
        <div className="font-hud text-[11px] text-primary">04 · ACCESS PROTOCOL</div>
        <h2 className="mt-6 max-w-3xl font-display text-5xl leading-[1.02] tracking-tight text-foreground glow-iris sm:text-7xl">
          Access is controlled.
        </h2>
        <p className="mt-6 max-w-xl text-base leading-7 text-foreground/55">
          Cerno is deployed to vetted organizations under direct review. Request
          access and the team will evaluate your use case.
        </p>
        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
          <Button size="lg" onClick={onRequest} className="group">
            REQUEST ACCESS
            <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
          </Button>
          <a
            href={googleLoginUrl()}
            className="font-hud text-xs text-foreground/55 transition-colors hover:text-primary"
          >
            ALREADY CLEARED? SIGN IN →
          </a>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Footer                                                             */
/* ------------------------------------------------------------------ */
function Footer({ onRequest }: { onRequest: () => void }) {
  return (
    <footer className="relative z-10 border-t border-border bg-card">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-8 px-5 py-12 sm:px-8 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex items-center gap-3 text-primary">
            <Sigil className="h-5 w-5" />
            <span className="font-display text-base tracking-[0.2em] text-foreground">CERNO</span>
          </div>
          <p className="mt-4 max-w-xs text-xs leading-5 text-foreground/40">
            Link-aware data intelligence. A Nemukai system, operated under controlled
            access.
          </p>
        </div>
        <div className="flex flex-col gap-3 font-hud text-[10px] text-foreground/40">
          <button onClick={onRequest} className="text-left transition-colors hover:text-primary">
            REQUEST ACCESS
          </button>
          <a href={googleLoginUrl()} className="transition-colors hover:text-primary">
            SIGN IN
          </a>
          <a href="mailto:nik@nemukai.com" className="transition-colors hover:text-primary">
            NIK@NEMUKAI.COM
          </a>
        </div>
        <div className="font-hud text-[9px] leading-5 text-foreground/30">
          <div>{BUILD}</div>
          <div>STATUS // RESTRICTED</div>
          <div>© {new Date().getFullYear()} NEMUKAI</div>
        </div>
      </div>
    </footer>
  );
}
