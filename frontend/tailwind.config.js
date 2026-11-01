import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: { 50: "#fdf2ef", 100: "#fbe0da", 200: "#f6c1b8", 500: "#d9372b", 600: "#c0281e", 700: "#9b1f17", 900: "#4a120e" },
        bear: { 50: "#faf4ee", 100: "#f1e3d2", 300: "#d9a77c", 500: "#a8764f", 700: "#6f4a2d", 900: "#3b2416" },
      },
    },
  },
  plugins: [typography],
};
