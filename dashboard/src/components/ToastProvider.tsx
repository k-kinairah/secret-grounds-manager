"use client";

import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";
import { CloseButton } from "./CloseButton";

export type ToastVariant = "success" | "warning" | "info" | "error";

interface ToastRecord {
  id: number;
  title: string;
  message?: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  pushToast: (title: string, variant?: ToastVariant, message?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const pushToast = useCallback((title: string, variant: ToastVariant = "info", message?: string) => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    const durationMs = variant === "success" || variant === "info" ? 4800 : 8500;
    setToasts((current) => [
      { id, title, message, variant },
      ...current.filter(
        (toast) => toast.title !== title || toast.message !== message || toast.variant !== variant
      )
    ].slice(0, 4));
    window.setTimeout(() => dismissToast(id), durationMs);
  }, [dismissToast]);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toastStack" aria-live="polite" aria-label="Notifications">
        {toasts.map((toast) => (
          <div className={`toast toast--${toast.variant}`} key={toast.id} role="status">
            <span className="toast__icon" aria-hidden="true">{toast.variant.slice(0, 1).toUpperCase()}</span>
            <span className="toast__body">
              <strong>{toast.title}</strong>
              {toast.message ? <small>{toast.message}</small> : null}
            </span>
            <CloseButton label="Dismiss notification" onClick={() => dismissToast(toast.id)} size="small" />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (context === null) {
    return { pushToast: () => undefined };
  }
  return context;
}
