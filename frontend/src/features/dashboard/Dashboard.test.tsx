import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {afterEach, describe, expect, it, vi} from "vitest";
import type {EchoApi} from "../../api/echo-api";
import type {ArchivePlanResponse, RunSummaryResponse, SystemStatusResponse} from "../../api/generated/types.gen";
import {AppContext, type AppContextValue} from "../../app/context";
import {Dashboard} from "./Dashboard";


const system: SystemStatusResponse = {
    version: "0.2.0",
    started: true,
    ready: true,
    archive_enabled: true,
    archiver_running: true,
    scheduler_running: true,
    configured_plans: 2,
    enabled_plans: 1,
    scheduled_plans: 1,
    active_runs: 1,
    run_counts: {},
};

const completedRun: RunSummaryResponse = {
    id: "run-complete",
    plan_name: "Cinema",
    operation: "archive",
    trigger: "scheduled",
    state: "succeeded",
    dry_run: false,
    source: "/cinema",
    destination: "s3:cinema",
    created_at: "2026-01-14T12:00:00Z",
    started_at: "2026-01-14T12:01:00Z",
    finished_at: "2026-01-14T12:03:00Z",
    duration_seconds: 120,
    files_added: 3,
    files_verified: 7,
};

const activeRun: RunSummaryResponse = {
    ...completedRun,
    id: "run-active",
    state: "running",
    trigger: "manual",
    progress: 42.4,
    files_added: 2,
    files_verified: 5,
    finished_at: null,
};

const plans: ArchivePlanResponse[] = [
    {
        name: "Cinema",
        source: "/cinema",
        destination: "s3:cinema",
        cron: "0 3 * * *",
        exclude: [],
        enabled: true,
        verify_after_archive: true,
        scheduled: true,
        active_run_id: "run-active",
        latest_run: activeRun,
    },
    {
        name: "Documents",
        source: "/documents",
        destination: "s3:documents",
        cron: null,
        exclude: [],
        enabled: false,
        verify_after_archive: false,
        scheduled: false,
        active_run_id: null,
        latest_run: null,
    },
];

const activeClients: QueryClient[] = [];

function createApi(overrides: Partial<EchoApi> = {}): EchoApi {
    return {
        system: vi.fn().mockResolvedValue(system),
        plans: vi.fn().mockResolvedValue(plans),
        runs: vi.fn().mockImplementation((query: { operation?: string; plan_name?: string }) => Promise.resolve({
            items: query.operation === "archive"
                ? query.plan_name === "Cinema" ? [completedRun] : []
                : [activeRun, completedRun],
            limit: query.operation === "archive" ? 100 : 8,
            offset: 0,
            has_more: false,
        })),
        submitArchive: vi.fn().mockResolvedValue({...activeRun, id: "new-run"}),
        ...overrides,
    } as unknown as EchoApi;
}

function renderDashboard(api = createApi()) {
    const client = new QueryClient({defaultOptions: {queries: {retry: false, gcTime: Infinity}}});
    activeClients.push(client);
    const value: AppContextValue = {
        api,
        connection: {baseUrl: "/echo"},
        canLogout: true,
        logout: vi.fn(),
        notify: vi.fn(),
    };
    const result = render(<QueryClientProvider client={client}><AppContext.Provider value={value}>
        <Dashboard/>
    </AppContext.Provider></QueryClientProvider>);
    return {...result, api, value, client};
}

afterEach(() => {
    for (const client of activeClients.splice(0)) client.clear();
});

describe("Dashboard", () => {
    it("shows loading while core status is unresolved", () => {
        renderDashboard(createApi({system: vi.fn(() => new Promise<SystemStatusResponse>(() => undefined))}));
        expect(screen.getByLabelText("Loading")).toBeInTheDocument();
    });

    it("renders operational plans, progress, archive history, and run history", async () => {
        const {api} = renderDashboard();
        expect(await screen.findByRole("heading", {name: "ECHO is operational"})).toBeInTheDocument();
        expect(screen.getByText("Archive plans").nextElementSibling).toHaveTextContent("2");
        expect(screen.getByText("Active now").nextElementSibling).toHaveTextContent("1");

        const cinema = screen.getByRole("heading", {name: "Cinema"}).closest("article")!;
        expect(within(cinema).getByText("Archive in progress")).toBeInTheDocument();
        expect(within(cinema).getByText("42% complete")).toBeInTheDocument();
        expect(within(cinema).getByText("2 added · 5 verified")).toBeInTheDocument();
        expect(within(cinema).getByRole("progressbar")).toHaveAttribute("value", "42.4");
        expect(within(cinema).getByRole("button", {name: /running/i})).toBeDisabled();
        expect(within(cinema).getByText(/daily at/i)).toBeInTheDocument();

        const documents = screen.getByRole("heading", {name: "Documents"}).closest("article")!;
        expect(within(documents).getByText("Disabled")).toBeInTheDocument();
        expect(within(documents).getByText("Manual only")).toBeInTheDocument();
        expect(within(documents).getByText("Never")).toBeInTheDocument();
        expect(within(documents).getByRole("button", {name: "Archive now"})).toBeDisabled();

        expect(screen.getByRole("cell", {name: /manual/i})).toHaveTextContent("Manual");
        expect(screen.getAllByText("2 added").length).toBeGreaterThan(0);
        fireEvent.click(screen.getByRole("button", {name: "Refresh run history"}));
        await waitFor(() => expect(vi.mocked(api.runs).mock.calls.length).toBeGreaterThanOrEqual(4));
    });

    it("queues an enabled plan after confirmation and notifies the user", async () => {
        const idlePlan = {...plans[0]!, active_run_id: null, latest_run: completedRun};
        const api = createApi({plans: vi.fn().mockResolvedValue([idlePlan])});
        const {value} = renderDashboard(api);

        fireEvent.click(await screen.findByRole("button", {name: "Archive now"}));
        expect(screen.getByText("Archive Cinema now?")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: "Start archive", hidden: true}));

        await waitFor(() => expect(api.submitArchive).toHaveBeenCalledWith("Cinema"));
        expect(value.notify).toHaveBeenCalledWith("Archive queued for Cinema.", "success");
        await waitFor(() => expect(screen.queryByText("Archive Cinema now?")).not.toBeInTheDocument());
    });

    it("keeps the dialog open while submission fails and reports the error", async () => {
        const idlePlan = {...plans[0]!, active_run_id: null, latest_run: completedRun};
        const api = createApi({
            plans: vi.fn().mockResolvedValue([idlePlan]),
            submitArchive: vi.fn().mockRejectedValue(new Error("Queue unavailable")),
        });
        const {value} = renderDashboard(api);
        fireEvent.click(await screen.findByRole("button", {name: "Archive now"}));
        fireEvent.click(screen.getByRole("button", {name: "Start archive", hidden: true}));
        await waitFor(() => expect(value.notify).toHaveBeenCalledWith("Queue unavailable", "danger"));
        expect(screen.getByText("Archive Cinema now?")).toBeVisible();
        fireEvent.click(screen.getByRole("button", {name: "Cancel", hidden: true}));
        await waitFor(() => expect(screen.queryByText("Archive Cinema now?")).not.toBeInTheDocument());
    });

    it("renders empty plans and a system attention state", async () => {
        renderDashboard(createApi({
            system: vi.fn().mockResolvedValue({...system, ready: false}),
            plans: vi.fn().mockResolvedValue([]),
            runs: vi.fn().mockResolvedValue({items: [], limit: 8, offset: 0, has_more: false}),
        }));
        expect(await screen.findByRole("heading", {name: "ECHO needs attention"})).toBeInTheDocument();
        expect(screen.getByText("No archive plans configured")).toBeInTheDocument();
        expect(screen.getByText("No runs recorded")).toBeInTheDocument();
    });

    it("shows the preparing state when an active run has not arrived yet", async () => {
        const startingPlan = {...plans[0]!, active_run_id: "run-pending", latest_run: completedRun};
        renderDashboard(createApi({plans: vi.fn().mockResolvedValue([startingPlan])}));
        expect(await screen.findByText("Starting")).toBeInTheDocument();
        expect(screen.getByText("Preparing operation…")).toBeInTheDocument();
        expect(screen.getByText("0 added · 0 verified")).toBeInTheDocument();
    });

    it("renders and retries a run-history error independently", async () => {
        const history = vi.fn()
            .mockImplementationOnce(() => Promise.reject(new Error("History failed")))
            .mockResolvedValue({items: [], limit: 8, offset: 0, has_more: false});
        const api = createApi({
            plans: vi.fn().mockResolvedValue([]),
            runs: history,
        });
        renderDashboard(api);
        expect(await screen.findByRole("alert")).toHaveTextContent("History failed");
        fireEvent.click(screen.getByRole("button", {name: /retry/i}));
        await waitFor(() => expect(history).toHaveBeenCalledTimes(2));
        expect(await screen.findByText("No runs recorded")).toBeInTheDocument();
    });

    it("uses the generic mutation error when a non-error is thrown", async () => {
        const idlePlan = {...plans[0]!, active_run_id: null, latest_run: completedRun};
        const api = createApi({
            plans: vi.fn().mockResolvedValue([idlePlan]),
            submitArchive: vi.fn().mockRejectedValue({unexpected: true}),
        });
        const {value} = renderDashboard(api);
        fireEvent.click(await screen.findByRole("button", {name: "Archive now"}));
        fireEvent.click(screen.getByRole("button", {name: "Start archive", hidden: true}));
        await waitFor(() => expect(value.notify).toHaveBeenCalledWith(
            "The archive could not be started.", "danger"));
    });

    it.each([
        ["system", "System failed"],
        ["plans", "Plans failed"],
    ] as const)("renders and retries %s query errors", async (endpoint, message) => {
        const failing = vi.fn()
            .mockRejectedValueOnce(new Error(message))
            .mockResolvedValue(endpoint === "system" ? system : plans);
        const api = createApi({[endpoint]: failing} as Partial<EchoApi>);
        renderDashboard(api);
        expect(await screen.findByRole("alert")).toHaveTextContent(message);
        fireEvent.click(screen.getByRole("button", {name: /retry/i}));
        await waitFor(() => expect(failing).toHaveBeenCalledTimes(2));
    });
});
