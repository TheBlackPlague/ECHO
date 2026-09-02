import {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useQueryClient} from "@tanstack/react-query";
import {EchoApi} from "../api/echo-api";
import {ConnectScreen} from "../features/connection/ConnectScreen";
import {Dashboard} from "../features/dashboard/Dashboard";
import {type ToastItem, ToastRegion} from "../ui/ToastRegion";
import {AppShell} from "./AppShell";
import {AppContext, type Connection, type ToastTone} from "./context";
import {defaultApiBaseUrl} from "../features/connection/api-base";
import {Logo} from "../ui/Logo";


const LEGACY_SESSION_KEY = "echo.connection.v1";
type AuthState = "checking" | "authenticated" | "login" | "unconfigured";

export default function App() {
    const queryClient = useQueryClient();
    const [authState, setAuthState] = useState<AuthState>("checking");
    const [loginEnabled, setLoginEnabled] = useState(false);
    const [toasts, setToasts] = useState<ToastItem[]>([]);
    const nextToastId = useRef(1);
    const connection = useMemo<Connection>(() => ({baseUrl: defaultApiBaseUrl()}), []);

    const unauthorized = useCallback(() => {
        queryClient.clear();
        setAuthState("login");
    }, [queryClient]);

    const api = useMemo(
        () => new EchoApi(connection.baseUrl, unauthorized),
        [connection.baseUrl, unauthorized],
    );

    const notify = useCallback((message: string, tone: ToastTone = "info") => {
        const id = nextToastId.current++;
        setToasts((items) => [...items, {id, message, tone}]);
        window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 4_500);
    }, []);

    const dismissToast = useCallback((id: number) => {
        setToasts((items) => items.filter((item) => item.id !== id));
    }, []);

    useEffect(() => {
        sessionStorage.removeItem(LEGACY_SESSION_KEY);
        const controller = new AbortController();
        void api.authSession(controller.signal).then((session) => {
            setLoginEnabled(session.login_enabled);
            setAuthState(session.authenticated
                ? "authenticated"
                : session.login_enabled ? "login" : "unconfigured");
        }).catch((error: unknown) => {
            if (controller.signal.aborted) return;
            setAuthState("login");
            notify(error instanceof Error ? error.message : "ECHO could not be reached.", "danger");
        });
        return () => controller.abort();
    }, [api, notify]);

    async function connect(password: string) {
        const session = await api.login(password);
        if (!session.authenticated) throw new Error("ECHO did not establish a session.");
        await api.system();
        setLoginEnabled(session.login_enabled);
        queryClient.clear();
        setAuthState("authenticated");
    }

    const logout = useCallback(() => {
        void api.logout().then(() => {
            queryClient.clear();
            setAuthState(loginEnabled ? "login" : "authenticated");
        }).catch((error: unknown) => {
            notify(error instanceof Error ? error.message : "ECHO could not log out.", "danger");
        });
    }, [api, loginEnabled, notify, queryClient]);

    if (authState === "checking") {
        return (
            <main className="connect-page">
                <section className="connect-card">
                    <Logo/>
                    <p className="session-note"><span className="spinner"/> Checking your session…</p>
                </section>
            </main>
        );
    }

    if (authState !== "authenticated") {
        return (
            <>
                <ConnectScreen loginEnabled={authState !== "unconfigured"} onConnect={connect}/>
                <ToastRegion toasts={toasts} dismiss={dismissToast}/>
            </>
        );
    }

    return (
        <AppContext.Provider value={{api, connection, canLogout: loginEnabled, logout, notify}}>
            <AppShell><Dashboard/></AppShell>
            <ToastRegion toasts={toasts} dismiss={dismissToast}/>
        </AppContext.Provider>
    );
}
