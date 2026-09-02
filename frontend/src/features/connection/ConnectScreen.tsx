import {type FormEvent, useState} from "react";
import {Eye, EyeOff, LockKeyhole, ShieldCheck} from "lucide-react";
import {ApiError} from "../../api/echo-api";
import {Logo} from "../../ui/Logo";


type ConnectScreenProps = {
    loginEnabled: boolean;
    onConnect: (password: string) => Promise<void>;
};

export function ConnectScreen({loginEnabled, onConnect}: ConnectScreenProps) {
    const [password, setPassword] = useState("");
    const [visible, setVisible] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [connecting, setConnecting] = useState(false);

    async function submit(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        setConnecting(true);
        setError(null);
        try {
            await onConnect(password);
        } catch (caught) {
            setError(caught instanceof ApiError && caught.status === 401
                ? "That password wasn’t accepted. Check it and try again."
                : caught instanceof Error ? caught.message : "ECHO could not be reached.");
        } finally {
            setConnecting(false);
        }
    }

    return (
        <main className="connect-page">
            <section className="connect-card">
                <Logo/>
                <form className="connect-form" onSubmit={submit}>
                    <div className="field-label-row">
                        <label className="field-label" htmlFor="password">Password</label>
                    </div>
                    <div className="input-shell">
                        <LockKeyhole size={17}/>
                        <input id="password" type={visible ? "text" : "password"} value={password}
                               onChange={(event) => setPassword(event.target.value)} placeholder="ECHO password"
                               autoComplete="current-password" autoFocus disabled={!loginEnabled}/>
                        <button type="button" className="input-action" onClick={() => setVisible((value) => !value)}
                                aria-label={visible ? "Hide password" : "Show password"}
                                disabled={!loginEnabled}>
                            {visible ? <EyeOff size={17}/> : <Eye size={17}/>}
                        </button>
                    </div>
                    {!loginEnabled && (
                        <div className="form-error" role="alert">
                            Web login is not configured. Set ECHO_API__WEB_PASSWORD on the server.
                        </div>
                    )}
                    {error && <div className="form-error" role="alert">{error}</div>}
                    <button className="button button-primary connect-button" type="submit"
                            disabled={connecting || !loginEnabled || !password}>
                        {connecting ? <><span className="spinner"/> Signing in…</> : "Log in"}
                    </button>
                </form>
                <p className="session-note"><ShieldCheck size={13}/>
                    Your password is exchanged for a secure session and is never stored.
                </p>
            </section>
        </main>
    );
}
