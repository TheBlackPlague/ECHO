import {useState} from "react";
import {useMutation, useQueries, useQuery, useQueryClient} from "@tanstack/react-query";
import {CalendarClock, Clock3, History, Play, RotateCcw, ShieldCheck} from "lucide-react";
import type {ArchivePlanResponse as ArchivePlan} from "../../api/generated/types.gen";
import {useApp} from "../../app/context";
import {ConfirmDialog} from "../../ui/ConfirmDialog";
import {EmptyState, ErrorState, LoadingState} from "../../ui/QueryState";
import {RunStateBadge, StatusBadge} from "../../ui/StatusBadge";
import {formatDate, formatDuration, formatRelativeTime, titleCase} from "./format";
import {describeSchedule} from "./schedule";


const HISTORY_SIZE = 8;
const LIVE_STATUS_INTERVAL = 1_000;

export function Dashboard() {
    const {api, notify} = useApp();
    const queryClient = useQueryClient();
    const [selectedPlan, setSelectedPlan] = useState<ArchivePlan | null>(null);

    const system = useQuery({
        queryKey: ["system"],
        queryFn: ({signal}) => api.system(signal),
        refetchInterval: 10_000,
    });
    const plans = useQuery({
        queryKey: ["plans"],
        queryFn: ({signal}) => api.plans(signal),
        refetchInterval: LIVE_STATUS_INTERVAL,
    });
    const history = useQuery({
        queryKey: ["runs", "dashboard"],
        queryFn: ({signal}) => api.runs({limit: HISTORY_SIZE}, signal),
        refetchInterval: (query) => query.state.data?.items.some(
            (run) => run.state === "running" || run.state === "queued")
            ? LIVE_STATUS_INTERVAL
            : 10_000,
    });
    const archiveHistory = useQueries({
        queries: (plans.data ?? []).map((plan) => ({
            queryKey: ["runs", "latest-archive", plan.name],
            queryFn: ({signal}: { signal: AbortSignal }) => api.runs({
                plan_name: plan.name,
                operation: "archive",
                limit: 100,
            }, signal),
            refetchInterval: 10_000,
        })),
    });
    const submit = useMutation({
        mutationFn: (plan: ArchivePlan) => api.submitArchive(plan.name),
        onSuccess: (run, plan) => {
            queryClient.setQueryData<ArchivePlan[]>(["plans"], (current) => current?.map((item) => (
                item.name === plan.name
                    ? {...item, active_run_id: run.id, latest_run: run}
                    : item
            )));
            notify(`Archive queued for ${plan.name}.`, "success");
            setSelectedPlan(null);
            void queryClient.invalidateQueries();
        },
        onError: (error) => notify(
            error instanceof Error ? error.message : "The archive could not be started.", "danger"),
    });

    if (system.isPending || plans.isPending) return <LoadingState rows={6}/>;
    if (system.isError) return <ErrorState error={system.error} retry={() => void system.refetch()}/>;
    if (plans.isError) return <ErrorState error={plans.error} retry={() => void plans.refetch()}/>;

    const operational = system.data.ready;
    const activePlans = plans.data.filter((plan) => plan.active_run_id).length;

    return (
        <div className="dashboard-stack">
            <section className={`system-banner${operational ? " system-banner-live" : ""}`}>
                <div className="system-symbol"><ShieldCheck size={26}/></div>
                <div className="system-copy">
                    <span className="eyebrow">System Status</span>
                    <h1>{operational ? "ECHO is operational" : "ECHO needs attention"}</h1>
                    <p>{operational ? "Your configured archive plans are being monitored." : "The archive service is not ready to accept work."}</p>
                </div>
                <dl className="system-totals">
                    <div>
                        <dt>Archive plans</dt>
                        <dd>{plans.data.length}</dd>
                    </div>
                    <div>
                        <dt>Active now</dt>
                        <dd>{activePlans}</dd>
                    </div>
                </dl>
            </section>

            <section aria-labelledby="plans-title">
                <div className="section-heading">
                    <div><h2 id="plans-title">Archive Plans</h2></div>
                    <span className="section-count">{plans.data.length}</span>
                </div>

                {plans.data.length === 0 ? (
                    <div className="card"><EmptyState title="No archive plans configured">Add an archive plan to ECHO’s
                        configuration to begin monitoring it.</EmptyState></div>
                ) : (
                    <div className="plans-grid">
                        {plans.data.map((plan, index) => {
                            const latestArchive = archiveHistory[index]?.data?.items.find((run) => !run.dry_run);
                            const activeRun = plan.active_run_id && plan.latest_run?.id === plan.active_run_id
                                ? plan.latest_run
                                : null;
                            return (
                                <article className="plan-card" key={plan.name}>
                                    <div className="plan-heading">
                                        <div>
                                            <h3>{plan.name}</h3>
                                            <StatusBadge
                                                label={activeRun ? `${titleCase(
                                                    activeRun.operation)} in progress` : plan.active_run_id ? "Starting" : plan.enabled ? "Ready" : "Disabled"}
                                                tone={plan.active_run_id ? "info" : plan.enabled ? "success" : "muted"}
                                                pulse={Boolean(plan.active_run_id)}
                                            />
                                        </div>
                                        <button
                                            className="button button-primary plan-run-button"
                                            type="button"
                                            onClick={() => setSelectedPlan(plan)}
                                            disabled={!plan.enabled || Boolean(plan.active_run_id)}
                                        >
                                            <Play size={15} fill="currentColor"/>
                                            {plan.active_run_id ? "Running" : "Archive now"}
                                        </button>
                                    </div>

                                    {plan.active_run_id && (
                                        <div className="plan-progress" aria-live="polite">
                                            <div className="plan-progress-heading">
                                                <span>{activeRun?.progress == null ? "Preparing operation…" : `${Math.round(
                                                    activeRun.progress)}% complete`}</span>
                                                <strong>{activeRun?.files_added ?? 0} added
                                                    · {activeRun?.files_verified ?? 0} verified</strong>
                                            </div>
                                            <progress value={activeRun?.progress ?? undefined} max="100"
                                                      aria-label="Archive operation progress"/>
                                        </div>
                                    )}

                                    <div className="plan-facts">
                                        <div className="plan-fact plan-fact-primary">
                                            <Clock3 size={19}/>
                                            <div>
                                                <span>Last archived</span>
                                                <strong>{latestArchive ? formatRelativeTime(
                                                    latestArchive.finished_at ?? latestArchive.started_at ?? latestArchive.created_at) : "Never"}</strong>
                                                <small>{latestArchive ? formatDate(
                                                    latestArchive.finished_at ?? latestArchive.started_at ?? latestArchive.created_at) : "No archive operation recorded"}</small>
                                            </div>
                                            {latestArchive && <RunStateBadge state={latestArchive.state}/>}
                                        </div>
                                        <div className="plan-fact">
                                            <CalendarClock size={19}/>
                                            <div>
                                                <span>Schedule</span>
                                                <strong>{describeSchedule(plan.cron, plan.scheduled)}</strong>
                                                <small>{plan.scheduled ? "Server local time" : "Runs only when started manually"}</small>
                                            </div>
                                        </div>
                                    </div>
                                </article>
                            );
                        })}
                    </div>
                )}
            </section>

            <section className="card history-card" aria-labelledby="history-title">
                <div className="section-heading card-heading">
                    <div><span className="eyebrow">Recent activity</span><h2 id="history-title"><History size={20}/> Run
                        history</h2></div>
                    <button className="icon-button" type="button" aria-label="Refresh run history"
                            onClick={() => void history.refetch()}>
                        <RotateCcw size={16} className={history.isFetching ? "spin" : ""}/>
                    </button>
                </div>
                {history.isPending ? <LoadingState rows={5}/> : history.isError ? (
                    <ErrorState error={history.error} retry={() => void history.refetch()}/>
                ) : history.data.items.length === 0 ? (
                    <EmptyState title="No runs recorded">Archive activity will appear here.</EmptyState>
                ) : (
                    <div className="table-scroll">
                        <table className="history-table">
                            <thead>
                            <tr>
                                <th>Plan</th>
                                <th>Operation</th>
                                <th>Status</th>
                                <th>Files</th>
                                <th>Started</th>
                                <th>Duration</th>
                            </tr>
                            </thead>
                            <tbody>
                            {history.data.items.map((run) => (
                                <tr key={run.id}>
                                    <td>
                                        <strong>{run.plan_name}</strong><small>{titleCase(
                                        run.trigger)}{run.dry_run ? " · Dry run" : ""}</small>
                                    </td>
                                    <td>{titleCase(run.operation)}</td>
                                    <td><RunStateBadge state={run.state}/></td>
                                    <td>
                                        <strong>{run.files_added} added</strong><small>{run.files_verified} verified</small>
                                    </td>
                                    <td>
                                        <time
                                            dateTime={run.started_at ?? run.created_at}>{formatRelativeTime(
                                            run.started_at ?? run.created_at)}</time>
                                    </td>
                                    <td>{formatDuration(run.duration_seconds)}</td>
                                </tr>
                            ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </section>

            <ConfirmDialog
                open={selectedPlan !== null}
                title={selectedPlan ? `Archive ${selectedPlan.name} now?` : "Start archive?"}
                description="ECHO will start this archive plan immediately. Its progress will appear here and in run history."
                confirmLabel="Start archive"
                busy={submit.isPending}
                onCancel={() => {
                    if (!submit.isPending) setSelectedPlan(null);
                }}
                onConfirm={() => {
                    if (selectedPlan) submit.mutate(selectedPlan);
                }}
            />
        </div>
    );
}
