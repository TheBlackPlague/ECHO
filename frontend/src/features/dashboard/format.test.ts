import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {formatDate, formatDuration, formatRelativeTime, titleCase} from "./format";


describe("dashboard formatters", () => {
    beforeEach(() => {
        vi.useFakeTimers();
        vi.setSystemTime(new Date("2026-01-15T12:00:00Z"));
    });

    afterEach(() => vi.useRealTimers());

    it("formats valid dates and falls back for absent or invalid dates", () => {
        const value = "2026-01-15T10:30:00Z";
        expect(formatDate(value)).toBe(new Intl.DateTimeFormat(undefined, {
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
        }).format(new Date(value)));
        expect(formatDate(null)).toBe("Never");
        expect(formatDate("not-a-date", "Unavailable")).toBe("Unavailable");
    });

    it.each([
        ["2028-01-15T12:00:00Z", "year"],
        ["2026-03-16T12:00:00Z", "month"],
        ["2026-01-17T12:00:00Z", "day"],
        ["2026-01-15T15:00:00Z", "hour"],
        ["2026-01-15T12:05:00Z", "minute"],
    ])("selects the appropriate relative unit for %s", (value, unit) => {
        expect(formatRelativeTime(value)).toBe(new Intl.RelativeTimeFormat(undefined, {numeric: "auto"})
            .format(Math.round((new Date(value).getTime() - Date.now()) / ({
                year: 365 * 24 * 60 * 60 * 1000,
                month: 30 * 24 * 60 * 60 * 1000,
                day: 24 * 60 * 60 * 1000,
                hour: 60 * 60 * 1000,
                minute: 60 * 1000,
            }[unit]!)), unit as Intl.RelativeTimeFormatUnit));
    });

    it("handles missing and invalid relative dates", () => {
        expect(formatRelativeTime(undefined)).toBe("Never");
        expect(formatRelativeTime("invalid")).toBe("Unknown");
    });

    it.each([
        [null, "—"],
        [undefined, "—"],
        [Number.NaN, "—"],
        [12.6, "13s"],
        [90.4, "1m 30s"],
        [3_661, "1h 1m"],
    ])("formats duration %s", (seconds, expected) => {
        expect(formatDuration(seconds)).toBe(expected);
    });

    it("turns underscored identifiers into titles", () => {
        expect(titleCase("verify_after_archive")).toBe("Verify After Archive");
    });
});
