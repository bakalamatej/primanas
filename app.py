from __future__ import annotations

import os
import threading
from urllib.parse import quote_plus
from typing import Any, Callable

from dotenv import load_dotenv
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

load_dotenv()

SCHEMA = os.getenv("PGSCHEMA", "public")
TABLES = ("images", "processes", "events")
PAGE_SIZE = 100
BG = (0.055, 0.065, 0.08, 1)
PANEL = (0.09, 0.105, 0.13, 1)
ROW = (0.13, 0.15, 0.19, 1)
ROW_ACTIVE = (0.2, 0.35, 0.55, 1)
TEXT = (0.9, 0.92, 0.96, 1)
MUTED = (0.58, 0.63, 0.7, 1)


def database_engine() -> Engine:
    url = os.getenv("DATABASE_URL")
    if not url:
        host = os.getenv("PGHOST", "localhost")
        port = os.getenv("PGPORT", "5432")
        database = os.getenv("PGDATABASE")
        user = os.getenv("PGUSER")
        password = os.getenv("PGPASSWORD")
        if not all((database, user, password)):
            raise RuntimeError("Chýba PGDATABASE, PGUSER alebo PGPASSWORD v súbore .env.")
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
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=15000"},
    )


def identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def display(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    result = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    return result if len(result) <= limit else result[: limit - 1] + "…"


class Panel(BoxLayout):
    radius = NumericProperty(6)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*PANEL)
            self.background = RoundedRectangle(pos=self.pos, size=self.size, radius=[self.radius])
        self.bind(pos=self._update_background, size=self._update_background)

    def _update_background(self, *_args: Any) -> None:
        self.background.pos = self.pos
        self.background.size = self.size


class Explorer(BoxLayout):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(orientation="vertical", padding=dp(12), spacing=dp(8), **kwargs)
        self.engine: Engine | None = None
        self.columns: dict[str, list[str]] = {}
        self.current_table = "images"
        self.current_image_id: Any = None
        self.current_process_id: Any = None
        self.current_rows: list[dict[str, Any]] = []
        self.current_page = 0
        self.query_generation = 0
        self._search_event = None
        Window.clearcolor = BG
        self._build_ui()
        Clock.schedule_once(lambda _dt: self._start_connection(), 0)

    def _build_ui(self) -> None:
        header = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(12))
        header.add_widget(Label(text="PostgreSQL Data Explorer", color=TEXT,
                               font_size=dp(20), halign="left"))
        self.status = Label(text="Pripájam sa k databáze…", color=MUTED,
                            halign="left")
        header.add_widget(self.status)
        refresh = self._toolbar_button("Obnoviť", dp(110))
        refresh.bind(on_release=lambda _button: self.refresh_current())
        header.add_widget(refresh)
        self.add_widget(header)

        toolbar = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        toolbar.add_widget(Label(text="Filter:", size_hint_x=None, width=dp(55), color=TEXT))
        self.search = TextInput(multiline=False, hint_text="Názov, cesta, príkaz, typ…",
                                background_color=ROW, foreground_color=TEXT,
                                hint_text_color=MUTED, cursor_color=TEXT)
        self.search.bind(on_text_validate=lambda _input: self.apply_search())
        toolbar.add_widget(self.search)
        find = self._toolbar_button("Hľadať", dp(100))
        find.bind(on_release=lambda _button: self.apply_search())
        clear = self._toolbar_button("Vymazať", dp(100))
        clear.bind(on_release=lambda _button: self.clear_search())
        toolbar.add_widget(find)
        toolbar.add_widget(clear)
        self.add_widget(toolbar)

        content = BoxLayout(spacing=dp(10))
        left = Panel(orientation="vertical", padding=dp(8), spacing=dp(6))
        left.add_widget(Label(text="Images — aplikácie", size_hint_y=None, height=dp(32),
                              color=TEXT, bold=True))
        self.image_scroll, self.image_rows = self._scroll_panel()
        left.add_widget(self.image_scroll)
        content.add_widget(left)

        self.process_panel, self.process_scroll, self.process_rows = self._data_panel("Processes")
        self.event_panel, self.event_scroll, self.event_rows = self._data_panel("Events")
        content.add_widget(self.process_panel)
        content.add_widget(self.event_panel)
        self.add_widget(content)

    def _data_panel(self, heading: str) -> tuple[Panel, ScrollView, GridLayout]:
        panel = Panel(orientation="vertical", padding=dp(8), spacing=dp(6))
        panel.add_widget(Label(text=heading, size_hint_y=None, height=dp(32), color=TEXT, bold=True))
        scroll, rows = self._scroll_panel()
        panel.add_widget(scroll)
        return panel, scroll, rows

    def _scroll_panel(self) -> tuple[ScrollView, GridLayout]:
        rows = GridLayout(cols=1, spacing=dp(2), size_hint_y=None)
        rows.bind(minimum_height=rows.setter("height"))
        scroll = ScrollView(do_scroll_x=False)
        scroll.add_widget(rows)
        return scroll, rows

    def _toolbar_button(self, text: str, width: float) -> Button:
        return Button(
            text=text,
            size_hint_x=None,
            width=width,
            color=TEXT,
            background_color=ROW,
            background_normal="",
            background_down="",
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
            for table in TABLES if inspector.has_table(table, schema=SCHEMA)
        }
        return engine, columns

    def _connection_finished(self, result: Any, error: Exception | None) -> None:
        if error:
            self.status.text = "Pripojenie zlyhalo"
            self._show_error("Databáza", str(error))
            return
        assert result is not None
        self.engine, self.columns = result
        missing = [table for table in TABLES if table not in self.columns]
        if missing:
            self.status.text = "Chýbajú tabuľky"
            self._show_error("Databázová schéma", "Nenašli sa tabuľky: " + ", ".join(missing))
            return
        self.status.text = f"Pripojené • schéma {SCHEMA}"
        self._load_images()

    def _show_error(self, title: str, message: str) -> None:
        Popup(title=title, content=Label(text=message, color=TEXT),
              size_hint=(0.75, 0.35), background_color=PANEL).open()

    def _search_clause(self, table: str, alias: str | None = None) -> tuple[str, dict[str, str]]:
        term = self.search.text.strip()
        if not term:
            return "", {}
        candidates = [
            column for column in self.columns.get(table, [])
            if any(marker in column.lower() for marker in ("name", "path", "cmd", "type", "host", "url", "owner"))
        ][:20]
        if not candidates:
            return "", {}
        prefix = f"{identifier(alias)}." if alias else ""
        parts = [f"CAST({prefix}{identifier(column)} AS TEXT) ILIKE :search_term" for column in candidates]
        return "WHERE (" + " OR ".join(parts) + ")", {"search_term": f"%{term}%"}

    def _load_images(self) -> None:
        self.current_table, self.current_image_id, self.current_process_id = "images", None, None
        self._clear_rows(self.image_rows)
        self._clear_rows(self.process_rows)
        self._clear_rows(self.event_rows)
        self._set_panel_title(self.process_panel, "Processes — vyber image")
        self._set_panel_title(self.event_panel, "Events — vyber proces")
        where_sql, params = self._search_clause("images", "img")
        sql = (
            f"SELECT img.*, (SELECT COUNT(*) FROM {identifier(SCHEMA)}.processes p "
            f"WHERE p.image_id = img.id) AS process_count "
            f"FROM {identifier(SCHEMA)}.images img {where_sql} "
            "ORDER BY img.id LIMIT :limit OFFSET :offset"
        )
        self._run_query("images", sql, {**params, "limit": PAGE_SIZE, "offset": self.current_page * PAGE_SIZE},
                        "Images — vyber image")

    def _load_processes(self, image_id: Any) -> None:
        self.current_image_id, self.current_process_id, self.current_table, self.current_page = image_id, None, "processes", 0
        self._clear_rows(self.process_rows)
        self._clear_rows(self.event_rows)
        self._set_panel_title(self.process_panel, f"Processes — image {image_id}")
        self._set_panel_title(self.event_panel, "Events — vyber proces")
        where_sql, params = self._search_clause("processes")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.processes WHERE image_id = :image_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} ORDER BY id LIMIT :limit OFFSET :offset"
        )
        self._run_query("processes", sql, {**params, "image_id": image_id, "limit": PAGE_SIZE, "offset": 0},
                        f"Processes — image {image_id}")

    def _load_events(self, process_id: Any) -> None:
        self.current_process_id, self.current_table, self.current_page = process_id, "events", 0
        self._clear_rows(self.event_rows)
        self._set_panel_title(self.event_panel, f"Events — process {process_id}")
        where_sql, params = self._search_clause("events")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.events WHERE process_id = :process_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} ORDER BY event_time DESC NULLS LAST, id "
            "LIMIT :limit OFFSET :offset"
        )
        self._run_query("events", sql, {**params, "process_id": process_id, "limit": PAGE_SIZE, "offset": 0},
                        f"Events — process {process_id}")

    def _run_query(self, table: str, sql: str, params: dict[str, Any], title: str) -> None:
        if not self.engine:
            return
        self.query_generation += 1
        generation = self.query_generation
        if table == "images":
            self._clear_rows(self.image_rows)
        elif table == "processes":
            self._clear_rows(self.process_rows)
        else:
            self._clear_rows(self.event_rows)
        self._run_async(lambda: self._execute_query(sql, params),
                        lambda rows, error: self._query_finished(table, rows, error, generation, title))

    def _execute_query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert self.engine is not None
        with self.engine.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            return [dict(row) for row in connection.execute(text(sql), params).mappings()]

    def _query_finished(self, table: str, rows: Any, error: Exception | None, generation: int, title: str) -> None:
        if generation != self.query_generation:
            return
        if error:
            self.status.text = "Chyba pri načítaní dát"
            self._show_error("SQL dotaz", str(error))
            return
        self.current_rows = rows
        self._show_rows(table, rows)

    def _show_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        target = {"images": self.image_rows, "processes": self.process_rows, "events": self.event_rows}[table]
        self._clear_rows(target)
        if not rows:
            target.add_widget(self._row_label("Žiadne záznamy pre aktuálny výber."))
            return
        if table == "images":
            for row in rows:
                image_id = row.get("id")
                name = row.get("original_name") or row.get("path") or "(bez názvu)"
                button = self._row_button(
                    f"Image {image_id}  •  {display(name, 70)}  •  {row.get('process_count', 0)} procesov"
                )
                button.bind(on_release=lambda _button, value=image_id: self._load_processes(value))
                target.add_widget(button)
        elif table == "processes":
            for row in rows:
                process_id = row.get("id")
                button = self._row_button(
                    f"Process {process_id}  •  {display(row.get('name') or row.get('cmd'))}"
                )
                button.bind(on_release=lambda _button, value=process_id: self._load_events(value))
                target.add_widget(button)
        else:
            for row in rows:
                target.add_widget(self._row_label(
                    text=f"Event {row.get('id')}  •  {display(row.get('event_type'))}  •  {display(row.get('event_time'))}",
                ))

    def _clear_rows(self, rows: GridLayout) -> None:
        rows.clear_widgets()

    def _row_button(self, label: str) -> Button:
        return Button(text=label, size_hint_y=None, height=dp(38), halign="left",
                      valign="middle", color=TEXT, background_color=ROW,
                      background_normal="", background_down="", padding=(dp(10), 0))

    def _row_label(self, text: str) -> Label:
        return Label(text=text, size_hint_y=None, height=dp(36), color=TEXT,
                     halign="left", valign="middle")

    def _set_panel_title(self, panel: Panel, title: str) -> None:
        label = panel.children[-1] if panel.children else None
        if isinstance(label, Label):
            label.text = title

    def apply_search(self) -> None:
        self.current_page = 0
        self.refresh_current()

    def clear_search(self) -> None:
        self.search.text = ""
        self.apply_search()

    def refresh_current(self) -> None:
        if self.current_table == "images":
            self._load_images()
        elif self.current_table == "processes" and self.current_image_id is not None:
            self._load_processes(self.current_image_id)
        elif self.current_process_id is not None:
            self._load_events(self.current_process_id)

    def _run_async(self, operation: Callable[[], Any], callback: Callable[[Any, Exception | None], None]) -> None:
        def worker() -> None:
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, exc
            Clock.schedule_once(lambda _dt: callback(result, error), 0)
        threading.Thread(target=worker, daemon=True).start()


class ExplorerApp(App):
    def build(self) -> Explorer:
        Window.size = (1450, 850)
        Window.minimum_width = 950
        Window.minimum_height = 600
        return Explorer()


if __name__ == "__main__":
    ExplorerApp().run()
