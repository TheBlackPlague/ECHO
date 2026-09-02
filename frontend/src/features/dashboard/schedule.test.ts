import {describe, expect, it} from "vitest";
import {describeSchedule} from "./schedule";


function displayTime(hour: number, minute: number): string {
    return new Intl.DateTimeFormat(undefined, {hour: "numeric", minute: "2-digit"})
        .format(new Date(2000, 0, 1, hour, minute));
}

describe("describeSchedule", () => {
    it.each([
        [null, true],
        ["0 2 * * *", false],
    ])("describes absent or disabled schedules as manual", (cron, scheduled) => {
        expect(describeSchedule(cron, scheduled)).toBe("Manual only");
    });

    it("describes interval, daily, weekly, and monthly schedules", () => {
        expect(describeSchedule("*/15 * * * *")).toBe("Every 15 minutes");
        expect(describeSchedule("30 2 * * *")).toBe(`Daily at ${displayTime(2, 30)}`);
        expect(describeSchedule("0 9 * * 1")).toBe(`Monday at ${displayTime(9, 0)}`);
        expect(describeSchedule("0 9 * * 7")).toBe(`Sunday at ${displayTime(9, 0)}`);
        expect(describeSchedule("45 18 20 * *")).toBe(`Monthly on day 20 at ${displayTime(18, 45)}`);
    });

    it.each(["bad cron", "*/5 1 * * *", "0 9 * 1 *", "x 9 * * *"])(
        "preserves unsupported cron expression %s", (cron) => {
            expect(describeSchedule(cron)).toBe(`Cron: ${cron}`);
        });
});
