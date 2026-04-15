/** @type {import('tailwindcss').Config}
 *
 * Дизайн-токены синхронизированы с docs/DESIGN.md (версия 1.0, 2026-04-15).
 *
 * СТРАТЕГИЯ: токены добавляются, а не переопределяют существующие.
 * - Палитра brand.primary/brand.accent — расширенная шкала 50..900 для новых страниц.
 * - Старые алиасы brand.teal/orange/dark/soft — оставлены для существующих шаблонов.
 * - Семантические success/warning/danger/info — новые токены, не конфликтуют.
 * - Шрифт/радиусы/тени — НЕ переопределяем глобально на этом этапе, чтобы не
 *   сдвинуть существующий UI. Редизайн страниц использует новые токены явно.
 *
 * Любое изменение сначала идёт в docs/DESIGN.md, потом сюда, потом rebuild CSS.
 */

const brandPrimary = {
  DEFAULT: "#01948E",
  50: "#E6F4F3",
  100: "#B3DEDC",
  200: "#80C7C4",
  300: "#4DB1AD",
  400: "#269E99",
  500: "#01948E",
  600: "#017E79",
  700: "#01635F",
  800: "#014845",
  900: "#012D2B",
};

const brandAccent = {
  DEFAULT: "#FDAD3A",
  50: "#FEF5E6",
  100: "#FDE4B3",
  200: "#FDD280",
  300: "#FCC14D",
  400: "#FDB542",
  500: "#FDAD3A",
  600: "#E09420",
  700: "#B07619",
  800: "#805511",
  900: "#503509",
};

// Notion-style нейтральная шкала (используется в редизайне страниц)
const crmNeutral = {
  0: "#FFFFFF",
  50: "#FAFAF9",
  100: "#F4F4F2",
  200: "#E7E7E3",
  300: "#D1D1CB",
  400: "#9B9B94",
  500: "#6E6E65",
  700: "#3F3F38",
  900: "#1A1A16",
};

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
          // Новые имена — использовать в редизайне страниц
          primary: brandPrimary,
          accent: brandAccent,
          // Старые имена — обратная совместимость с существующими шаблонами
          teal: "#01948E",
          orange: "#FDAD3A",
          dark: "#003D38",
          soft: "#C2E2DE",
        },
        // Notion-style нейтральная шкала (для редизайна)
        "crm-neutral": crmNeutral,
        // Семантические токены
        success: {
          50: "#E8F5E9",
          500: "#2E7D32",
          700: "#1B5E20",
        },
        warning: {
          50: "#FFF4E5",
          500: "#E65100",
          700: "#BF360C",
        },
        danger: {
          50: "#FDECEA",
          500: "#C62828",
          700: "#8B1A1A",
        },
        info: {
          50: "#E3F2FD",
          500: "#1565C0",
          700: "#0D47A1",
        },
      },
      // Утилиты для редизайна (не override defaults)
      boxShadow: {
        "crm-xs": "0 1px 2px rgba(15,21,20,0.04)",
        "crm-sm": "0 2px 4px rgba(15,21,20,0.06)",
        "crm": "0 4px 8px rgba(15,21,20,0.08)",
        "crm-md": "0 6px 16px rgba(15,21,20,0.10)",
        "crm-lg": "0 12px 32px rgba(15,21,20,0.12)",
        "crm-xl": "0 20px 48px rgba(15,21,20,0.16)",
      },
    },
  },
  plugins: [],
};
