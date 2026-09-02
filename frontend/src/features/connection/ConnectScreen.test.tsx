import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {describe, expect, it, vi} from "vitest";
import {ApiError} from "../../api/echo-api";
import {ConnectScreen} from "./ConnectScreen";


vi.mock("../../ui/Logo", () => ({Logo: () => <div>ECHO logo</div>}));

describe("ConnectScreen", () => {
    it("explains when login is unconfigured and disables all credentials controls", () => {
        render(<ConnectScreen loginEnabled={false} onConnect={vi.fn()}/>);
        expect(screen.getByRole("alert")).toHaveTextContent("ECHO_API__WEB_PASSWORD");
        expect(screen.getByLabelText("Password")).toBeDisabled();
        expect(screen.getByRole("button", {name: "Show password"})).toBeDisabled();
        expect(screen.getByRole("button", {name: "Log in"})).toBeDisabled();
    });

    it("toggles password visibility and submits the entered password", async () => {
        let resolve!: () => void;
        const onConnect = vi.fn(() => new Promise<void>((done) => {
            resolve = done;
        }));
        render(<ConnectScreen loginEnabled onConnect={onConnect}/>);
        const password = screen.getByLabelText("Password");

        fireEvent.change(password, {target: {value: "secret"}});
        fireEvent.click(screen.getByRole("button", {name: "Show password"}));
        expect(password).toHaveAttribute("type", "text");
        expect(screen.getByRole("button", {name: "Hide password"})).toBeInTheDocument();
        fireEvent.submit(password.closest("form")!);

        expect(onConnect).toHaveBeenCalledWith("secret");
        expect(screen.getByRole("button", {name: /signing in/i})).toBeDisabled();
        resolve();
        await waitFor(() => expect(screen.getByRole("button", {name: "Log in"})).toBeEnabled());
    });

    it("gives a safe message for rejected passwords", async () => {
        render(<ConnectScreen loginEnabled
                              onConnect={vi.fn().mockRejectedValue(new ApiError("Unauthorized", {status: 401}))}/>);
        fireEvent.change(screen.getByLabelText("Password"), {target: {value: "wrong"}});
        fireEvent.click(screen.getByRole("button", {name: "Log in"}));
        expect(await screen.findByRole("alert")).toHaveTextContent("That password wasn’t accepted");
    });

    it.each([
        [new Error("Server is offline"), "Server is offline"],
        [{unexpected: true}, "ECHO could not be reached."],
    ])("surfaces connection failure %#", async (failure, message) => {
        render(<ConnectScreen loginEnabled onConnect={vi.fn().mockRejectedValue(failure)}/>);
        fireEvent.change(screen.getByLabelText("Password"), {target: {value: "secret"}});
        fireEvent.click(screen.getByRole("button", {name: "Log in"}));
        expect(await screen.findByRole("alert")).toHaveTextContent(message);
    });
});
