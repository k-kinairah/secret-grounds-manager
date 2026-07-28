"use client";

import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

export type ToastVariant = "success" | "warning" | "info" | "error";

interface ToastRecord {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  pushToast: (message: string, variant?: ToastVariant) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const pushToast = useCallback((message: string, variant: ToastVariant = "info") => {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    setToasts((current) => [{ id, message, variant }, ...current].slice(0, 4));
    window.setTimeout(() => dismissToast(id), 4200);
  }, [dismissToast]);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toastStack" aria-live="polite" aria-label="Notifications">
        {toasts.map((toast) => (
          <div className={`toast toast--${toast.variant}`} key={toast.id} role="status">
            <span className="toast__icon" aria-hidden="true">{toast.variant.slice(0, 1).toUpperCase()}</span>
            <span>{toast.message}</span>
            <button aria-label="Dismiss notification" onClick={() => dismissToast(toast.id)} type="button">
              x
            </button>
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
