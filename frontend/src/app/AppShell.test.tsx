import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {fireEvent, render, screen} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";
import type {EchoApi} from "../api/echo-api";
import {AppContext, type AppContextValue} from "./context";
import {AppShell} from "./AppShell";


vi.mock("../ui/Logo", () => ({Logo: () => <div>ECHO logo</div>}));

function renderShell(canLogout: boolean, cached = true) {
    const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
    if (cached) client.setQueryData(["system"], {version: "0.2.0"});
    const system = vi.fn().mockResolvedValue({version: "0.2.0"});
    const value: AppContextValue = {
        api: {system} as unknown as EchoApi,
        connection: {baseUrl: "/echo"},
        canLogout,
        logout: vi.fn(),
        notify: vi.fn(),
    };
    render(<QueryClientProvider client={client}><AppContext.Provider value={value}>
        <AppShell><p>Dashboard content</p></AppShell>
    </AppContext.Provider></QueryClientProvider>);
    return {client, system, value};
}

describe("AppShell", () => {
    it("renders content, cached version, and logout when permitted", () => {
        const {value} = renderShell(true);
        expect(screen.getByText("Dashboard content")).toBeInTheDocument();
        expect(screen.getByText("ECHO v0.2.0")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: "Log out"}));
        expect(value.logout).toHaveBeenCalledOnce();
    });

    it("hides logout when web login is disabled", () => {
        renderShell(false);
        expect(screen.queryByRole("button", {name: "Log out"})).not.toBeInTheDocument();
    });

    it("registers a system query that forwards its abort signal", async () => {
        const {client, system} = renderShell(false, false);
        await client.fetchQuery({queryKey: ["system"]});
        expect(system).toHaveBeenCalledWith(expect.any(AbortSignal));
    });
});
