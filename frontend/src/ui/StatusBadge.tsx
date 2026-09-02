import type {RunState} from "../api/generated/types.gen";
import {titleCase} from "../features/dashboard/format";


type Tone = "success" | "danger" | "warning" | "info" | "muted";

const runTones: Record<RunState, Tone> = {
    queued: "info",
    running: "info",
    succeeded: "success",
    failed: "danger",
    cancelled: "muted",
    interrupted: "warning",
};

export function StatusBadge({label, tone = "muted", pulse = false}: { label: string; tone?: Tone; pulse?: boolean }) {
    return <span className={`status-badge status-${tone}`}><span
        className={`status-dot${pulse ? " status-dot-pulse" : ""}`}/>{label}</span>;
}

export function RunStateBadge({state}: { state: RunState }) {
    return <StatusBadge label={titleCase(state)} tone={runTones[state]} pulse={state === "running"}/>;
}
