import type {ReactNode} from "react";
import {AlertTriangle, Inbox, RefreshCw} from "lucide-react";
import {ApiError} from "../api/echo-api";


export function LoadingState({rows = 3}: { rows?: number }) {
    return <div className="skeleton-stack" aria-label="Loading">{Array.from({length: rows}, (_, index) => <div
        className="skeleton-row" key={index}/>)}</div>;
}

export function ErrorState({error, retry}: { error: unknown; retry?: () => void }) {
    const message = error instanceof Error ? error.message : "Something went wrong while loading this view.";
    const requestId = error instanceof ApiError ? error.requestId : null;
    return (
        <div className="state-message state-error" role="alert">
            <AlertTriangle size={22}/>
            <div><strong>Couldn’t load this data</strong><p>{message}</p>{requestId &&
                <small>Request {requestId}</small>}</div>
            {retry && <button className="button button-secondary button-small" type="button" onClick={retry}><RefreshCw
                size={14}/> Retry</button>}
        </div>
    );
}

export function EmptyState({title, children, action}: { title: string; children: ReactNode; action?: ReactNode }) {
    return <div className="empty-state">
        <div className="empty-icon"><Inbox size={22}/></div>
        <strong>{title}</strong><p>{children}</p>{action}</div>;
}
