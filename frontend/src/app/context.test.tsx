import {render, screen} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";
import type {EchoApi} from "../api/echo-api";
import {AppContext, type AppContextValue, useApp} from "./context";


function Consumer() {
    const {connection, canLogout} = useApp();
    return <span>{connection.baseUrl}:{String(canLogout)}</span>;
}

describe("AppContext", () => {
    it("returns the provided application dependencies", () => {
        const value: AppContextValue = {
            api: {} as EchoApi,
            connection: {baseUrl: "/echo"},
            canLogout: true,
            logout: vi.fn(),
            notify: vi.fn(),
        };
        render(<AppContext.Provider value={value}><Consumer/></AppContext.Provider>);
        expect(screen.getByText("/echo:true")).toBeInTheDocument();
    });

    it("rejects use outside the provider", () => {
        const mute = vi.spyOn(console, "error").mockImplementation(() => undefined);
        expect(() => render(<Consumer/>)).toThrow("useApp must be used inside AppContext.Provider");
        mute.mockRestore();
    });
});
