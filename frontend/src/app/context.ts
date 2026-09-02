import {createContext, useContext} from "react";
import type {EchoApi} from "../api/echo-api";


export type ToastTone = "success" | "danger" | "info";

export interface Connection {
    baseUrl: string;
}

export interface AppContextValue {
    api: EchoApi;
    connection: Connection;
    canLogout: boolean;
    logout: () => void;
    notify: (message: string, tone?: ToastTone) => void;
}

export const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
    const value = useContext(AppContext);
    if (!value) throw new Error("useApp must be used inside AppContext.Provider");
    return value;
}
