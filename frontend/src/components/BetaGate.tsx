import type { ReactNode } from "react";
import { logout, type CurrentUser } from "../lib/auth";

const CONTACT_EMAIL = "nik@nemukai.com";

type BetaGateProps = {
  user: CurrentUser;
  onUserUpdate: (user: CurrentUser) => void;
  children: ReactNode;
};

export function BetaGate({ user, children }: BetaGateProps) {
  if (user.access_status === "granted") {
    return <>{children}</>;
  }
  return <WaitlistScreen user={user} revoked={user.access_status === "revoked"} />;
}

function WaitlistScreen({ user, revoked = false }: { user: CurrentUser; revoked?: boolean }) {
  const onSignOut = async () => {
    await logout();
    window.location.assign("/");
  };

  return (
    <div className="cerno-void cerno-noise relative min-h-screen overflow-hidden">
      <div className="pointer-events-none fixed inset-0 cerno-grid opacity-40" />
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(120%_80%_at_50%_-10%,transparent_40%,rgba(0,0,0,0.85)_100%)]" />

      <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-5xl flex-col px-6 py-8">
        <header className="flex items-center justify-between border-b border-white/[0.08] pb-5">
          <span className="font-display text-lg font-medium tracking-[0.2em] text-foreground">
            CERNO
          </span>
          <button
            type="button"
            onClick={onSignOut}
            className="font-hud text-[10px] text-foreground/55 transition-colors hover:text-iris"
          >
            SIGN OUT
          </button>
        </header>

        <main className="grid flex-1 items-center gap-12 py-12 lg:grid-cols-[1.05fr_0.95fr]">
          <section>
            <div className="flex items-center gap-3 font-hud text-[11px] text-iris">
              <span className="h-px w-8 bg-iris/60" />
              CONTROLLED ACCESS · MANUAL REVIEW
            </div>
            <h1 className="mt-7 max-w-2xl font-display text-5xl leading-[0.95] tracking-[-0.02em] text-foreground glow-iris md:text-7xl">
              {revoked ? "Access withdrawn." : "Under review."}
            </h1>
            <p className="mt-7 max-w-lg text-base leading-7 text-foreground/55">
              {revoked
                ? "This account is not currently eligible for the system. If you believe this is an error, contact the Cerno team."
                : "Cerno is invite-only. Access is verified against an internal approved list after sign-in. We review every request manually."}
            </p>
            <a
              href={`mailto:${CONTACT_EMAIL}?subject=Cerno access request`}
              className="mt-9 inline-flex items-center gap-2 border border-iris/40 px-5 py-3 font-hud text-[11px] text-iris transition-colors hover:bg-iris/10"
            >
              CONTACT {CONTACT_EMAIL.toUpperCase()}
              <span className="font-mono">→</span>
            </a>
          </section>

          <section className="relative border border-white/[0.1] bg-[#060509] p-7">
            <div className="font-hud text-[10px] text-foreground/40">ACCESS STATUS</div>
            <div className="mt-10 border-y border-white/[0.08] py-8">
              <div className="flex items-center gap-3">
                <span
                  className={`h-2 w-2 ${revoked ? "bg-alert" : "animate-signal-blink bg-signal"}`}
                />
                <span className="font-display text-3xl text-foreground">
                  {revoked ? "REVOKED" : "PENDING"}
                </span>
              </div>
              <p className="mt-3 text-sm leading-6 text-foreground/50">
                {revoked
                  ? "This account is not eligible for access right now."
                  : "This account is not approved yet. We will reach out by email once it is cleared."}
              </p>
            </div>
            <div className="mt-6">
              <div className="font-hud text-[9px] text-foreground/40">SIGNED IN AS</div>
              <div className="mt-2 break-all font-mono text-sm text-iris">{user.email}</div>
            </div>
            <div className="mt-7 grid grid-cols-3 border border-white/[0.08] text-center font-hud text-[9px] text-foreground/40">
              <div className="border-r border-white/[0.08] py-3">GOOGLE</div>
              <div className="border-r border-white/[0.08] py-3">VERIFIED</div>
              <div className="py-3">MANUAL</div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
