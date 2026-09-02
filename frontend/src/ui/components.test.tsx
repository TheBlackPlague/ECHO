import {fireEvent, render, screen} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";
import {ApiError} from "../api/echo-api";
import {ConfirmDialog} from "./ConfirmDialog";
import {EmptyState, ErrorState, LoadingState} from "./QueryState";
import {RunStateBadge, StatusBadge} from "./StatusBadge";
import {ToastRegion} from "./ToastRegion";


describe("query states", () => {
    it("renders the requested number of loading placeholders", () => {
        const {container} = render(<LoadingState rows={4}/>);
        expect(screen.getByLabelText("Loading")).toBeInTheDocument();
        expect(container.querySelectorAll(".skeleton-row")).toHaveLength(4);
    });

    it("renders errors, request IDs, and retry actions", () => {
        const retry = vi.fn();
        render(<ErrorState error={new ApiError("Database unavailable", {
            status: 503, requestId: "request-17",
        })} retry={retry}/>);

        expect(screen.getByRole("alert")).toHaveTextContent("Database unavailable");
        expect(screen.getByText("Request request-17")).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", {name: /retry/i}));
        expect(retry).toHaveBeenCalledOnce();
    });

    it("uses a fallback for non-errors and omits optional controls", () => {
        render(<ErrorState error={{reason: "unknown"}}/>);
        expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong while loading this view.");
        expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("renders empty state content and actions", () => {
        render(<EmptyState title="Nothing here" action={<button>Configure</button>}>Add an archive plan.</EmptyState>);
        expect(screen.getByText("Nothing here")).toBeInTheDocument();
        expect(screen.getByText("Add an archive plan.")).toBeInTheDocument();
        expect(screen.getByRole("button", {name: "Configure"})).toBeInTheDocument();
    });
});

describe("status badges", () => {
    it("applies explicit tone and pulse styling", () => {
        const {container} = render(<StatusBadge label="Starting" tone="info" pulse/>);
        expect(screen.getByText("Starting")).toHaveClass("status-info");
        expect(container.querySelector(".status-dot")).toHaveClass("status-dot-pulse");
    });

    it.each([
        ["queued", "status-info", false],
        ["running", "status-info", true],
        ["succeeded", "status-success", false],
        ["failed", "status-danger", false],
        ["cancelled", "status-muted", false],
        ["interrupted", "status-warning", false],
    ] as const)("maps %s runs to their visual state", (state, tone, pulse) => {
        const {container} = render(<RunStateBadge state={state}/>);
        expect(screen.getByText(state[0]!.toUpperCase() + state.slice(1))).toHaveClass(tone);
        expect(container.querySelector(".status-dot")).toHaveClass(pulse ? "status-dot-pulse" : "status-dot");
        if (!pulse) expect(container.querySelector(".status-dot")).not.toHaveClass("status-dot-pulse");
    });
});

describe("ToastRegion", () => {
    it("renders tones and dismisses the selected notification", () => {
        const dismiss = vi.fn();
        render(<ToastRegion toasts={[
            {id: 4, message: "Archive queued", tone: "success"},
            {id: 7, message: "Connection lost", tone: "danger"},
            {id: 9, message: "Checking", tone: "info"},
        ]} dismiss={dismiss}/>);

        expect(screen.getByText("Archive queued").closest(".toast")).toHaveClass("toast-success");
        expect(screen.getByText("Connection lost").closest(".toast")).toHaveClass("toast-danger");
        fireEvent.click(screen.getAllByRole("button", {name: "Dismiss notification"})[1]!);
        expect(dismiss).toHaveBeenCalledWith(7);
    });
});

describe("ConfirmDialog", () => {
    const requiredProps = {
        title: "Archive photos now?",
        description: "The plan will start immediately.",
        confirmLabel: "Start archive",
        onCancel: vi.fn(),
        onConfirm: vi.fn(),
    };

    it("opens, confirms, and cancels", () => {
        const onCancel = vi.fn();
        const onConfirm = vi.fn();
        const {rerender} = render(<ConfirmDialog {...requiredProps} open onCancel={onCancel} onConfirm={onConfirm}/>);

        expect(screen.getByRole("dialog", {hidden: true})).toHaveAttribute("open");
        fireEvent.click(screen.getByRole("button", {name: "Start archive", hidden: true}));
        fireEvent.click(screen.getByRole("button", {name: "Close", hidden: true}));
        expect(onConfirm).toHaveBeenCalledOnce();
        expect(onCancel).toHaveBeenCalledOnce();

        rerender(<ConfirmDialog {...requiredProps} open={false}/>);
        expect(screen.getByRole("dialog", {hidden: true})).not.toHaveAttribute("open");
    });

    it("disables actions and shows progress for busy destructive actions", () => {
        render(<ConfirmDialog {...requiredProps} open tone="danger" busy/>);
        expect(screen.getByRole("button", {name: "Cancel", hidden: true})).toBeDisabled();
        expect(screen.getByRole("button", {name: "Working…", hidden: true})).toBeDisabled();
        expect(screen.getByRole("button", {name: "Working…", hidden: true})).toHaveClass("button-danger");
    });
});
