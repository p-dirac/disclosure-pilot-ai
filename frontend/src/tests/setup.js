/**
 * src/tests/setup.js
 *
 * Vitest global setup file.
 * Path must match vite.config.js → test.setupFiles: ["./src/tests/setup.js"]
 *
 * Extends Vitest's expect with @testing-library/jest-dom matchers such as:
 *   toBeInTheDocument(), toHaveValue(), toBeVisible(), toHaveTextContent(), etc.
 */
import "@testing-library/jest-dom";
