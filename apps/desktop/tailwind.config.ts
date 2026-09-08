import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#24231f",
        paper: "#f5f2ea",
        panel: "#fbfaf6",
        indigo: "#5457a6",
        muted: "#68665f",
        line: "#dfdbd0",
      },
      fontFamily: {
        sans: ["Inter", "Microsoft YaHei UI", "PingFang SC", "sans-serif"],
        serif: ["Noto Serif SC", "Songti SC", "SimSun", "serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
