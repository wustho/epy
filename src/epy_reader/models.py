from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import os
from typing import Optional, Tuple, Mapping, Union, Any


class Direction(Enum):
    FORWARD = "forward"
    BACKWARD = "backward"


@dataclass(frozen=True)
class BookMetadata:
    title: Optional[str] = None
    creator: Optional[str] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    language: Optional[str] = None
    format: Optional[str] = None
    identifier: Optional[str] = None
    source: Optional[str] = None


@dataclass(frozen=True)
class LibraryItem:
    last_read: datetime
    filepath: str
    title: Optional[str] = None
    author: Optional[str] = None
    reading_progress: Optional[float] = None

    def __str__(self) -> str:
        if self.reading_progress is None:
            reading_progress_str = "N/A"
        else:
            reading_progress_str = f"{int(self.reading_progress * 100)}%"
        reading_progress_str = reading_progress_str.rjust(4)

        book_name: str
        filename = self.filepath.replace(os.path.expanduser("~"), "~", 1)
        if self.title is not None and self.author is not None:
            book_name = f"{self.title} - {self.author} ({filename})"
        elif self.title is None and self.author:
            book_name = f"{filename} - {self.author}"
        else:
            book_name = filename

        last_read_str = self.last_read.strftime("%I:%M%p %b %d")

        return f"{reading_progress_str} {last_read_str}: {book_name}"


@dataclass(frozen=True)
class ReadingState:
    """
    Data model for reading state.

    `row` has to be explicitly assigned with value
    because Seamless feature needs it to adjust from
    relative (to book's content index) row to absolute
    (to book's entire content) row.

    `rel_pctg` and `section` default to None and if
    either of them is assigned with value, then it
    will be overriding the `row` value.
    """

    content_index: int
    textwidth: int
    row: int
    rel_pctg: Optional[float] = None
    section: Optional[str] = None


@dataclass(frozen=True)
class SearchData:
    direction: Direction = Direction.FORWARD
    value: str = ""


@dataclass(frozen=True)
class LettersCount:
    """
    all: total letters in book
    cumulative: list of total letters for previous contents
                eg. let's say cumulative = (0, 50, 89, ...) it means
                    0  is total cumulative letters of book contents[-1] to contents[0]
                    50 is total cumulative letters of book contents[0] to contents[1]
                    89 is total cumulative letters of book contents[0] to contents[2]
    """

    all: int
    cumulative: Tuple[int, ...]


@dataclass(frozen=True)
class CharPos:
    """
    Describes character position in text.
    eg. ["Lorem ipsum dolor sit amet,",  # row=0
         "consectetur adipiscing elit."]  # row=1
             ^CharPos(row=1, col=3)
    """

    row: int
    col: int


@dataclass(frozen=True)
class TextMark:
    """
    Describes marking in text.
    eg. Interval [CharPos(row=0, col=3), CharPos(row=1, col=4)]
    notice the marking inclusive [] for both side instead of right exclusive [)
    """

    start: CharPos
    end: Optional[CharPos] = None

    def is_valid(self) -> bool:
        """
        Assert validity and check if the mark is unterminated
        eg. <div><i>This is italic text</div>
        Missing </i> tag
        """
        if self.end is not None:
            if self.start.row == self.end.row:
                return self.start.col <= self.end.col
            else:
                return self.start.row < self.end.row

        return False


@dataclass(frozen=True)
class TextSpan:
    """
    Like TextMark but using span of letters (n_letters)
    """

    start: CharPos
    n_letters: int


@dataclass(frozen=True)
class InlineStyle:
    """
    eg. InlineStyle(attr=curses.A_BOLD, row=3, cols=4, n_letters=3)
    """

    row: int
    col: int
    n_letters: int
    attr: int


@dataclass(frozen=True)
class TocEntry:
    label: str
    content_index: int
    section: Optional[str]


@dataclass(frozen=True)
class TextStructure:
    """
    Object that describes how the text
    should be displayed in screen.

    text_lines: ("list of lines", "of text", ...)
    image_maps: {line_num: path/to/image/in/ebook/zip}
    section_rows: {section_id: line_num}
    formatting: (InlineStyle, ...)
    """

    text_lines: Tuple[str, ...]
    image_maps: Mapping[int, str]
    section_rows: Mapping[str, int]
    formatting: Tuple[InlineStyle, ...]


@dataclass(frozen=True)
class NoUpdate:
    pass


class Key:
    """
    Because ord("k") chr(34) are confusing
    """

    def __init__(self, char_or_int: Union[str, int]):
        self.value: int = char_or_int if isinstance(char_or_int, int) else ord(char_or_int)
        self.char: str = char_or_int if isinstance(char_or_int, str) else chr(char_or_int)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Key):
            return self.value == other.value
        return False

    def __ne__(self, other: Any) -> bool:
        return self.__eq__(other)

    def __hash__(self) -> int:
        return hash(self.value)


class AppData:
    @property
    def prefix(self) -> Optional[str]:
        """Return None if there exists no homedir | userdir"""
        prefix: Optional[str] = None

        # UNIX filesystem
        homedir = os.getenv("HOME")
        # WIN filesystem
        userdir = os.getenv("USERPROFILE")

        if homedir:
            if os.path.isdir(os.path.join(homedir, ".config")):
                prefix = os.path.join(homedir, ".config", "epy")
            else:
                prefix = os.path.join(homedir, ".epy")
        elif userdir:
            prefix = os.path.join(userdir, ".epy")

        if prefix:
            os.makedirs(prefix, exist_ok=True)

        return prefix
