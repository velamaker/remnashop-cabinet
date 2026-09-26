/** @type {import('tailwindcss').Config} */

// Палитра живёт в CSS-переменных (index.css) — тема переключается без пересборки.
// Но простая строка "var(--success)" ломает модификаторы прозрачности: классы
// вида `bg-success/10`, `border-accent/30` не давали НИКАКОГО css и молча
// пропадали. Поэтому цвет объявлен функцией: без модификатора — переменная как
// есть, с модификатором — color-mix (он умеет прозрачность для любого формата
// цвета, включая hex и rgba в переменной).
const token = (name) => ({ opacityValue } = {}) => {
  const alpha = Number(opacityValue);
  if (!Number.isFinite(alpha) || alpha === 1) return `var(${name})`;
  return `color-mix(in srgb, var(${name}) ${alpha * 100}%, transparent)`;
};

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: token("--bg"),
          subtle: token("--bg-subtle"),
          raised: token("--bg-raised"),
          overlay: token("--bg-overlay"),
        },
        border: {
          DEFAULT: token("--border"),
          subtle: token("--border-subtle"),
        },
        fg: {
          DEFAULT: token("--fg"),
          muted: token("--fg-muted"),
          subtle: token("--fg-subtle"),
        },
        accent: {
          DEFAULT: token("--accent"),
          hover: token("--accent-hover"),
          fg: token("--accent-fg"),
          subtle: token("--accent-subtle"),
        },
        success: token("--success"),
        warning: token("--warning"),
        danger: token("--danger"),
      },
      fontFamily: {
        sans: ["var(--font-sans)"],
      },
      borderRadius: {
        sm: "3px",
        md: "5px",
        lg: "7px",
        xl: "9px",
        "2xl": "12px",
        "3xl": "16px",
        xl2: "9px",
      },
      boxShadow: {
        soft: "0 1px 2px rgba(0,0,0,0.05)",
        raised: "0 2px 8px rgba(0,0,0,0.08), 0 0 0 0.5px rgba(0,0,0,0.04)",
        glow: "0 0 0 1px var(--accent-subtle), 0 4px 16px -2px var(--accent-glow)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(3px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [],
};
