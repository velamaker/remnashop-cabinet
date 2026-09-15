import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// В деве проксируем /api на бэкенд бота, чтобы не возиться с CORS локально.
// В проде /api проксируется на том же домене через nginx/Caddy (см. nginx.conf и docker-compose).
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": "/src",
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: "http://localhost:5000",
                changeOrigin: true,
                rewrite: function (path) { return path.replace(/^\/api/, ""); },
            },
        },
    },
    build: {
        outDir: "dist",
        sourcemap: false,
        rollupOptions: {
            // Сколько файлов сборщик обрабатывает одновременно. По умолчанию 20 — на
            // сервере с одним-двумя ядрами это даёт пик нагрузки, при котором машина
            // перестаёт отвечать во время установки. Четыре потока собирают заметно
            // спокойнее, а по времени на таких машинах разница невелика: там всё
            // равно упирается в диск и память, а не в параллелизм.
            maxParallelFileOps: 4,
        },
    },
});
