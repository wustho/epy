import dataclasses
import hashlib
import os
import sqlite3
from datetime import datetime
from typing import List, Tuple

from epy_reader.ebooks import Ebook
from epy_reader.models import AppData, LibraryItem, Optional, ReadingState


class State(AppData):
    """
    Use sqlite3 instead of JSON (in older version)
    to shift the weight from memory to process
    """

    def __init__(self):
        if not os.path.isfile(self.filepath):
            self.init_db()

    @property
    def filepath(self) -> str:
        return os.path.join(self.prefix, "states.db") if self.prefix else os.devnull

    def get_from_history(self) -> List[LibraryItem]:
        try:
            conn = sqlite3.connect(self.filepath)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT last_read, filepath, title, author, reading_progress
                FROM library ORDER BY last_read DESC
                """
            )
            results = cur.fetchall()
            library_items: List[LibraryItem] = []
            for result in results:
                library_items.append(
                    LibraryItem(
                        last_read=datetime.fromisoformat(result[0]),
                        filepath=result[1],
                        title=result[2],
                        author=result[3],
                        reading_progress=result[4],
                    )
                )
            return library_items
        finally:
            conn.close()

    def delete_from_library(self, filepath: str) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM reading_states WHERE filepath=?", (filepath,))
            conn.commit()
        finally:
            conn.close()

    def get_last_read(self) -> Optional[str]:
        library = self.get_from_history()
        return library[0].filepath if library else None

    def update_library(self, ebook: Ebook, reading_progress: Optional[float]) -> None:
        try:
            metadata = ebook.get_meta()
            conn = sqlite3.connect(self.filepath)
            conn.execute(
                """
                INSERT OR REPLACE INTO library (filepath, title, author, reading_progress)
                VALUES (?, ?, ?, ?)
                """,
                (ebook.path, metadata.title, metadata.creator, reading_progress),
            )
            conn.commit()
        finally:
            conn.close()

    def get_last_reading_state(self, ebook: Ebook) -> ReadingState:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM reading_states WHERE filepath=?", (ebook.path,))
            result = cur.fetchone()
            if result:
                result = dict(result)
                del result["filepath"]
                return ReadingState(**result, section=None)
            return ReadingState(content_index=0, textwidth=80, row=0, rel_pctg=None, section=None)
        finally:
            conn.close()

    def set_last_reading_state(self, ebook: Ebook, reading_state: ReadingState) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute(
                """
                INSERT OR REPLACE INTO reading_states
                VALUES (:filepath, :content_index, :textwidth, :row, :rel_pctg)
                """,
                {"filepath": ebook.path, **dataclasses.asdict(reading_state)},
            )
            conn.commit()
        finally:
            conn.close()

    def insert_bookmark(self, ebook: Ebook, name: str, reading_state: ReadingState) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute(
                """
                INSERT INTO bookmarks
                VALUES (:id, :filepath, :name, :content_index, :textwidth, :row, :rel_pctg)
                """,
                {
                    "id": hashlib.sha1(f"{ebook.path}{name}".encode()).hexdigest()[:10],
                    "filepath": ebook.path,
                    "name": name,
                    **dataclasses.asdict(reading_state),
                },
            )
            conn.commit()
        finally:
            conn.close()

    def delete_bookmark(self, ebook: Ebook, name: str) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute("DELETE FROM bookmarks WHERE filepath=? AND name=?", (ebook.path, name))
            conn.commit()
        finally:
            conn.close()

    def get_bookmarks(self, ebook: Ebook) -> List[Tuple[str, ReadingState]]:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM bookmarks WHERE filepath=?", (ebook.path,))
            results = cur.fetchall()
            bookmarks: List[Tuple[str, ReadingState]] = []
            for result in results:
                tmp_dict = dict(result)
                name = tmp_dict["name"]
                tmp_dict = {
                    k: v
                    for k, v in tmp_dict.items()
                    if k in ("content_index", "textwidth", "row", "rel_pctg")
                }
                bookmarks.append((name, ReadingState(**tmp_dict)))
            return bookmarks
        finally:
            conn.close()

    def init_db(self) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.executescript(
                """
                CREATE TABLE reading_states (
                    filepath TEXT PRIMARY KEY,
                    content_index INTEGER,
                    textwidth INTEGER,
                    row INTEGER,
                    rel_pctg REAL
                );

                CREATE TABLE library (
                    last_read DATETIME DEFAULT (datetime('now','localtime')),
                    filepath TEXT PRIMARY KEY,
                    title TEXT,
                    author TEXT,
                    reading_progress REAL,
                    FOREIGN KEY (filepath) REFERENCES reading_states(filepath)
                    ON DELETE CASCADE
                );

                CREATE TABLE bookmarks (
                    id TEXT PRIMARY KEY,
                    filepath TEXT,
                    name TEXT,
                    content_index INTEGER,
                    textwidth INTEGER,
                    row INTEGER,
                    rel_pctg REAL,
                    FOREIGN KEY (filepath) REFERENCES reading_states(filepath)
                    ON DELETE CASCADE
                );
                """
            )
            conn.commit()
        finally:
            conn.close()
