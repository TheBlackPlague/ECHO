import {
    cancelRun,
    getArchiveCapacity,
    getArchiveSize,
    getHealthReadiness,
    getPlan,
    getRcloneStatus,
    getRun,
    getSystemStatus,
    listArchive,
    listPlans,
    listRuns,
    submitArchiveRun,
    submitVerificationRun,
} from "./generated/sdk.gen";
import {type Client, createClient} from "./generated/client";
import type {ListArchiveData, ListRunsData} from "./generated/types.gen";


interface EchoErrorEnvelope {
    error?: {
        code?: string;
        message?: string;
        details?: unknown;
        request_id?: string | null;
    };
}

export interface AuthSession {
    authenticated: boolean;
    login_enabled: boolean;
}

export class ApiError extends Error {
    readonly status: number;
    readonly code: string;
    readonly requestId: string | null;
    readonly details: unknown;

    constructor(
        message: string,
        options: { status: number; code?: string; requestId?: string | null; details?: unknown },
    ) {
        super(message);
        this.name = "ApiError";
        this.status = options.status;
        this.code = options.code ?? `http_${options.status}`;
        this.requestId = options.requestId ?? null;
        this.details = options.details;
    }
}

export class EchoApi {
    readonly baseUrl: string;
    private readonly client: Client;
    private readonly onUnauthorized?: () => void;

    constructor(baseUrl: string, onUnauthorized?: () => void) {
        this.baseUrl = baseUrl.replace(/\/$/, "");
        this.onUnauthorized = onUnauthorized;
        this.client = createClient({
            baseUrl: this.baseUrl,
            credentials: "include",
            headers: {Accept: "application/json"},
            throwOnError: true,
        });
        this.client.interceptors.error.use((error, response) => {
            if (response?.status === 401) onUnauthorized?.();
            return normalizeError(error, response);
        });
    }

    authSession(signal?: AbortSignal): Promise<AuthSession> {
        return this.authRequest("/api/auth/session", {signal});
    }

    login(password: string): Promise<AuthSession> {
        return this.authRequest("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({password}),
            headers: {"Content-Type": "application/json"},
        });
    }

    async logout(): Promise<void> {
        await this.authRequest("/api/auth/logout", {method: "POST"});
    }

    health(signal?: AbortSignal) {
        return data(getHealthReadiness({client: this.client, signal, throwOnError: true}));
    }

    system(signal?: AbortSignal) {
        return data(getSystemStatus({client: this.client, signal, throwOnError: true}));
    }

    rclone(signal?: AbortSignal) {
        return data(getRcloneStatus({client: this.client, signal, throwOnError: true}));
    }

    plans(signal?: AbortSignal) {
        return data(listPlans({client: this.client, signal, throwOnError: true}));
    }

    plan(name: string, signal?: AbortSignal) {
        return data(getPlan({
            client: this.client,
            path: {name},
            signal,
            throwOnError: true,
        }));
    }

    submitArchive(name: string, dryRun = false) {
        return data(submitArchiveRun({
            body: {dry_run: dryRun},
            client: this.client,
            path: {name},
            throwOnError: true,
        }));
    }

    submitVerification(name: string) {
        return data(submitVerificationRun({
            client: this.client,
            path: {name},
            throwOnError: true,
        }));
    }

    runs(query: ListRunsData["query"] = {}, signal?: AbortSignal) {
        return data(listRuns({client: this.client, query, signal, throwOnError: true}));
    }

    run(runId: string, signal?: AbortSignal) {
        return data(getRun({
            client: this.client,
            path: {run_id: runId},
            signal,
            throwOnError: true,
        }));
    }

    cancelRun(runId: string) {
        return data(cancelRun({
            client: this.client,
            path: {run_id: runId},
            throwOnError: true,
        }));
    }

    archive(query: ListArchiveData["query"] = {}, signal?: AbortSignal) {
        return data(listArchive({client: this.client, query, signal, throwOnError: true}));
    }

    archiveSize(signal?: AbortSignal) {
        return data(getArchiveSize({client: this.client, signal, throwOnError: true}));
    }

    capacity(signal?: AbortSignal) {
        return data(getArchiveCapacity({client: this.client, signal, throwOnError: true}));
    }

    private async authRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
        const headers = new Headers(init.headers);
        if (!headers.has("Accept")) headers.set("Accept", "application/json");

        let response: Response;
        try {
            response = await fetch(`${this.baseUrl}${path}`, {
                ...init,
                credentials: "include",
                headers,
            });
        } catch (error) {
            throw normalizeError(error);
        }

        const payload = response.status === 204
            ? undefined
            : await response.json().catch(() => undefined) as unknown;
        if (!response.ok) {
            if (response.status === 401) this.onUnauthorized?.();
            throw normalizeError(payload, response);
        }
        return payload as T;
    }
}

async function data<T>(request: Promise<{ data: T }>): Promise<T> {
    return (await request).data;
}

function normalizeError(error: unknown, response?: Response): ApiError {
    if (error instanceof ApiError) return error;

    const payload = isErrorEnvelope(error) ? error.error : undefined;
    const networkError = response === undefined;
    const message = payload?.message
        ?? (networkError ? "ECHO could not be reached. Check the server address and network." : response.statusText)
        ?? "Request failed";

    return new ApiError(message, {
        status: response?.status ?? 0,
        code: payload?.code ?? (networkError ? "network_error" : undefined),
        requestId: payload?.request_id,
        details: payload?.details,
    });
}

function isErrorEnvelope(value: unknown): value is EchoErrorEnvelope {
    return typeof value === "object" && value !== null && "error" in value;
}
