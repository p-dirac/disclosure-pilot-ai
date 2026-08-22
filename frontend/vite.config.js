import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react({ jsxRuntime: "automatic" })],
  resolve: {
    // Defensive guard against a repeat of the "two React copies" bug
    // (npm installing a separate react/react-dom tree because some
    // dependency's peer range didn't match the top-level pinned version,
    // producing a null hook dispatcher at runtime). Deduping here won't
    // fix an actual version mismatch in package.json - that still needs
    // fixing directly - but it stops Vite from bundling two resolvable
    // copies of the same package if npm's own resolution ever splits
    // them again after a future dependency bump.
    dedupe: ["react", "react-dom"],
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
  test: {
    // jsdom gives tests a browser-like DOM environment
    environment: "jsdom",

    // Must match the actual file location in your project
    setupFiles: ["./src/tests/setup.js"],

    // Make describe/it/expect/vi available globally without explicit imports
    globals: true,

    // Find all test files in src/ — both .test.jsx and the _test.jsx naming convention
    include: [
      "src/**/*.test.{js,jsx,ts,tsx}",
      "src/**/*_test.{js,jsx,ts,tsx}",
    ],

    // Suppress noisy act() warnings from React Testing Library
    // (can be removed once @testing-library/react ≥ 14 is installed)
    onConsoleLog(log) {
      if (log.includes("act(...)")) return false;
    },
  },
})
