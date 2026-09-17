import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  // Сторож возможностей бота (src/lib/botCapabilities.guard.test.ts) сверяет манифест
  // кабинета со списком в коде бота и с адаптером — это файлы вне корня кабинета, и
  // без разрешения Vite отказывает в их чтении («Denied ID»). Только для тестов:
  // сборка кабинета (vite.config.ts) их не читает.
  server: {
    fs: { allow: [fileURLToPath(new URL("..", import.meta.url))] },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
});
