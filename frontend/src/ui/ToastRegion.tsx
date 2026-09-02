import {CheckCircle2, Info, TriangleAlert, X} from "lucide-react";
import type {ToastTone} from "../app/context";


export interface ToastItem {
    id: number;
    message: string;
    tone: ToastTone;
}

const icons = {success: CheckCircle2, danger: TriangleAlert, info: Info};

export function ToastRegion({toasts, dismiss}: { toasts: ToastItem[]; dismiss: (id: number) => void }) {
    return (
        <div className="toast-region" aria-live="polite" aria-atomic="true">
            {toasts.map((toast) => {
                const Icon = icons[toast.tone];
                return <div className={`toast toast-${toast.tone}`} key={toast.id}><Icon
                    size={18}/><span>{toast.message}</span>
                    <button type="button" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}><X
                        size={15}/></button>
                </div>;
            })}
        </div>
    );
}
