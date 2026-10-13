from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
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
    return "" if value is None else str(value)


class Explorer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PostgreSQL Data Explorer")
        self.geometry("1450x850")
        self.minsize(950, 600)
        self.configure(bg="#f3f5f7")

        self.engine: Engine | None = None
        self.columns: dict[str, list[str]] = {}
        self.ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.query_generation = 0
        self.loading = False

        self.current_table = "images"
        self.current_image_id: Any = None
        self.current_process_id: Any = None
        self.current_rows: list[dict[str, Any]] = []
        self.current_page = 0
        self.current_title = "Images"

        self.status = tk.StringVar(value="Pripájam sa k databáze…")
        self.search = tk.StringVar()
        self.page_info = tk.StringVar(value="")

        self._build_ui()
        self.after(50, self._process_ui_queue)
        self.after(100, self._start_connection)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background="white",
            fieldbackground="white",
            foreground="#202124",
            rowheight=27,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background="#e9edf2",
            foreground="#202124",
            font=("Helvetica", 10, "bold"),
            padding=(6, 7),
        )
        style.map(
            "Treeview",
            background=[("selected", "#cfe4ff")],
            foreground=[("selected", "#172b4d")],
        )

        header = tk.Frame(self, bg="#f3f5f7")
        header.pack(fill="x", padx=16, pady=(14, 8))

        tk.Label(
            header,
            text="PostgreSQL Data Explorer",
            bg="#f3f5f7",
            fg="#202124",
            font=("Helvetica", 18, "bold"),
        ).pack(side="left")

        tk.Label(
            header,
            textvariable=self.status,
            bg="#f3f5f7",
            fg="#52606d",
            font=("Helvetica", 10),
        ).pack(side="left", padx=(18, 0))

        ttk.Button(header, text="Obnoviť", command=self.refresh_current).pack(
            side="right"
        )

        toolbar = tk.Frame(self, bg="#f3f5f7")
        toolbar.pack(fill="x", padx=16, pady=(0, 10))

        tk.Label(
            toolbar, text="Filter:", bg="#f3f5f7", fg="#202124"
        ).pack(side="left")

        search_entry = ttk.Entry(toolbar, textvariable=self.search, width=38)
        search_entry.pack(side="left", padx=(8, 6))
        search_entry.bind("<Return>", lambda _event: self.apply_search())

        ttk.Button(toolbar, text="Hľadať", command=self.apply_search).pack(side="left")
        ttk.Button(toolbar, text="Vymazať filter", command=self.clear_search).pack(
            side="left", padx=(6, 0)
        )

        main = tk.Frame(self, bg="#f3f5f7")
        main.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, minsize=300, weight=0)
        main.grid_columnconfigure(1, weight=1)

        left = ttk.Frame(main, padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        ttk.Label(left, text="Images", font=("Helvetica", 13, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        image_frame = ttk.Frame(left)
        image_frame.grid(row=1, column=0, sticky="nsew")
        image_frame.grid_rowconfigure(0, weight=1)
        image_frame.grid_columnconfigure(0, weight=1)

        self.image_tree = ttk.Treeview(
            image_frame,
            columns=("id", "name", "process_count"),
            show="headings",
            selectmode="browse",
        )
        self.image_tree.heading("id", text="ID")
        self.image_tree.heading("name", text="Názov / cesta")
        self.image_tree.heading("process_count", text="Procesy")
        self.image_tree.column("id", width=55, minwidth=45, stretch=False, anchor="e")
        self.image_tree.column("name", width=185, minwidth=100, stretch=True)
        self.image_tree.column(
            "process_count", width=75, minwidth=65, stretch=False, anchor="center"
        )
        image_scroll = ttk.Scrollbar(
            image_frame, orient="vertical", command=self.image_tree.yview
        )
        self.image_tree.configure(yscrollcommand=image_scroll.set)
        self.image_tree.grid(row=0, column=0, sticky="nsew")
        image_scroll.grid(row=0, column=1, sticky="ns")
        self.image_tree.bind("<<TreeviewSelect>>", self._image_selected)

        right = ttk.Frame(main, padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.content_title = ttk.Label(
            right, text="Vyber obrázok", font=("Helvetica", 13, "bold")
        )
        self.content_title.grid(row=0, column=0, sticky="w", pady=(0, 8))

        result_frame = ttk.Frame(right)
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.grid_rowconfigure(0, weight=1)
        result_frame.grid_columnconfigure(0, weight=1)

        self.result_tree = ttk.Treeview(
            result_frame, show="headings", selectmode="browse"
        )
        y_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.result_tree.yview
        )
        x_scroll = ttk.Scrollbar(
            result_frame, orient="horizontal", command=self.result_tree.xview
        )
        self.result_tree.configure(
            yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set
        )
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.result_tree.bind("<<TreeviewSelect>>", self._process_selected)

        footer = ttk.Frame(right)
        footer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        footer.grid_columnconfigure(1, weight=1)
        self.prev_button = ttk.Button(
            footer, text="← Predchádzajúca", command=self.previous_page, state="disabled"
        )
        self.prev_button.grid(row=0, column=0, sticky="w")
        ttk.Label(footer, textvariable=self.page_info).grid(
            row=0, column=1, sticky="ew", padx=10
        )
        self.next_button = ttk.Button(
            footer, text="Nasledujúca →", command=self.next_page, state="disabled"
        )
        self.next_button.grid(row=0, column=2, sticky="e")

    def _start_connection(self) -> None:
        self._run_async(self._connect_and_inspect, self._connection_finished)

    def _connect_and_inspect(self) -> tuple[Engine, dict[str, list[str]]]:
        engine = database_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        inspector = inspect(engine)
        columns = {
            table: [
                column["name"]
                for column in inspector.get_columns(table, schema=SCHEMA)
            ]
            for table in TABLES
            if inspector.has_table(table, schema=SCHEMA)
        }
        return engine, columns

    def _connection_finished(
        self,
        result: tuple[Engine, dict[str, list[str]]] | None,
        error: Exception | None,
    ) -> None:
        if error:
            self.status.set("Pripojenie zlyhalo")
            messagebox.showerror("Databáza", str(error), parent=self)
            return

        assert result is not None
        self.engine, self.columns = result
        missing = [table for table in TABLES if table not in self.columns]
        if missing:
            self.status.set("Chýbajú tabuľky")
            messagebox.showerror(
                "Databázová schéma",
                "Nenašli sa tabuľky: " + ", ".join(missing),
                parent=self,
            )
            return

        self.status.set(f"Pripojené • schéma {SCHEMA}")
        self._load_images()

    def _search_clause(self, table: str, alias: str | None = None) -> tuple[str, dict[str, str]]:
        term = self.search.get().strip()
        if not term:
            return "", {}

        candidates = [
            column
            for column in self.columns.get(table, [])
            if any(
                marker in column.lower()
                for marker in ("name", "path", "cmd", "type", "host", "url", "owner")
            )
        ][:20]
        if not candidates:
            return "", {}

        prefix = f"{identifier(alias)}." if alias else ""
        parts = [
            f"CAST({prefix}{identifier(column)} AS TEXT) ILIKE :search_term"
            for column in candidates
        ]
        return "WHERE (" + " OR ".join(parts) + ")", {
            "search_term": f"%{term}%"
        }

    def _load_images(self) -> None:
        self.current_table = "images"
        self.current_image_id = None
        self.current_process_id = None
        self.current_page = 0
        where_sql, params = self._search_clause("images", "img")
        sql = (
            f"SELECT img.*, "
            f"(SELECT COUNT(*) FROM {identifier(SCHEMA)}.{identifier('processes')} p "
            f"WHERE p.image_id = img.id) AS process_count "
            f"FROM {identifier(SCHEMA)}.{identifier('images')} img "
            f"{where_sql} ORDER BY img.id LIMIT :limit OFFSET :offset"
        )
        self._run_query(
            "images",
            sql,
            {**params, "limit": PAGE_SIZE, "offset": self.current_page * PAGE_SIZE},
            "Images — vyber obrázok",
        )

    def _image_selected(self, _event: tk.Event | None = None) -> None:
        selected = self.image_tree.selection()
        if not selected:
            return
        image_id = self.image_tree.item(selected[0], "values")[0]
        self.current_image_id = image_id
        self.current_process_id = None
        self.current_table = "processes"
        self.current_page = 0
        where_sql, params = self._search_clause("processes")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.{identifier('processes')} "
            f"WHERE image_id = :image_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} "
            f"ORDER BY id LIMIT :limit OFFSET :offset"
        )
        self._run_query(
            "processes",
            sql,
            {
                **params,
                "image_id": image_id,
                "limit": PAGE_SIZE,
                "offset": self.current_page * PAGE_SIZE,
            },
            f"Processes — image {image_id}",
        )

    def _process_selected(self, _event: tk.Event | None = None) -> None:
        if self.current_table != "processes":
            return
        selected = self.result_tree.selection()
        if not selected:
            return
        values = self.result_tree.item(selected[0], "values")
        columns = self.result_tree["columns"]
        if "id" not in columns:
            return
        process_id = values[list(columns).index("id")]
        self.current_process_id = process_id
        self.current_table = "events"
        self.current_page = 0
        where_sql, params = self._search_clause("events")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.{identifier('events')} "
            f"WHERE process_id = :process_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} "
            f"ORDER BY event_time DESC NULLS LAST, id "
            f"LIMIT :limit OFFSET :offset"
        )
        self._run_query(
            "events",
            sql,
            {
                **params,
                "process_id": process_id,
                "limit": PAGE_SIZE,
                "offset": self.current_page * PAGE_SIZE,
            },
            f"Events — process {process_id}",
        )

    def _run_query(
        self, table: str, sql: str, params: dict[str, Any], title: str
    ) -> None:
        if not self.engine:
            return
        self.query_generation += 1
        generation = self.query_generation
        self.loading = True
        self.current_title = title
        self.content_title.configure(text=f"{title} • načítavam…")
        self._clear_result_tree()
        self.page_info.set("Načítavam dáta…")
        self.prev_button.configure(state="disabled")
        self.next_button.configure(state="disabled")
        self._run_async(
            lambda: self._execute_query(sql, params),
            lambda rows, error: self._query_finished(
                table, rows, error, generation, title
            ),
        )

    def _execute_query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert self.engine is not None
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            result = connection.execute(text(sql), params)
            return [dict(row) for row in result.mappings()]

    def _query_finished(
        self,
        table: str,
        rows: list[dict[str, Any]] | None,
        error: Exception | None,
        generation: int,
        title: str,
    ) -> None:
        if generation != self.query_generation:
            return
        self.loading = False

        if error:
            self.status.set("Dotaz zlyhal")
            self.page_info.set("Chyba pri načítaní dát")
            messagebox.showerror("SQL dotaz", str(error), parent=self)
            return

        assert rows is not None
        self.current_table = table
        self.current_rows = rows
        self.content_title.configure(text=title)
        self._show_rows(table, rows)
        self._update_paging(len(rows))

    def _clear_result_tree(self) -> None:
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_tree["columns"] = ()

    def _show_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        if table == "images":
            self.image_tree.delete(*self.image_tree.get_children())
            for row in rows:
                image_id = row.get("id")
                name = row.get("original_name") or row.get("path") or "(bez názvu)"
                self.image_tree.insert(
                    "",
                    "end",
                    iid=f"image_{image_id}",
                    values=(image_id, name, row.get("process_count", 0)),
                )
            self._clear_result_tree()
            self.content_title.configure(text="Images — vyber obrázok")
            self.page_info.set(f"{len(rows)} obrázkov načítaných")
            return

        if not rows:
            self._clear_result_tree()
            self.page_info.set("Žiadne záznamy pre aktuálny výber")
            return

        # Keep all columns visible; long values can be inspected by horizontal scrolling.
        columns = list(rows[0].keys())
        self.result_tree.delete(*self.result_tree.get_children())
        self.result_tree["columns"] = columns

        for column in columns:
            self.result_tree.heading(column, text=column)
            self.result_tree.column(
                column,
                width=max(100, min(260, len(column) * 11)),
                minwidth=70,
                anchor="w",
                stretch=True,
            )

        for index, row in enumerate(rows):
            values = [value_text(row.get(column)) for column in columns]
            iid = f"{table}_{index}"
            self.result_tree.insert("", "end", iid=iid, values=values)

        if table == "processes":
            self.page_info.set(
                f"{len(rows)} procesov • dvojklikom alebo kliknutím vyber proces a zobraz eventy"
            )
        else:
            self.page_info.set(f"{len(rows)} eventov")

    def _update_paging(self, row_count: int) -> None:
        offset = self.current_page * PAGE_SIZE
        self.prev_button.configure(
            state="normal" if self.current_page > 0 else "disabled"
        )
        self.next_button.configure(
            state="normal" if row_count == PAGE_SIZE else "disabled"
        )
        self.page_info.set(
            f"Strana {self.current_page + 1} • zobrazených {row_count} záznamov"
        )

    def apply_search(self) -> None:
        self.current_page = 0
        self.refresh_current()

    def clear_search(self) -> None:
        self.search.set("")
        self.apply_search()

    def refresh_current(self) -> None:
        if self.current_table == "images":
            self._load_images()
        elif self.current_table == "processes" and self.current_image_id is not None:
            self._load_processes_for_current_image()
        elif self.current_table == "events" and self.current_process_id is not None:
            self._load_events_for_current_process()
        else:
            self._load_images()

    def _load_processes_for_current_image(self) -> None:
        image_id = self.current_image_id
        self.current_table = "processes"
        where_sql, params = self._search_clause("processes")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.{identifier('processes')} "
            f"WHERE image_id = :image_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} "
            f"ORDER BY id LIMIT :limit OFFSET :offset"
        )
        self._run_query(
            "processes",
            sql,
            {
                **params,
                "image_id": image_id,
                "limit": PAGE_SIZE,
                "offset": self.current_page * PAGE_SIZE,
            },
            f"Processes — image {image_id}",
        )

    def _load_events_for_current_process(self) -> None:
        process_id = self.current_process_id
        self.current_table = "events"
        where_sql, params = self._search_clause("events")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.{identifier('events')} "
            f"WHERE process_id = :process_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} "
            f"ORDER BY event_time DESC NULLS LAST, id "
            f"LIMIT :limit OFFSET :offset"
        )
        self._run_query(
            "events",
            sql,
            {
                **params,
                "process_id": process_id,
                "limit": PAGE_SIZE,
                "offset": self.current_page * PAGE_SIZE,
            },
            f"Events — process {process_id}",
        )

    def previous_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_current()

    def next_page(self) -> None:
        if len(self.current_rows) == PAGE_SIZE:
            self.current_page += 1
            self.refresh_current()

    def _run_async(
        self,
        operation: Callable[[], Any],
        callback: Callable[[Any, Exception | None], None],
    ) -> None:
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
