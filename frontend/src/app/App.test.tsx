import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {act, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {beforeEach, describe, expect, it, vi} from "vitest";
import {useApp} from "./context";
import App from "./App";


const state = vi.hoisted(() => {
    class MockApiError extends Error {
        readonly status: number;
        readonly code: string;
        readonly requestId: string | null;
        readonly details: unknown;

        constructor(
            message: string, options: { status: number; code?: string; requestId?: string | null; details?: unknown }) {
            super(message);
            this.name = "ApiError";
            this.status = options.status;
            this.code = options.code ?? `http_${options.status}`;
            this.requestId = options.requestId ?? null;
            this.details = options.details;
        }
    }

    return {
        MockApiError,
        constructor: vi.fn(),
        unauthorized: undefined as (() => void) | undefined,
        api: {
            authSession: vi.fn(),
            login: vi.fn(),
            logout: vi.fn(),
            system: vi.fn(),
        },
    };
});

vi.mock("../api/echo-api", () => ({
    ApiError: state.MockApiError,
    EchoApi: class MockEchoApi {
        constructor(baseUrl: string, unauthorized: () => void) {
            state.constructor(baseUrl, unauthorized);
            state.unauthorized = unauthorized;
            return state.api;
        }
    },
}));
vi.mock("../features/connection/api-base", () => ({defaultApiBaseUrl: () => "https://echo.test"}));
vi.mock("../ui/Logo", () => ({Logo: () => <div>ECHO logo</div>}));
vi.mock("../features/dashboard/Dashboard", () => ({
    Dashboard: () => {
        const {logout, notify} = useApp();
        return <div>Dashboard ready
            <button onClick={() => notify("Manual notification", "success")}>Notify</button>
            <button onClick={logout}>Force logout</button>
        </div>;
    },
}));

function renderApp() {
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
    const result = render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
    return {...result, client};
}

beforeEach(() => {
    state.constructor.mockClear();
    state.unauthorized = undefined;
    state.api.authSession.mockReset();
    state.api.login.mockReset().mockResolvedValue({authenticated: true, login_enabled: true});
    state.api.logout.mockReset().mockResolvedValue(undefined);
    state.api.system.mockReset().mockResolvedValue({ready: true});
});

describe("App", () => {
    it("checks the existing session, removes legacy state, and renders authenticated content", async () => {
        sessionStorage.setItem("echo.connection.v1", "obsolete");
        let resolve!: (session: { authenticated: boolean; login_enabled: boolean }) => void;
        state.api.authSession.mockReturnValue(new Promise((done) => {
            resolve = done;
        }));
        renderApp();

        expect(screen.getByText(/checking your session/i)).toBeInTheDocument();
        expect(sessionStorage.getItem("echo.connection.v1")).toBeNull();
        await act(() => {
            resolve({authenticated: true, login_enabled: true});
        });
        expect(await screen.findByText("Dashboard ready")).toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Log out"})).toBeInTheDocument();
        expect(state.constructor).toHaveBeenCalledWith("https://echo.test", expect.any(Function));
    });

    it("shows login for an anonymous configured session and connects successfully", async () => {
        state.api.authSession.mockResolvedValue({authenticated: false, login_enabled: true});
        const {client} = renderApp();
        const clear = vi.spyOn(client, "clear");

        const password = await screen.findByLabelText("Password");
        fireEvent.change(password, {target: {value: "correct password"}});
        fireEvent.click(screen.getByRole("button", {name: "Log in"}));

        expect(await screen.findByText("Dashboard ready")).toBeInTheDocument();
        expect(state.api.login).toHaveBeenCalledWith("correct password");
        expect(state.api.system).toHaveBeenCalledOnce();
        expect(clear).toHaveBeenCalledOnce();
    });

    it("rejects a login response that did not establish a session", async () => {
        state.api.authSession.mockResolvedValue({authenticated: false, login_enabled: true});
        state.api.login.mockResolvedValue({authenticated: false, login_enabled: true});
        renderApp();
        fireEvent.change(await screen.findByLabelText("Password"), {target: {value: "password"}});
        fireEvent.click(screen.getByRole("button", {name: "Log in"}));
        expect(await screen.findByRole("alert")).toHaveTextContent("ECHO did not establish a session.");
    });

    it("explains an unconfigured server without offering login", async () => {
        state.api.authSession.mockResolvedValue({authenticated: false, login_enabled: false});
        renderApp();
        expect(await screen.findByRole("alert")).toHaveTextContent("Web login is not configured");
        expect(screen.getByRole("button", {name: "Log in"})).toBeDisabled();
    });

    it("moves an authenticated user back to login after an unauthorized response", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: true});
        renderApp();
        expect(await screen.findByText("Dashboard ready")).toBeInTheDocument();
        act(() => state.unauthorized?.());
        expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    });

    it("logs out a configured user", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: true});
        const {client} = renderApp();
        const clear = vi.spyOn(client, "clear");
        fireEvent.click(await screen.findByRole("button", {name: "Log out"}));

        await waitFor(() => expect(state.api.logout).toHaveBeenCalledOnce());
        expect(await screen.findByLabelText("Password")).toBeInTheDocument();
        expect(clear).toHaveBeenCalledOnce();
    });

    it("falls back to login when the session check fails", async () => {
        state.api.authSession.mockRejectedValue({network: "down"});
        renderApp();
        expect(await screen.findByLabelText("Password")).toBeInTheDocument();
        expect(screen.getByText("ECHO could not be reached.")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: "Dismiss notification"}));
        expect(screen.queryByText("ECHO could not be reached.")).not.toBeInTheDocument();
    });

    it("shows the session-check error message on the login screen", async () => {
        state.api.authSession.mockRejectedValue(new Error("Session service unavailable."));
        renderApp();
        expect(await screen.findByLabelText("Password")).toBeInTheDocument();
        expect(screen.getByText("Session service unavailable.")).toBeInTheDocument();
    });

    it("ignores a session response after unmounting and aborts its request", async () => {
        let resolve!: (session: { authenticated: boolean; login_enabled: boolean }) => void;
        state.api.authSession.mockReturnValue(new Promise((done) => {
            resolve = done;
        }));
        const {unmount} = renderApp();
        const signal = state.api.authSession.mock.calls[0]?.[0] as AbortSignal;
        unmount();
        expect(signal.aborted).toBe(true);
        await act(() => resolve({authenticated: true, login_enabled: true}));
    });

    it("ignores a session failure after unmounting", async () => {
        let reject!: (error: Error) => void;
        state.api.authSession.mockReturnValue(new Promise((_resolve, fail) => {
            reject = fail;
        }));
        const {unmount} = renderApp();
        unmount();
        await act(() => reject(new Error("late failure")));
    });

    it("reports logout errors as dismissible notifications", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: true});
        state.api.logout.mockRejectedValue({unexpected: true});
        renderApp();
        fireEvent.click(await screen.findByRole("button", {name: "Log out"}));
        expect(await screen.findByText("ECHO could not log out.")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: "Dismiss notification"}));
        expect(screen.queryByText("ECHO could not log out.")).not.toBeInTheDocument();
    });

    it("preserves logout error messages", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: true});
        state.api.logout.mockRejectedValue(new Error("Logout failed at the server."));
        renderApp();
        fireEvent.click(await screen.findByRole("button", {name: "Log out"}));
        expect(await screen.findByText("Logout failed at the server.")).toBeInTheDocument();
    });

    it("keeps passwordless deployments authenticated after logout", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: false});
        renderApp();
        fireEvent.click(await screen.findByRole("button", {name: "Force logout"}));
        await waitFor(() => expect(state.api.logout).toHaveBeenCalledOnce());
        expect(screen.getByText("Dashboard ready")).toBeInTheDocument();
    });

    it("allows dashboard notifications to be dismissed", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: false});
        renderApp();
        fireEvent.click(await screen.findByRole("button", {name: "Notify"}));
        expect(screen.getByText("Manual notification").closest(".toast")).toHaveClass("toast-success");
        fireEvent.click(screen.getByRole("button", {name: "Dismiss notification"}));
        expect(screen.queryByText("Manual notification")).not.toBeInTheDocument();
    });

    it("automatically expires dashboard notifications", async () => {
        state.api.authSession.mockResolvedValue({authenticated: true, login_enabled: false});
        renderApp();
        const notify = await screen.findByRole("button", {name: "Notify"});

        vi.useFakeTimers();
        try {
            fireEvent.click(notify);
            expect(screen.getByText("Manual notification")).toBeInTheDocument();
            act(() => vi.advanceTimersByTime(4_500));
            expect(screen.queryByText("Manual notification")).not.toBeInTheDocument();
        } finally {
            vi.useRealTimers();
        }
    });
});
