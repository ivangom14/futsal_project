import typography from "@tailwindcss/typography";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { pitch: { 50: "#effaf3", 100: "#d8f3e2", 500: "#16a34a", 600: "#15803d", 700: "#166534", 900: "#0f3d22" } },
    },
  },
  plugins: [typography],
};
