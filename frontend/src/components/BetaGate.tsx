import type { ReactNode } from "react";
import { CernoLockup } from "./Brand";
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
  if (user.access_status === "revoked") {
    return <WaitlistScreen user={user} revoked />;
  }
  return <WaitlistScreen user={user} />;
}

function WaitlistScreen({
  user,
  revoked = false,
}: {
  user: CurrentUser;
  revoked?: boolean;
}) {
  const onSignOut = async () => {
    await logout();
    window.location.reload();
  };

  return (
    <div className="min-h-screen overflow-hidden bg-tidepaper text-night-watch">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-6 py-8">
        <header className="flex items-center justify-between border-b border-drift pb-5">
          <CernoLockup
            markClassName="h-7 w-7 text-deep-sea"
            wordmarkClassName="text-xl text-night-watch"
          />
          <button
            type="button"
            onClick={onSignOut}
            className="small-caps border border-drift px-4 py-2 text-xs text-night-watch/60 transition hover:border-night-watch hover:text-night-watch"
          >
            sign out
          </button>
        </header>

        <main className="grid flex-1 items-center gap-10 py-12 lg:grid-cols-[1.08fr_0.92fr]">
          <section>
            <div className="small-caps text-sm text-deep-sea">
              private workspace access
            </div>
            <h1 className="mt-5 max-w-3xl font-serif text-5xl leading-[0.95] tracking-[-0.03em] text-night-watch md:text-7xl">
              You are on the list to be reviewed.
            </h1>
            <p className="mt-7 max-w-xl text-lg leading-8 text-night-watch/65">
              Cerno is currently invite-only. Access is verified against an
              internal approved email list after Google sign-in.
            </p>
            <div className="mt-8 max-w-xl border border-drift bg-white/50 p-5">
              <div className="small-caps text-xs text-night-watch/45">
                signed in as
              </div>
              <div className="mt-2 break-all font-mono text-lg text-night-watch">
                {user.email}
              </div>
            </div>
          </section>

          <section className="relative border border-drift bg-[#fbf7ee] p-6 shadow-[0_24px_80px_rgba(42,38,31,0.08)]">
            <div className="absolute right-5 top-5 grid grid-cols-2 gap-1">
              <span className="h-2 w-2 bg-ember" />
              <span className="h-2 w-2 bg-drift" />
              <span className="h-2 w-2 bg-drift" />
              <span className="h-2 w-2 bg-night-watch" />
            </div>
            <div className="small-caps text-xs text-night-watch/45">
              access status
            </div>
            <div className="mt-12 border-y border-drift py-8">
              <div className="font-mono text-4xl text-night-watch">
                {revoked ? "revoked" : "waiting"}
              </div>
              <p className="mt-3 text-sm leading-6 text-night-watch/60">
                {revoked
                  ? "This account is not eligible for access right now."
                  : "This account is not approved yet. Email Nik and ask to be added to the Cerno access list."}
              </p>
            </div>
            <a
              href={`mailto:${CONTACT_EMAIL}?subject=Cerno access request`}
              className="mt-6 flex items-center justify-between border border-night-watch bg-night-watch px-5 py-4 text-sm text-tidepaper transition hover:bg-deep-sea"
            >
              <span>Email {CONTACT_EMAIL}</span>
              <span className="font-mono">-&gt;</span>
            </a>
            <div className="mt-5 grid grid-cols-3 border border-drift text-center font-mono text-xs text-night-watch/45">
              <div className="border-r border-drift py-3">google</div>
              <div className="border-r border-drift py-3">verified</div>
              <div className="py-3">manual</div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}
