import type {ReactNode} from "react";
import {useQuery} from "@tanstack/react-query";
import {LogOut} from "lucide-react";
import {Logo} from "../ui/Logo";
import {useApp} from "./context";


export function AppShell({children}: { children: ReactNode }) {
    const {api, canLogout, logout} = useApp();
    const system = useQuery({
        queryKey: ["system"],
        queryFn: ({signal}) => api.system(signal),
        enabled: false,
    });

    return (
        <div className="app-frame">
            <header className="site-header">
                <Logo/>
                <div className="header-actions">
                    {canLogout && (
                        <button className="icon-button" type="button" aria-label="Log out" onClick={logout}>
                            <LogOut size={17}/>
                        </button>
                    )}
                </div>
            </header>
            <main className="page-content">{children}</main>
            <footer className="site-footer">ECHO {system.data?.version ? `v${system.data.version}` : ""}</footer>
        </div>
    );
}
