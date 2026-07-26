from __future__ import annotations

from pathlib import Path

from client.config import (
    ClientConfig,
    ClientConfigError,
    DEFAULT_AUTO_CONNECT,
    DEFAULT_PC_NAME,
    DEFAULT_SERVER_HOST,
    DEFAULT_SERVER_PORT,
    load_client_config,
    save_client_config,
)


def build_setup_payload(
    server_host: str,
    server_port: str,
    pc_name: str,
    auto_connect: bool,
) -> dict[str, object]:
    try:
        parsed_port = int(server_port)
    except ValueError as error:
        raise ClientConfigError("Server Port must be a number.") from error

    return {
        "server_host": server_host.strip(),
        "server_port": parsed_port,
        "pc_name": pc_name.strip(),
        "auto_connect": bool(auto_connect),
    }


def load_existing_or_default(config_path: Path) -> dict[str, object]:
    try:
        config = load_client_config(config_path)
        return {
            "server_host": config.server_host,
            "server_port": config.server_port,
            "pc_name": config.pc_name,
            "auto_connect": config.auto_connect,
        }
    except ClientConfigError:
        return {
            "server_host": DEFAULT_SERVER_HOST,
            "server_port": DEFAULT_SERVER_PORT,
            "pc_name": DEFAULT_PC_NAME,
            "auto_connect": DEFAULT_AUTO_CONNECT,
        }


def show_setup_wizard(
    config_path: Path,
    *,
    title: str = "Secret Grounds Setup",
    allow_cancel: bool = True,
) -> ClientConfig | None:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError as error:
        raise ClientConfigError(
            "Tkinter is unavailable, so the first-run setup wizard cannot open."
        ) from error

    initial_values = load_existing_or_default(config_path)
    saved_config: ClientConfig | None = None

    root = tk.Tk()
    root.title(title)
    root.configure(background="#0F172A")
    root.resizable(False, False)

    def cancel() -> None:
        root.destroy()

    if allow_cancel:
        root.protocol("WM_DELETE_WINDOW", cancel)
    else:
        root.protocol("WM_DELETE_WINDOW", lambda: None)

    frame = tk.Frame(root, background="#0F172A", padx=22, pady=18)
    frame.grid(row=0, column=0, sticky="nsew")

    title_label = tk.Label(
        frame,
        text="Secret Grounds Client Setup",
        font=("Segoe UI", 15, "bold"),
        foreground="#E0F2FE",
        background="#0F172A",
    )
    title_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))

    server_host_var = tk.StringVar(value=str(initial_values["server_host"]))
    server_port_var = tk.StringVar(value=str(initial_values["server_port"]))
    pc_name_var = tk.StringVar(value=str(initial_values["pc_name"]))
    auto_connect_var = tk.BooleanVar(value=bool(initial_values["auto_connect"]))
    error_var = tk.StringVar(value="")

    def add_labeled_entry(row: int, label: str, variable: tk.StringVar) -> tk.Entry:
        tk.Label(
            frame,
            text=label,
            font=("Segoe UI", 10),
            foreground="#CBD5E1",
            background="#0F172A",
        ).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
        entry = tk.Entry(
            frame,
            textvariable=variable,
            width=28,
            font=("Segoe UI", 10),
            relief="flat",
        )
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        return entry

    first_entry = add_labeled_entry(1, "Server IP", server_host_var)
    add_labeled_entry(2, "Server Port", server_port_var)
    add_labeled_entry(3, "PC Name", pc_name_var)

    tk.Checkbutton(
        frame,
        text="Connect automatically",
        variable=auto_connect_var,
        font=("Segoe UI", 10),
        foreground="#E5E7EB",
        background="#0F172A",
        activeforeground="#E5E7EB",
        activebackground="#0F172A",
        selectcolor="#111827",
    ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))

    error_label = tk.Label(
        frame,
        textvariable=error_var,
        font=("Segoe UI", 9),
        foreground="#FCA5A5",
        background="#0F172A",
        wraplength=330,
        justify="left",
    )
    error_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(3, 10))

    button_row = tk.Frame(frame, background="#0F172A")
    button_row.grid(row=6, column=0, columnspan=2, sticky="e")

    if allow_cancel:
        tk.Button(button_row, text="Cancel", width=10, command=cancel).pack(
            side="left",
            padx=(0, 8),
        )

    def save() -> None:
        nonlocal saved_config
        try:
            payload = build_setup_payload(
                server_host_var.get(),
                server_port_var.get(),
                pc_name_var.get(),
                auto_connect_var.get(),
            )
            saved_config = save_client_config(payload, config_path)
        except ClientConfigError as error:
            error_var.set(str(error))
            return

        messagebox.showinfo(
            "Secret Grounds Setup",
            f"Configuration saved to:\n{config_path}",
        )
        root.destroy()

    tk.Button(button_row, text="Save", width=10, command=save).pack(side="left")

    root.bind("<Return>", lambda _: save())
    root.bind("<Escape>", lambda _: cancel() if allow_cancel else None)
    first_entry.focus_set()
    root.update_idletasks()
    width = root.winfo_reqwidth()
    height = root.winfo_reqheight()
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 3)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.mainloop()

    return saved_config
