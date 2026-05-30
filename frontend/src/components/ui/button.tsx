import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-hud text-xs transition-all duration-200 disabled:pointer-events-none disabled:opacity-40 focus-visible:outline-none",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:opacity-95 box-glow-iris dark:hover:shadow-[0_0_40px_rgba(183,177,255,0.5)]",
        signal:
          "bg-signal text-[#0a0f02] hover:bg-signal-dim shadow-[0_0_28px_rgba(201,242,78,0.35)]",
        outline:
          "border border-primary/40 bg-transparent text-primary hover:border-primary hover:bg-primary/10",
        ghost:
          "border border-border bg-transparent text-foreground/70 hover:border-foreground/40 hover:text-foreground",
        link: "text-primary underline-offset-4 hover:underline",
        destructive:
          "bg-destructive text-destructive-foreground hover:opacity-90",
      },
      size: {
        default: "h-11 px-6",
        sm: "h-9 px-4",
        lg: "h-14 px-8 text-sm",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
