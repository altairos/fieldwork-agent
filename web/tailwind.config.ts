import type { Config } from "tailwindcss";

/* 颜色一律 rgb(var(--fw-*) / <alpha-value>)：值来自 styles/themes.css 的
 * 六套 [data-theme] CSS 变量（RGB 三元组），支持 border-cinnabar/25 等
 * 透明度修饰符。四个主色衍生 token（ontint/tintpress/onindigo/tintline）
 * 原为设计板 token 外用值，主题化时一并语义化。 */
const c = (v: string) => `rgb(var(${v}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        paper: c("--fw-paper"),
        paperdeep: c("--fw-paperdeep"),
        card: c("--fw-card"),
        cardsunk: c("--fw-cardsunk"),
        ink: c("--fw-ink"),
        inksoft: c("--fw-inksoft"),
        inkfaint: c("--fw-inkfaint"),
        hairline: c("--fw-hairline"),
        hairsoft: c("--fw-hairsoft"),
        cinnabar: c("--fw-cinnabar"),
        cinnabarsoft: c("--fw-cinnabarsoft"),
        cinnabarwash: c("--fw-cinnabarwash"),
        indigo: c("--fw-indigo"),
        indigowash: c("--fw-indigowash"),
        bamboo: c("--fw-bamboo"),
        bamboowash: c("--fw-bamboowash"),
        ochre: c("--fw-ochre"),
        ochrewash: c("--fw-ochrewash"),
        danger: c("--fw-danger"),
        /* 主色衍生（原设计板 token 外用值语义化） */
        ontint: c("--fw-ontint"),
        tintpress: c("--fw-tintpress"),
        onindigo: c("--fw-onindigo"),
        tintline: c("--fw-tintline"),
      },
      fontFamily: {
        serif: ['"Noto Serif SC"', '"Songti SC"', '"STSong"', '"SimSun"', "serif"],
        sans: ['"Noto Serif SC"', '"Songti SC"', '"STSong"', '"SimSun"', "serif"],
        num: ['"Fraunces"', '"Noto Serif SC"', '"Songti SC"', "serif"],
      },
      /* 阴影基色随主题（A3 为中性黑），透明度不变 */
      boxShadow: {
        card: `0 1px 2px rgb(var(--fw-shadow) / .05), 0 8px 24px rgb(var(--fw-shadow) / .05)`,
        pop: `0 2px 6px rgb(var(--fw-shadow) / .10), 0 16px 40px rgb(var(--fw-shadow) / .12)`,
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
