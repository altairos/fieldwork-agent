import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: "#F9F6F0",
        paperdeep: "#F1ECE2",
        card: "#FDFCF9",
        cardsunk: "#F4F0E7",
        ink: "#2A2724",
        inksoft: "#5F584C",
        inkfaint: "#948B7A",
        hairline: "#E2D9C8",
        hairsoft: "#EBE5D9",
        cinnabar: "#A8412E",
        cinnabarsoft: "#C9705C",
        cinnabarwash: "#F6EBE6",
        indigo: "#3A5C74",
        indigowash: "#E9EEF1",
        bamboo: "#5E7A4E",
        bamboowash: "#EBF0E5",
        ochre: "#A8802C",
        ochrewash: "#F5EFDF",
        danger: "#9E2F26",
      },
      fontFamily: {
        serif: ['"Noto Serif SC"', '"Songti SC"', '"STSong"', '"SimSun"', "serif"],
        sans: ['"Noto Serif SC"', '"Songti SC"', '"STSong"', '"SimSun"', "serif"],
        num: ['"Fraunces"', '"Noto Serif SC"', '"Songti SC"', "serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgba(60,48,30,.05), 0 8px 24px rgba(60,48,30,.05)",
        pop: "0 2px 6px rgba(60,48,30,.10), 0 16px 40px rgba(60,48,30,.12)",
      },
      borderRadius: {
        sm: "4px",
        md: "8px",
        lg: "14px",
      },
    },
  },
  plugins: [],
} satisfies Config;
