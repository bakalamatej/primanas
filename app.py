from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import quote_plus
from typing import Any, Callable

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

load_dotenv()

SCHEMA = os.getenv("PGSCHEMA", "public")
TABLES = ("images", "processes", "events")
PAGE_SIZE = 100


def database_engine() -> Engine:
    url = os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("PGHOST", "localhost")
        port = os.getenv("PGPORT", "5432")
        database = os.getenv("PGDATABASE")
        user = os.getenv("PGUSER")
        password = os.getenv("PGPASSWORD")
        if not all((database, user, password)):
            raise RuntimeError(
                "Chýba PGDATABASE, PGUSER alebo PGPASSWORD v súbore .env."
            )
        url = (
            f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{database}"
        )
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=15000"
        },
    )


def identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def value_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class Explorer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PostgreSQL Data Explorer")
        self.geometry("1400x850")
        self.minsize(900, 600)
        self.engine: Engine | None = None
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.columns: dict[str, list[str]] = {}
        self.current_table = "images"
        self.current_rows: list[dict[str, Any]] = []
        self.loading = False
        self.search_job: str | None = None
        self.image_node_count = 0

        self.status = tk.StringVar(value="Pripájam sa k databáze…")
        self.search = tk.StringVar()
        self.page = tk.IntVar(value=1)
        self.page_info = tk.StringVar(value="")

        self._build_ui()
        self.after(50, self._process_ui_queue)
        self.after(100, self._start_connection)

    def _build_ui(self) -> None:
        self.configure(background="#f4f4f4")
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.nodes: list[tuple[str, str, Any]] = []
        header = tk.Frame(self, background="#f4f4f4")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=10)
        tk.Button(header, text="PostgreSQL Data Explorer", command=lambda: None).pack(side="left")
        self.status_label = tk.Button(header, textvariable=self.status, command=lambda: None)
        self.status_label.pack(side="left", padx=12)
        tk.Button(header, text="Obnoviť", command=self.refresh_current).pack(side="right")

        toolbar = tk.Frame(self, background="#f4f4f4")
        toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        tk.Button(toolbar, text="Vyhľadávanie", command=lambda: self.search_entry.focus_set()).pack(side="left")
        search_box = tk.Frame(toolbar, background="#ffffff", highlightbackground="#888888",
                              highlightthickness=1, width=330, height=28)
        search_box.pack(side="left", padx=8)
        search_box.pack_propagate(False)
        self.search_entry = tk.Entry(
            search_box,
            textvariable=self.search,
            background="#ffffff",
            foreground="#000000",
            insertbackground="#000000",
            selectbackground="#3478c9",
            selectforeground="#ffffff",
            relief="flat",
            borderwidth=0,
        )
        self.search_entry.pack(fill="both", expand=True, padx=4)
        self.search_entry.bind("<Return>", lambda _event: self.refresh_current())
        self.search_entry.bind("<KeyRelease>", self._search_changed)
        tk.Button(toolbar, text="Hľadať", command=self.refresh_current).pack(side="left")
        tk.Button(toolbar, text="Vymazať", command=self.clear_search).pack(side="left", padx=5)

        body = tk.Frame(self, background="#f4f4f4")
        body.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, background="#f4f4f4", width=310)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_propagate(False)
        tk.Button(left, text="Hierarchia databázy", command=lambda: None).pack(fill="x", pady=(0, 4))
        tk.Button(left, text="Image -> Processes -> Events", command=lambda: None).pack(fill="x", pady=(0, 4))
        tree_box = tk.Frame(left, background="#ffffff", highlightbackground="#888888",
                            highlightthickness=1)
        tree_box.pack(fill="both", expand=True)
        self.tree_list = tk.Listbox(
            tree_box,
            activestyle="none",
            exportselection=False,
            background="#ffffff",
            foreground="#000000",
            selectbackground="#3478c9",
            selectforeground="#ffffff",
            font=("Helvetica", 11),
            height=20,
            relief="solid",
            borderwidth=1,
        )
        self.tree_list.pack(fill="both", expand=True, padx=2, pady=2)
        self.tree_list.bind("<<ListboxSelect>>", self._list_selected)

        right = tk.Frame(body, background="#f4f4f4")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        self.title_button = tk.Button(right, text="Images", command=lambda: None)
        self.title_button.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        table_box = tk.Frame(right, background="#ffffff", highlightbackground="#888888",
                             highlightthickness=1)
        table_box.grid(row=1, column=0, sticky="nsew")
        self.table_list = tk.Listbox(
            table_box,
            activestyle="none",
            background="#ffffff",
            foreground="#000000",
            selectbackground="#3478c9",
            selectforeground="#ffffff",
            font=("Courier", 10),
            height=20,
            relief="solid",
            borderwidth=1,
        )
        self.table_list.pack(fill="both", expand=True, padx=2, pady=2)
        self.page_button = tk.Button(right, textvariable=self.page_info, command=self.next_page)
        self.page_button.grid(row=2, column=0, sticky="ew", pady=(5, 0))

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="white",
            fieldbackground="white",
            foreground="black",
            rowheight=25,
        )
        style.configure(
            "Treeview.Heading",
            background="#dddddd",
            foreground="black",
            font=("Helvetica", 10, "bold"),
        )
        style.configure(
            "TScrollbar",
            background="#dddddd",
            troughcolor="#f4f4f4",
            arrowcolor="black",
        )
        style.configure(
            "TLabel",
            background="white",
            foreground="black",
        )

    def _start_connection(self) -> None:
        self._run_async(self._connect_and_inspect, self._connection_finished)

    def _connect_and_inspect(self) -> tuple[Engine, dict[str, list[str]]]:
        engine = database_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        inspector = inspect(engine)
        columns = {
            table: [column["name"] for column in inspector.get_columns(table, schema=SCHEMA)]
            for table in TABLES
            if inspector.has_table(table, schema=SCHEMA)
        }
        return engine, columns

    def _connection_finished(
        self, result: tuple[Engine, dict[str, list[str]]] | None, error: Exception | None
    ) -> None:
        if error:
            self.status.set("Pripojenie zlyhalo")
            self.status_label.configure(text=self.status.get())
            messagebox.showerror("Databáza", str(error), parent=self)
            return
        assert result is not None
        self.engine, self.columns = result
        missing = [table for table in TABLES if table not in self.columns]
        if missing:
            self.status.set("Chýbajú tabuľky")
            self.status_label.configure(text=self.status.get())
            messagebox.showerror(
                "Databázová schéma",
                "Nenašli sa tabuľky: " + ", ".join(missing),
                parent=self,
            )
            return
        self.status.set(f"Pripojené • schéma {SCHEMA}")
        self.status_label.configure(text=self.status.get())
        self._load_images()

    def _load_images(self) -> None:
        self.current_table = "images"
        self.page.set(1)
        where_sql, params = self._search_clause("images")
        self._run_query(
            "images",
            f"SELECT * FROM {identifier(SCHEMA)}.{identifier('images')} "
            f"{where_sql} ORDER BY id LIMIT :limit",
            {**params, "limit": PAGE_SIZE},
            "Images",
        )

    def _tree_selected(self, _event: tk.Event) -> None:
        if not self.engine:
            return
        kind = getattr(self, "_selected_kind", "")
        node_id = getattr(self, "_selected_id", None)
        if kind == "image":
            self.current_table = "processes"
            self.page.set(1)
            where_sql, params = self._search_clause("processes")
            self._run_query(
                "processes",
                f"SELECT * FROM {identifier(SCHEMA)}.{identifier('processes')} "
                f"WHERE image_id = :node_id {where_sql.replace('WHERE ', 'AND ', 1)} ORDER BY id LIMIT :limit",
                {**params, "node_id": node_id, "limit": PAGE_SIZE},
                f"Processes pre image {node_id}",
            )
        elif kind == "process":
            self.current_table = "events"
            self.page.set(1)
            where_sql, params = self._search_clause("events")
            self._run_query(
                "events",
                f"SELECT * FROM {identifier(SCHEMA)}.{identifier('events')} "
                f"WHERE process_id = :node_id {where_sql.replace('WHERE ', 'AND ', 1)} "
                "ORDER BY event_time DESC NULLS LAST LIMIT :limit",
                {**params, "node_id": node_id, "limit": PAGE_SIZE},
                f"Events pre process {node_id}",
            )

    def _list_selected(self, _event: tk.Event) -> None:
        selection = self.tree_list.curselection()
        if not selection:
            return
        _label, kind, node_id = self.nodes[selection[0]]
        self._selected_kind = kind
        self._selected_id = node_id
        self._tree_selected(_event)

    def _search_clause(self, table: str) -> tuple[str, dict[str, str]]:
        term = self.search.get().strip()
        if not term:
            return "", {}
        text_columns = [
            column for column in self.columns.get(table, [])
            if any(marker in column.lower() for marker in ("name", "path", "cmd", "type", "host", "url", "owner"))
        ]
        if not text_columns:
            return "", {}
        parts = [
            f"CAST({identifier(column)} AS TEXT) ILIKE :search_term"
            for column in text_columns[:20]
        ]
        return "WHERE (" + " OR ".join(parts) + ")", {"search_term": f"%{term}%"}

    def _run_query(self, table: str, sql: str, params: dict[str, Any], title: str) -> None:
        if self.loading or not self.engine:
            return
        self.loading = True
        self.title_button.configure(text=title)
        self._run_async(
            lambda: self._execute_query(sql, params),
            lambda result, error: self._query_finished(table, result, error),
        )

    def _execute_query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert self.engine is not None
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return [dict(row) for row in connection.execute(text(sql), params).mappings()]

    def _query_finished(
        self, table: str, rows: list[dict[str, Any]] | None, error: Exception | None
    ) -> None:
        self.loading = False
        if error:
            self.status.set("Dotaz zlyhal")
            self.status_label.configure(text=self.status.get())
            messagebox.showerror("SQL dotaz", str(error), parent=self)
            return
        assert rows is not None
        self.current_rows = rows
        self._show_rows(table, rows)
        self._populate_hierarchy(table, rows)

    def _show_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        columns = self.columns.get(table, list(rows[0]) if rows else [])
        self.table_list.delete(0, tk.END)
        if columns:
            self.table_list.insert(tk.END, " | ".join(columns))
        if rows:
            for row in rows[:PAGE_SIZE]:
                self.table_list.insert(
                    tk.END,
                    " | ".join(value_text(row.get(column))[:40] for column in columns),
                )
        else:
            self.table_list.insert(tk.END, "Žiadne záznamy pre aktuálny výber.")
        self.page_info.set(f"{len(rows)} záznamov • tabuľka {table}")

    def _populate_hierarchy(self, table: str, rows: list[dict[str, Any]]) -> None:
        if table == "images":
            self.tree_list.delete(0, tk.END)
            self.nodes.clear()
            self.image_node_count = 0
            for row in rows:
                image_id = row.get("id")
                label = f"Image {image_id}: {row.get('original_name') or row.get('path') or ''}"
                self.nodes.append((label, "image", image_id))
                self.tree_list.insert(tk.END, label[:45])
            self.image_node_count = len(self.nodes)
        elif table == "processes":
            if self.image_node_count:
                self.tree_list.delete(self.image_node_count, tk.END)
                self.nodes = self.nodes[:self.image_node_count]
            for row in rows:
                process_id = row.get("id")
                label = f"Process {process_id}: {row.get('name') or ''}"
                self.nodes.append((label, "process", process_id))
                self.tree_list.insert(tk.END, f"  {label[:43]}")

    def refresh_current(self) -> None:
        if self.current_table == "images":
            self._load_images()
        elif hasattr(self, "_selected_kind"):
            self._tree_selected(tk.Event())

    def clear_search(self) -> None:
        self.search.set("")
        self.refresh_current()

    def _search_changed(self, _event: tk.Event) -> None:
        if self.search_job is not None:
            self.after_cancel(self.search_job)
        self.search_job = self.after(350, self._search_finished)

    def _search_finished(self) -> None:
        self.search_job = None
        self.refresh_current()

    def previous_page(self) -> None:
        if self.page.get() > 1:
            self.page.set(self.page.get() - 1)
            self.refresh_current()

    def next_page(self) -> None:
        self.page.set(self.page.get() + 1)
        self.refresh_current()

    def _run_async(self, operation: Callable[[], Any], callback: Callable[[Any, Exception | None], None]) -> None:
        def worker() -> None:
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, exc
            self.ui_queue.put(lambda: callback(result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _process_ui_queue(self) -> None:
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._process_ui_queue)


if __name__ == "__main__":
    Explorer().mainloop()
