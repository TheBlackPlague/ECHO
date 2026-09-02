import {render, screen} from "@testing-library/react";
import {describe, expect, it} from "vitest";

import {Logo} from "./Logo";


describe("Logo", () => {
    it("renders the accessible ECHO identity", () => {
        render(<Logo/>);
        expect(screen.getByLabelText("ECHO — Emergency Copy Held Offsite")).toBeInTheDocument();
        expect(screen.getByRole("img", {name: "ECHO"})).toHaveAttribute("src", "/echo-icon.svg");
        expect(screen.getByText("Emergency Copy Held Offsite")).toBeInTheDocument();
    });
});
