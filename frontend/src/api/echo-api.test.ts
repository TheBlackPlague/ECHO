import {beforeEach, describe, expect, it, vi} from "vitest";
import {ApiError, EchoApi} from "./echo-api";


const generated = vi.hoisted(() => ({
    cancelRun: vi.fn(),
    getArchiveCapacity: vi.fn(),
    getArchiveSize: vi.fn(),
    getHealthReadiness: vi.fn(),
    getPlan: vi.fn(),
    getRcloneStatus: vi.fn(),
    getRun: vi.fn(),
    getSystemStatus: vi.fn(),
    listArchive: vi.fn(),
    listPlans: vi.fn(),
    listRuns: vi.fn(),
    submitArchiveRun: vi.fn(),
    submitVerificationRun: vi.fn(),
}));

const clientMock = vi.hoisted(() => {
    const errorUse = vi.fn();
    return {
        createClient: vi.fn(() => ({interceptors: {error: {use: errorUse}}})),
        errorUse,
    };
});

vi.mock("./generated/sdk.gen", () => generated);
vi.mock("./generated/client", () => ({createClient: clientMock.createClient}));


describe("EchoApi", () => {
    beforeEach(() => {
        vi.stubGlobal("fetch", vi.fn());
        for (const mock of Object.values(generated)) mock.mockReset();
        clientMock.createClient.mockClear();
        clientMock.errorUse.mockClear();
    });

    it("normalizes the base URL and configures the generated client", () => {
        const unauthorized = vi.fn();
        const api = new EchoApi("https://echo.example.test/root/", unauthorized);

        expect(api.baseUrl).toBe("https://echo.example.test/root");
        expect(clientMock.createClient).toHaveBeenCalledWith({
            baseUrl: "https://echo.example.test/root",
            credentials: "include",
            headers: {Accept: "application/json"},
            throwOnError: true,
        });

        const interceptor = clientMock.errorUse.mock.calls[0]?.[0] as (error: unknown, response?: Response) => ApiError;
        const result = interceptor(
            {error: {code: "denied", message: "No access", request_id: "req-1"}},
            new Response(null, {status: 401, statusText: "Unauthorized"})
        );
        expect(unauthorized).toHaveBeenCalledOnce();
        expect(result).toMatchObject({status: 401, code: "denied", message: "No access", requestId: "req-1"});
        expect(interceptor(result)).toBe(result);
    });

    it("delegates generated endpoints with paths, queries, signals, and unwrapped data", async () => {
        const api = new EchoApi("/echo");
        const signal = new AbortController().signal;
        for (const mock of Object.values(generated)) mock.mockResolvedValue({data: {marker: mock.getMockName()}});

        await api.health(signal);
        await api.system(signal);
        await api.rclone(signal);
        await api.plans(signal);
        await api.plan("photos", signal);
        await api.submitArchive("photos", true);
        await api.submitVerification("photos");
        await api.runs({limit: 8}, signal);
        await api.run("run-1", signal);
        await api.cancelRun("run-1");
        await api.archive({path: "Cinema", recursive: true}, signal);
        await api.archiveSize(signal);
        await api.capacity(signal);

        expect(generated.getHealthReadiness)
            .toHaveBeenCalledWith(expect.objectContaining({signal, throwOnError: true}));
        expect(generated.getSystemStatus).toHaveBeenCalledWith(expect.objectContaining({signal}));
        expect(generated.getRcloneStatus).toHaveBeenCalledWith(expect.objectContaining({signal}));
        expect(generated.listPlans).toHaveBeenCalledWith(expect.objectContaining({signal}));
        expect(generated.getPlan).toHaveBeenCalledWith(expect.objectContaining({path: {name: "photos"}, signal}));
        expect(generated.submitArchiveRun).toHaveBeenCalledWith(expect.objectContaining({
            path: {name: "photos"}, body: {dry_run: true},
        }));
        expect(generated.submitVerificationRun).toHaveBeenCalledWith(expect.objectContaining({path: {name: "photos"}}));
        expect(generated.listRuns).toHaveBeenCalledWith(expect.objectContaining({query: {limit: 8}, signal}));
        expect(generated.getRun).toHaveBeenCalledWith(expect.objectContaining({path: {run_id: "run-1"}, signal}));
        expect(generated.cancelRun).toHaveBeenCalledWith(expect.objectContaining({path: {run_id: "run-1"}}));
        expect(generated.listArchive).toHaveBeenCalledWith(expect.objectContaining({
            query: {path: "Cinema", recursive: true}, signal,
        }));
        expect(generated.getArchiveSize).toHaveBeenCalledWith(expect.objectContaining({signal}));
        expect(generated.getArchiveCapacity).toHaveBeenCalledWith(expect.objectContaining({signal}));
    });

    it("uses empty default queries and defaults archive submissions to a real run", async () => {
        const api = new EchoApi("/echo");
        generated.submitArchiveRun.mockResolvedValue({data: {id: "run-1"}});
        generated.listRuns.mockResolvedValue({data: {items: []}});
        generated.listArchive.mockResolvedValue({data: {items: []}});

        await api.submitArchive("photos");
        await api.runs();
        await api.archive();

        expect(generated.submitArchiveRun).toHaveBeenCalledWith(expect.objectContaining({body: {dry_run: false}}));
        expect(generated.listRuns).toHaveBeenCalledWith(expect.objectContaining({query: {}}));
        expect(generated.listArchive).toHaveBeenCalledWith(expect.objectContaining({query: {}}));
    });

    it("performs auth requests with cookies and appropriate headers", async () => {
        const fetchMock = vi.mocked(fetch);
        fetchMock
            .mockResolvedValueOnce(new Response(JSON.stringify({authenticated: false, login_enabled: true}), {
                status: 200, headers: {"Content-Type": "application/json"},
            }))
            .mockResolvedValueOnce(new Response(JSON.stringify({authenticated: true, login_enabled: true}), {
                status: 200, headers: {"Content-Type": "application/json"},
            }))
            .mockResolvedValueOnce(new Response(null, {status: 204}));
        const api = new EchoApi("https://echo.test");
        const signal = new AbortController().signal;

        await expect(api.authSession(signal)).resolves.toEqual({authenticated: false, login_enabled: true});
        await expect(api.login("correct horse")).resolves.toEqual({authenticated: true, login_enabled: true});
        await expect(api.logout()).resolves.toBeUndefined();

        expect(fetchMock).toHaveBeenNthCalledWith(1, "https://echo.test/api/auth/session", expect.objectContaining({
            credentials: "include", signal,
        }));
        const loginInit = fetchMock.mock.calls[1]?.[1];
        expect(loginInit)
            .toMatchObject({method: "POST", credentials: "include", body: JSON.stringify({password: "correct horse"})});
        expect(new Headers(loginInit?.headers).get("Accept")).toBe("application/json");
        expect(new Headers(loginInit?.headers).get("Content-Type")).toBe("application/json");
        expect(fetchMock)
            .toHaveBeenNthCalledWith(3, "https://echo.test/api/auth/logout", expect.objectContaining({method: "POST"}));
    });

    it("normalizes structured, plain HTTP, and network failures", async () => {
        const unauthorized = vi.fn();
        const fetchMock = vi.mocked(fetch);
        fetchMock
            .mockResolvedValueOnce(new Response(JSON.stringify({
                error: {code: "bad_password", message: "Wrong password", details: {tries: 2}, request_id: "req-2"},
            }), {status: 401, statusText: "Unauthorized", headers: {"Content-Type": "application/json"}}))
            .mockResolvedValueOnce(new Response("not json", {status: 503, statusText: "Unavailable"}))
            .mockRejectedValueOnce(new TypeError("fetch failed"));
        const api = new EchoApi("/echo", unauthorized);

        await expect(api.login("wrong")).rejects.toMatchObject({
            name: "ApiError", status: 401, code: "bad_password", message: "Wrong password",
            requestId: "req-2", details: {tries: 2},
        });
        expect(unauthorized).toHaveBeenCalledOnce();
        await expect(api.authSession()).rejects.toMatchObject({status: 503, code: "http_503", message: "Unavailable"});
        await expect(api.authSession()).rejects.toMatchObject({
            status: 0,
            code: "network_error",
            message: "ECHO could not be reached. Check the server address and network.",
        });
    });
});
