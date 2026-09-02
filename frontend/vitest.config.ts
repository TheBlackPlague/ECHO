import react from "@vitejs/plugin-react";
import {defineConfig} from "vitest/config";
import {fileURLToPath} from "node:url";


export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "../assets/echo-icon.svg": fileURLToPath(new URL("./src/test/echo-icon-stub.ts", import.meta.url)),
        },
    },
    test: {
        environment: "jsdom",
        setupFiles: ["./src/test/setup.ts"],
        clearMocks: true,
        restoreMocks: true,
        coverage: {
            provider: "v8",
            reporter: ["text", "json-summary"],
            include: ["src/**/*.{ts,tsx}"],
            exclude: [
                "src/api/generated/**",
                "src/main.tsx",
                "src/test/**",
                "src/vite-env.d.ts",
            ],
            thresholds: {
                statements: 99,
                branches: 92,
                functions: 100,
                lines: 99,
            },
        },
    },
});
