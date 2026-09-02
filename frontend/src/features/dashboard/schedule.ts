const weekdays = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export function describeSchedule(cron: string | null | undefined, scheduled = Boolean(cron)): string {
    if (!cron || !scheduled) return "Manual only";

    const [minute, hour, day, month, weekday] = cron.trim().split(/\s+/);
    if ([minute, hour, day, month, weekday].some((field) => field === undefined)) return `Cron: ${cron}`;

    if (minute?.startsWith("*/") && hour === "*" && day === "*" && month === "*" && weekday === "*") {
        return `Every ${minute.slice(2)} minutes`;
    }

    if (isNumber(minute) && isNumber(hour) && month === "*") {
        const time = formatTime(Number(hour), Number(minute));
        if (day === "*" && weekday === "*") return `Daily at ${time}`;
        if (day === "*" && isNumber(weekday)) return `${weekdays[Number(weekday) % 7]} at ${time}`;
        if (isNumber(day) && weekday === "*") return `Monthly on day ${Number(day)} at ${time}`;
    }

    return `Cron: ${cron}`;
}

function isNumber(value: string | undefined): value is string {
    return value !== undefined && /^\d+$/.test(value);
}

function formatTime(hour: number, minute: number): string {
    const date = new Date(2000, 0, 1, hour, minute);
    return new Intl.DateTimeFormat(undefined, {hour: "numeric", minute: "2-digit"}).format(date);
}
