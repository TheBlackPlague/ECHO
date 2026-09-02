export function normalizeApiBaseUrl(value: string): string {
    const trimmed = value.trim();
    if (!trimmed) return new URL(".", window.location.href).toString().replace(/\/$/, "");

    const url = new URL(trimmed, window.location.origin);
    url.hash = "";
    url.search = "";
    return url.toString().replace(/\/$/, "");
}

export function defaultApiBaseUrl(): string {
    const configured = import.meta.env.VITE_ECHO_API_BASE_URL?.trim();
    return normalizeApiBaseUrl(configured || new URL(".", window.location.href).toString());
}
