from __future__ import annotations

import os
import queue
import threading
from urllib.parse import quote_plus
from typing import Any, Callable

from dotenv import load_dotenv
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
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


def display(value: Any, limit: int = 240) -> str:
    if value is None:
        return ""
    result = " ".join(str(value).replace("\r", " ").replace("\n", " ").split())
    return result if len(result) <= limit else result[: limit - 1] + "…"


class Worker(QObject):
    finished = Signal(object, object)

    def __init__(self, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.operation = operation

    def run(self) -> None:
        try:
            self.finished.emit(self.operation(), None)
        except Exception as error:
            self.finished.emit(None, error)


class Explorer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PostgreSQL Data Explorer")
        self.resize(1450, 850)
        self.setMinimumSize(950, 600)
        self.engine: Engine | None = None
        self.columns: dict[str, list[str]] = {}
        self.current_table = "images"
        self.current_image_id: Any = None
        self.current_process_id: Any = None
        self.current_rows: list[dict[str, Any]] = []
        self.current_page = 0
        self.query_generation = 0
        self.active_threads: list[QThread] = []
        self._build_ui()
        QTimer.singleShot(0, self._start_connection)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        header = QHBoxLayout()
        title = QLabel("PostgreSQL Data Explorer")
        title.setObjectName("appTitle")
        self.status = QLabel("Pripájam sa k databáze…")
        self.refresh_button = QPushButton("Obnoviť")
        self.refresh_button.clicked.connect(self.refresh_current)
        header.addWidget(title)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(self.refresh_button)
        root.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Filter:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Hľadať v názve, ceste, príkaze, type…")
        self.search.returnPressed.connect(self.apply_search)
        self.search.textChanged.connect(self._search_changed)
        toolbar.addWidget(self.search)
        search_button = QPushButton("Hľadať")
        search_button.clicked.connect(self.apply_search)
        clear_button = QPushButton("Vymazať filter")
        clear_button.clicked.connect(self.clear_search)
        toolbar.addWidget(search_button)
        toolbar.addWidget(clear_button)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Images — aplikácie"))
        self.image_table = QTableWidget()
        self.image_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.image_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.image_table.itemSelectionChanged.connect(self._image_selected)
        left_layout.addWidget(self.image_table)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.content_title = QLabel("Vyber image")
        self.content_title.setObjectName("contentTitle")
        right_layout.addWidget(self.content_title)
        self.result_table = QTableWidget()
        self.result_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.result_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.result_table.itemSelectionChanged.connect(self._process_selected)
        right_layout.addWidget(self.result_table)
        self.page_info = QLabel("")
        right_layout.addWidget(self.page_info)
        splitter.addWidget(right)
        splitter.setSizes([360, 1050])
        root.addWidget(splitter, 1)

        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f3f5f7; color: #202124; }
            #appTitle { font-size: 18px; font-weight: bold; }
            #contentTitle { font-size: 14px; font-weight: bold; padding: 5px; }
            QLineEdit, QTableWidget { background: white; color: #202124; }
            QTableWidget { gridline-color: #d5dbe1; selection-background-color: #cfe4ff;
                           selection-color: #172b4d; }
            QHeaderView::section { background: #e9edf2; color: #202124;
                                   font-weight: bold; padding: 6px; }
            QPushButton { padding: 6px 12px; }
            """
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

    def _connection_finished(self, result: Any, error: Exception | None) -> None:
        if error:
            self.status.setText("Pripojenie zlyhalo")
            QMessageBox.critical(self, "Databáza", str(error))
            return
        assert result is not None
        self.engine, self.columns = result
        missing = [table for table in TABLES if table not in self.columns]
        if missing:
            QMessageBox.critical(self, "Databázová schéma", "Nenašli sa tabuľky: " + ", ".join(missing))
            return
        self.status.setText(f"Pripojené • schéma {SCHEMA}")
        self._load_images()

    def _search_clause(self, table: str, alias: str | None = None) -> tuple[str, dict[str, str]]:
        term = self.search.text().strip()
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
        where_sql, params = self._search_clause("images", "img")
        sql = (
            f"SELECT img.*, (SELECT COUNT(*) FROM {identifier(SCHEMA)}.processes p "
            f"WHERE p.image_id = img.id) AS process_count "
            f"FROM {identifier(SCHEMA)}.images img {where_sql} "
            "ORDER BY img.id LIMIT :limit OFFSET :offset"
        )
        self._run_query("images", sql, {**params, "limit": PAGE_SIZE, "offset": self.current_page * PAGE_SIZE},
                        "Images — vyber image")

    def _image_selected(self) -> None:
        selected = self.image_table.selectedItems()
        if not selected:
            return
        image_id = self.image_table.item(selected[0].row(), 0).text()
        self.current_image_id, self.current_process_id, self.current_table, self.current_page = image_id, None, "processes", 0
        self._load_processes_for_current_image()

    def _load_processes_for_current_image(self) -> None:
        where_sql, params = self._search_clause("processes")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.processes WHERE image_id = :image_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} ORDER BY id LIMIT :limit OFFSET :offset"
        )
        self._run_query("processes", sql, {**params, "image_id": self.current_image_id, "limit": PAGE_SIZE,
                                           "offset": self.current_page * PAGE_SIZE},
                        f"Processes — image {self.current_image_id}")

    def _process_selected(self) -> None:
        if self.current_table != "processes":
            return
        selected = self.result_table.selectedItems()
        if not selected:
            return
        process_id = self.result_table.item(selected[0].row(), 0).text()
        self.current_process_id, self.current_table, self.current_page = process_id, "events", 0
        self._load_events_for_current_process()

    def _load_events_for_current_process(self) -> None:
        where_sql, params = self._search_clause("events")
        sql = (
            f"SELECT * FROM {identifier(SCHEMA)}.events WHERE process_id = :process_id "
            f"{where_sql.replace('WHERE ', 'AND ', 1)} ORDER BY event_time DESC NULLS LAST, id "
            "LIMIT :limit OFFSET :offset"
        )
        self._run_query("events", sql, {**params, "process_id": self.current_process_id,
                                        "limit": PAGE_SIZE, "offset": self.current_page * PAGE_SIZE},
                        f"Events — process {self.current_process_id}")

    def _run_query(self, table: str, sql: str, params: dict[str, Any], title: str) -> None:
        if not self.engine:
            return
        self.query_generation += 1
        generation = self.query_generation
        self.content_title.setText(title + " • načítavam…")
        self.result_table.clear()
        self.result_table.setRowCount(0)
        self.page_info.setText("Načítavam dáta…")
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
            self.page_info.setText("Chyba pri načítaní dát")
            QMessageBox.critical(self, "SQL dotaz", str(error))
            return
        self.current_rows = rows
        self.content_title.setText(title)
        self._show_rows(table, rows)
        self.page_info.setText(f"{len(rows)} záznamov • strana {self.current_page + 1}")

    def _show_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        target = self.image_table if table == "images" else self.result_table
        target.blockSignals(True)
        target.clear()
        if not rows:
            target.setColumnCount(1)
            target.setRowCount(1)
            target.setHorizontalHeaderLabels(["Výsledok"])
            target.setItem(0, 0, QTableWidgetItem("Žiadne záznamy pre aktuálny výber."))
            target.blockSignals(False)
            return
        target.setRowCount(len(rows))
        columns = list(rows[0])
        if table == "images":
            columns = ["id", "name", "process_count"]
        target.setColumnCount(len(columns))
        target.setHorizontalHeaderLabels(columns)
        for row_index, row in enumerate(rows):
            for column_index, column in enumerate(columns):
                value = row.get(column)
                if table == "images" and column == "name":
                    value = row.get("original_name") or row.get("path") or "(bez názvu)"
                item = QTableWidgetItem(display(value))
                target.setItem(row_index, column_index, item)
        target.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        target.horizontalHeader().setStretchLastSection(True)
        target.resizeColumnsToContents()
        target.blockSignals(False)

    def apply_search(self) -> None:
        self.current_page = 0
        self.refresh_current()

    def clear_search(self) -> None:
        self.search.clear()
        self.apply_search()

    def _search_changed(self) -> None:
        pass

    def refresh_current(self) -> None:
        if self.current_table == "images":
            self._load_images()
        elif self.current_table == "processes":
            self._load_processes_for_current_image()
        else:
            self._load_events_for_current_process()

    def previous_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self.refresh_current()

    def next_page(self) -> None:
        if len(self.current_rows) == PAGE_SIZE:
            self.current_page += 1
            self.refresh_current()

    def _run_async(self, operation: Callable[[], Any], callback: Callable[[Any, Exception | None], None]) -> None:
        thread = QThread(self)
        worker = Worker(operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(callback)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self.active_threads.remove(thread) if thread in self.active_threads else None)
        self.active_threads.append(thread)
        thread.start()


if __name__ == "__main__":
    app = QApplication([])
    window = Explorer()
    window.show()
    app.exec()
