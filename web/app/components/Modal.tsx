"use client";
import { useEffect, useRef, type ReactNode } from "react";
import Icon from "./Icon";

// Native <dialog>: focus trap, Escape and the top layer come from the browser.
// busy: a request is in flight, so Escape, the backdrop and the X do not close the dialog under it.
export default function Modal({ title, subtitle, busy = false, onClose, children }: { title: string; subtitle?: string; busy?: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog ref={ref} className="modal" aria-labelledby="modal-title" onClose={onClose}
      onCancel={(e) => busy && e.preventDefault()}
      onClick={(e) => !busy && e.target === e.currentTarget && ref.current?.close()}>
      <div className="modal-head">
        <div>
          <p className="eyebrow">Vimo workspace</p>
          <h2 id="modal-title">{title}</h2>
          {subtitle && <p>{subtitle}</p>}
        </div>
        <button className="modal-close" disabled={busy} onClick={() => ref.current?.close()} aria-label="Close dialog"><Icon name="close" /></button>
      </div>
      {children}
    </dialog>
  );
}
