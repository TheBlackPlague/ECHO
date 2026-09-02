import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {defaultApiBaseUrl, normalizeApiBaseUrl} from "./api-base";


describe("API base URL handling", () => {
    beforeEach(() => window.history.replaceState({}, "", "/echo/dashboard?tab=runs#active"));
    afterEach(() => vi.unstubAllEnvs());

    it("uses the current application directory for a blank value", () => {
        expect(normalizeApiBaseUrl("  ")).toBe("http://localhost:3000/echo");
    });

    it("resolves relative addresses and removes query, fragment, and trailing slash", () => {
        expect(normalizeApiBaseUrl(" /backend/?token=hidden#fragment ")).toBe("http://localhost:3000/backend");
        expect(normalizeApiBaseUrl("https://echo.example.test/root///")).toBe("https://echo.example.test/root//");
    });

    it("defaults to the page directory when no build-time override is configured", () => {
        expect(defaultApiBaseUrl()).toBe("http://localhost:3000/echo");
    });

    it("honors and normalizes a build-time API override", () => {
        vi.stubEnv("VITE_ECHO_API_BASE_URL", " https://api.example.test/echo/?ignored=yes ");
        expect(defaultApiBaseUrl()).toBe("https://api.example.test/echo");
    });
});
