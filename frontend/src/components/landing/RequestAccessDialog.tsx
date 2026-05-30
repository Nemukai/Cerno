import { useState } from "react";
import { Loader2, ShieldCheck } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { submitAccessRequest } from "@/lib/api";

type RequestAccessDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type Status = "idle" | "submitting" | "done" | "error";

const FIELD_GROUP = "grid gap-1.5";

export function RequestAccessDialog({ open, onOpenChange }: RequestAccessDialogProps) {
  const [status, setStatus] = useState<Status>("idle");
  const [requestId, setRequestId] = useState<string>("");
  const [error, setError] = useState<string>("");

  const reset = () => {
    setStatus("idle");
    setRequestId("");
    setError("");
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    const email = String(form.get("email") ?? "").trim();
    const organization = String(form.get("organization") ?? "").trim();
    const role = String(form.get("role") ?? "").trim();
    const message = String(form.get("message") ?? "").trim();
    const company = String(form.get("company") ?? "").trim();

    if (!name || !email || !organization) {
      setError("Name, email and organization are required.");
      setStatus("error");
      return;
    }

    setStatus("submitting");
    setError("");
    try {
      const result = await submitAccessRequest({
        name,
        email,
        organization,
        role,
        message,
        company,
      });
      setRequestId(result.id);
      setStatus("done");
    } catch {
      setError("Transmission failed. Please try again or email nik@nemukai.com.");
      setStatus("error");
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setTimeout(reset, 250);
      }}
    >
      <DialogContent>
        {status === "done" ? (
          <div className="flex flex-col items-start gap-5 py-2">
            <div className="flex h-12 w-12 items-center justify-center border border-signal/40 text-signal box-glow-iris">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <DialogHeader>
              <DialogTitle className="glow-iris">Request transmitted</DialogTitle>
              <DialogDescription>
                Your access request has been logged and routed to the Cerno team.
                We review every request manually and will reach out by email.
              </DialogDescription>
            </DialogHeader>
            <div className="w-full border border-border bg-background px-3 py-2 font-hud text-[10px] text-muted-foreground">
              REF // {requestId.slice(0, 18) || "PENDING"}
            </div>
            <Button variant="outline" className="w-full" onClick={() => onOpenChange(false)}>
              Close
            </Button>
          </div>
        ) : (
          <>
            <DialogHeader>
              <div className="font-hud text-[10px] text-iris/70">Controlled access · manual review</div>
              <DialogTitle>Request access</DialogTitle>
              <DialogDescription>
                Cerno is deployed to vetted organizations. Tell us who you are and
                what you need to analyze. The team evaluates each request.
              </DialogDescription>
            </DialogHeader>

            <form onSubmit={handleSubmit} className="grid gap-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <div className={FIELD_GROUP}>
                  <Label htmlFor="ra-name">Full name *</Label>
                  <Input id="ra-name" name="name" autoComplete="name" required placeholder="Jane Doe" />
                </div>
                <div className={FIELD_GROUP}>
                  <Label htmlFor="ra-email">Work email *</Label>
                  <Input
                    id="ra-email"
                    name="email"
                    type="email"
                    autoComplete="email"
                    required
                    placeholder="jane@agency.gov.in"
                  />
                </div>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className={FIELD_GROUP}>
                  <Label htmlFor="ra-org">Organization *</Label>
                  <Input id="ra-org" name="organization" required placeholder="Directorate / firm" />
                </div>
                <div className={FIELD_GROUP}>
                  <Label htmlFor="ra-role">Role</Label>
                  <Input id="ra-role" name="role" placeholder="Investigator, analyst…" />
                </div>
              </div>
              <div className={FIELD_GROUP}>
                <Label htmlFor="ra-message">What do you need to analyze?</Label>
                <Textarea
                  id="ra-message"
                  name="message"
                  placeholder="Brief description of the data and the questions you're chasing."
                />
              </div>

              {/* Honeypot: hidden from humans, catches bots */}
              <div className="absolute -left-[9999px] h-0 w-0 overflow-hidden" aria-hidden="true">
                <label>
                  Company
                  <input name="company" tabIndex={-1} autoComplete="off" />
                </label>
              </div>

              {status === "error" && (
                <div className="border border-destructive/40 bg-destructive/10 px-3 py-2 font-mono text-xs text-destructive">
                  {error}
                </div>
              )}

              <Button type="submit" disabled={status === "submitting"} className="w-full">
                {status === "submitting" ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Transmitting
                  </>
                ) : (
                  "Transmit request"
                )}
              </Button>
              <p className="text-center font-mono text-[11px] text-muted-foreground/70">
                Or email{" "}
                <a className="text-iris hover:underline" href="mailto:nik@nemukai.com">
                  nik@nemukai.com
                </a>
              </p>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
