import {useEffect, useRef} from "react";
import {AlertTriangle, X} from "lucide-react";


type ConfirmDialogProps = {
    open: boolean;
    title: string;
    description: string;
    confirmLabel: string;
    tone?: "primary" | "danger";
    busy?: boolean;
    onCancel: () => void;
    onConfirm: () => void;
};

export function ConfirmDialog({
    open,
    title,
    description,
    confirmLabel,
    tone = "primary",
    busy = false,
    onCancel,
    onConfirm
}: ConfirmDialogProps) {
    const dialogRef = useRef<HTMLDialogElement>(null);

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!dialog) return;
        if (open && !dialog.open) dialog.showModal();
        if (!open && dialog.open) dialog.close();
    }, [open]);

    return (
        <dialog className="dialog" ref={dialogRef} onCancel={onCancel} onClose={onCancel}>
            <button className="icon-button dialog-close" aria-label="Close" type="button" onClick={onCancel}><X
                size={18}/></button>
            <div className={`dialog-icon dialog-icon-${tone}`}><AlertTriangle size={22}/></div>
            <h2>{title}</h2>
            <p>{description}</p>
            <div className="dialog-actions">
                <button className="button button-ghost" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
                <button className={`button ${tone === "danger" ? "button-danger" : "button-primary"}`} type="button"
                        onClick={onConfirm} disabled={busy}>
                    {busy ? "Working…" : confirmLabel}
                </button>
            </div>
        </dialog>
    );
}
