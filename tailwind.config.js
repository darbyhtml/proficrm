/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./backend/templates/**/*.html",
    "./backend/**/templates/**/*.html",
    "./backend/static/ui/*.js",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          teal: "#01948E",
          orange: "#FDAD3A",
          dark: "#003D38",
          soft: "#C2E2DE",
        },
      },
    },
  },
  plugins: [],
};
