import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Satoshi", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
        display: ["Space Grotesk", "Satoshi", "system-ui", "sans-serif"],
      },
      colors: {
        // Existing Nemukai/light app palette (used by the authenticated app)
        tidepaper: "#F5F1E8",
        paper: "#F5F1E8",
        drift: "#E2DDD1",
        "deep-sea": "#345A67",
        "sea-glass": "#2F8C8F",
        clay: "#C97E52",
        "night-watch": "#2C3338",
        ink: "#2C3338",
        ember: "#C97E52",
        "ember-hover": "#B86F45",

        // Cerno Intelligence (dark) landing palette
        void: "#000000",
        iris: {
          DEFAULT: "#B7B1FF",
          dim: "#8C86C9",
          deep: "#3D3A66",
          ghost: "#1A1930",
        },
        signal: {
          DEFAULT: "#C9F24E",
          dim: "#8FAE38",
        },
        alert: "#FF4D3D",

        // shadcn semantic tokens (mapped to CSS vars defined in styles.css)
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      borderRadius: {
        none: "0",
        sm: "0",
        DEFAULT: "0",
        md: "0",
        lg: "0",
        xl: "0",
        "2xl": "0",
        "3xl": "0",
        full: "9999px",
      },
      keyframes: {
        "iris-pulse": {
          "0%, 100%": { opacity: "0.55", filter: "brightness(1)" },
          "50%": { opacity: "1", filter: "brightness(1.35)" },
        },
        "signal-blink": {
          "0%, 45%, 100%": { opacity: "1" },
          "50%, 95%": { opacity: "0.2" },
        },
        scanline: {
          "0%": { transform: "translateY(-100%)" },
          "100%": { transform: "translateY(100%)" },
        },
        marquee: {
          "0%": { transform: "translateX(0)" },
          "100%": { transform: "translateX(-50%)" },
        },
        flicker: {
          "0%, 19%, 21%, 23%, 80%, 100%": { opacity: "1" },
          "20%, 22%, 81%": { opacity: "0.4" },
        },
        "rise-in": {
          "0%": { opacity: "0", transform: "translateY(14px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
      },
      animation: {
        "iris-pulse": "iris-pulse 3.2s ease-in-out infinite",
        "signal-blink": "signal-blink 2.4s steps(1) infinite",
        scanline: "scanline 7s linear infinite",
        marquee: "marquee 38s linear infinite",
        flicker: "flicker 6s linear infinite",
        "rise-in": "rise-in 0.7s cubic-bezier(0.22,1,0.36,1) both",
      },
    },
  },
  plugins: [animate],
};

export default config;
