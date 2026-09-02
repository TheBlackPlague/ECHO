export function formatDate(value: string | null | undefined, fallback = "Never"): string {
    if (!value) return fallback;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return fallback;

    return new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    }).format(date);
}

export function formatRelativeTime(value: string | null | undefined): string {
    if (!value) return "Never";
    const difference = new Date(value).getTime() - Date.now();
    if (!Number.isFinite(difference)) return "Unknown";

    const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
        ["year", 365 * 24 * 60 * 60 * 1000],
        ["month", 30 * 24 * 60 * 60 * 1000],
        ["day", 24 * 60 * 60 * 1000],
        ["hour", 60 * 60 * 1000],
        ["minute", 60 * 1000],
    ];
    const formatter = new Intl.RelativeTimeFormat(undefined, {numeric: "auto"});

    for (const [unit, milliseconds] of units) {
        if (Math.abs(difference) >= milliseconds || unit === "minute") {
            return formatter.format(Math.round(difference / milliseconds), unit);
        }
    }
    return "Just now";
}

export function formatDuration(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const remaining = Math.round(seconds % 60);
    if (minutes < 60) return `${minutes}m ${remaining}s`;
    const hours = Math.floor(minutes / 60);
    return `${hours}h ${minutes % 60}m`;
}

export function titleCase(value: string): string {
    return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}
