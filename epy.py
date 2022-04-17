#!/usr/bin/env python3
# vim:tabstop=4:shiftwidth=4:softtabstop=4:smarttab:expandtab:foldmethod=marker
"""\
usage: epy [-h] [-r] [-d] [-v] [PATH | # | PATTERN | URL]

Read ebook in terminal

positional arguments:
  [ PATH | # | PATTERN | URL ]
                        ebook path, history number, pattern or URL

optional arguments:
  -h, --help            show this help message and exit
  -r, --history         print reading history
  -d, --dump            dump the content of ebook
  -v, --version         print version and exit

examples:
  epy /path/to/ebook   read /path/to/ebook file
  epy 3                read #3 file from reading history
  epy count monte      read file matching 'count monte'
                       from reading history
"""


__version__ = "2022.4.18"
__license__ = "GPL-3.0"
__author__ = "Benawi Adha"
__email__ = "benawiadha@gmail.com"
__url__ = "https://github.com/wustho/epy"


# Imports {{{

import argparse
import base64
import contextlib
import curses
import dataclasses
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib

from typing import Optional, Union, Sequence, Tuple, List, Dict, Mapping, Set, Type, Any
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher as SM
from enum import Enum
from functools import wraps
from html import unescape
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

try:
    from epy_extras import unpackBook  # type: ignore

    MOBI_SUPPORT = True
except ModuleNotFoundError:
    MOBI_SUPPORT = False

# }}}


# Debug Utils {{{

try:
    # Debug swith
    # $ DEBUG=1 ./epy.py

    DEBUG = int(str(os.getenv("DEBUG"))) == 1
    STDSCR = None

    def debug(context: int = 5) -> None:
        # if not isinstance(STDSCR, curses.window):
        #     raise RuntimeError("STDSCR not set")
        if STDSCR:
            curses.nocbreak()
            STDSCR.keypad(False)  # type: ignore
            curses.echo()
            curses.endwin()

        try:
            import ipdb  # type: ignore

            ipdb.set_trace(context=context)
        except ModuleNotFoundError:
            breakpoint()

except ValueError:
    DEBUG = False

# }}}


# Data Models {{{

# add image viewers here
# sorted by most widely used
VIEWER_PRESET_LIST = (
    "feh",
    "imv",
    "gio",
    "gnome-open",
    "gvfs-open",
    "xdg-open",
    "kde-open",
    "firefox",
)

DICT_PRESET_LIST = (
    "wkdict",
    "sdcv",
    "dict",
)


class Direction(Enum):
    FORWARD = "forward"
    BACKWARD = "backward"


class DoubleSpreadPadding(Enum):
    LEFT = 10
    MIDDLE = 7
    RIGHT = 10


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


@dataclass(frozen=True)
class Settings:
    DefaultViewer: str = "auto"
    DictionaryClient: str = "auto"
    ShowProgressIndicator: bool = True
    PageScrollAnimation: bool = True
    MouseSupport: bool = False
    StartWithDoubleSpread: bool = False
    # -1 is default terminal fg/bg colors
    DefaultColorFG: int = -1
    DefaultColorBG: int = -1
    DarkColorFG: int = 252
    DarkColorBG: int = 235
    LightColorFG: int = 238
    LightColorBG: int = 253
    SeamlessBetweenChapters: bool = False
    PreferredTTSEngine: Optional[str] = None
    TTSEngineArgs: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CfgDefaultKeymaps:
    ScrollUp: str = "k"
    ScrollDown: str = "j"
    PageUp: str = "h"
    PageDown: str = "l"
    # HalfScreenUp: str = "h"
    # HalfScreenDown: str
    NextChapter: str = "L"
    PrevChapter: str = "H"
    BeginningOfCh: str = "g"
    EndOfCh: str = "G"
    Shrink: str = "-"
    Enlarge: str = "+"
    SetWidth: str = "="
    Metadata: str = "M"
    DefineWord: str = "d"
    TableOfContents: str = "t"
    Follow: str = "f"
    OpenImage: str = "o"
    RegexSearch: str = "/"
    ShowHideProgress: str = "s"
    MarkPosition: str = "m"
    JumpToPosition: str = "`"
    AddBookmark: str = "b"
    ShowBookmarks: str = "B"
    Quit: str = "q"
    Help: str = "?"
    SwitchColor: str = "c"
    TTSToggle: str = "!"
    DoubleSpreadToggle: str = "D"
    Library: str = "R"


@dataclass(frozen=True)
class CfgBuiltinKeymaps:
    ScrollUp: Tuple[int, ...] = (curses.KEY_UP,)
    ScrollDown: Tuple[int, ...] = (curses.KEY_DOWN,)
    PageUp: Tuple[int, ...] = (curses.KEY_PPAGE, curses.KEY_LEFT)
    PageDown: Tuple[int, ...] = (curses.KEY_NPAGE, ord(" "), curses.KEY_RIGHT)
    BeginningOfCh: Tuple[int, ...] = (curses.KEY_HOME,)
    EndOfCh: Tuple[int, ...] = (curses.KEY_END,)
    TableOfContents: Tuple[int, ...] = (9, ord("\t"))
    Follow: Tuple[int, ...] = (10,)
    Quit: Tuple[int, ...] = (3, 27, 304)


@dataclass(frozen=True)
class Keymap:
    # HalfScreenDown: Tuple[Key, ...]
    # HalfScreenUp: Tuple[Key, ...]
    AddBookmark: Tuple[Key, ...]
    BeginningOfCh: Tuple[Key, ...]
    DefineWord: Tuple[Key, ...]
    DoubleSpreadToggle: Tuple[Key, ...]
    EndOfCh: Tuple[Key, ...]
    Enlarge: Tuple[Key, ...]
    Follow: Tuple[Key, ...]
    Help: Tuple[Key, ...]
    JumpToPosition: Tuple[Key, ...]
    Library: Tuple[Key, ...]
    MarkPosition: Tuple[Key, ...]
    Metadata: Tuple[Key, ...]
    NextChapter: Tuple[Key, ...]
    OpenImage: Tuple[Key, ...]
    PageDown: Tuple[Key, ...]
    PageUp: Tuple[Key, ...]
    PrevChapter: Tuple[Key, ...]
    Quit: Tuple[Key, ...]
    RegexSearch: Tuple[Key, ...]
    ScrollDown: Tuple[Key, ...]
    ScrollUp: Tuple[Key, ...]
    SetWidth: Tuple[Key, ...]
    ShowBookmarks: Tuple[Key, ...]
    ShowHideProgress: Tuple[Key, ...]
    Shrink: Tuple[Key, ...]
    SwitchColor: Tuple[Key, ...]
    TTSToggle: Tuple[Key, ...]
    TableOfContents: Tuple[Key, ...]


# }}}


# Speaker / TTS Engine Wrappers {{{


class SpeakerBaseModel:
    cmd: str = "tts_engine_binary"
    available: bool = False

    def __init__(self, args: List[str] = []):
        self.args = args

    def speak(self, text: str) -> None:
        raise NotImplementedError("Speaker.speak() not implemented")

    def is_done(self) -> bool:
        raise NotImplementedError("Speaker.is_done() not implemented")

    def stop(self) -> None:
        raise NotImplementedError("Speaker.stop() not implemented")

    def cleanup(self) -> None:
        raise NotImplementedError("Speaker.cleanup() not implemented")


class SpeakerMimic(SpeakerBaseModel):
    cmd = "mimic"
    available = bool(shutil.which("mimic"))

    def speak(self, text: str) -> None:
        self.process = subprocess.Popen(
            [self.cmd, *self.args],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        assert self.process.stdin
        self.process.stdin.write(text)
        self.process.stdin.close()

    def is_done(self) -> bool:
        return self.process.poll() is not None

    def stop(self) -> None:
        self.process.terminate()
        # self.process.kill()

    def cleanup(self) -> None:
        pass


class SpeakerPico(SpeakerBaseModel):
    cmd = "pico2wave"
    available = all([shutil.which(dep) for dep in ["pico2wave", "play"]])

    def speak(self, text: str) -> None:
        _, self.tmp_path = tempfile.mkstemp(suffix=".wav")

        try:
            subprocess.run(
                [self.cmd, *self.args, "-w", self.tmp_path, text],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            if "invalid pointer" not in e.output:
                sys.exit(e.output)

        self.process = subprocess.Popen(
            ["play", self.tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def is_done(self) -> bool:
        return self.process.poll() is not None

    def stop(self) -> None:
        self.process.terminate()
        # self.process.kill()

    def cleanup(self) -> None:
        os.remove(self.tmp_path)


# register wrappers here
SPEAKERS: List[Type[SpeakerBaseModel]] = [SpeakerMimic, SpeakerPico]

# }}}


# Ebooks {{{


class Ebook:
    def __init__(self, fileepub: str):
        raise NotImplementedError("Ebook.__init__() not implemented")

    @property
    def path(self) -> str:
        return self._path

    @path.setter
    def path(self, value: str) -> None:
        self._path = value

    @property
    def contents(self) -> Union[Tuple[str, ...], Tuple[ET.Element, ...]]:
        return self._contents

    @contents.setter
    def contents(self, value: Union[Tuple[str, ...], Tuple[ET.Element, ...]]) -> None:
        self._contents = value

    @property
    def toc_entries(self) -> Tuple[TocEntry, ...]:
        return self._toc_entries

    @toc_entries.setter
    def toc_entries(self, value: Tuple[TocEntry, ...]) -> None:
        self._toc_entries = value

    def get_meta(self) -> BookMetadata:
        raise NotImplementedError("Ebook.get_meta() not implemented")

    def initialize(self) -> None:
        raise NotImplementedError("Ebook.initialize() not implemented")

    def get_raw_text(self, content: Union[str, ET.Element]) -> str:
        raise NotImplementedError("Ebook.get_raw_text() not implemented")

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        raise NotImplementedError("Ebook.get_img_bytestr() not implemented")

    def cleanup(self) -> None:
        raise NotImplementedError("Ebook.cleanup() not implemented")


class Epub(Ebook):
    NAMESPACE = {
        "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
        "OPF": "http://www.idpf.org/2007/opf",
        "CONT": "urn:oasis:names:tc:opendocument:xmlns:container",
        "XHTML": "http://www.w3.org/1999/xhtml",
        "EPUB": "http://www.idpf.org/2007/ops",
        # Dublin Core
        "DC": "http://purl.org/dc/elements/1.1/",
    }

    def __init__(self, fileepub: str):
        self.path: str = os.path.abspath(fileepub)
        self.file: Union[zipfile.ZipFile, str] = zipfile.ZipFile(fileepub, "r")

        # populate these attributes
        # by calling self.initialize()
        self.root_filepath: str
        self.root_dirpath: str

    def get_meta(self) -> BookMetadata:
        assert isinstance(self.file, zipfile.ZipFile)
        # why self.file.read(self.root_filepath) problematic
        # content_opf = ET.fromstring(self.file.open(self.root_filepath).read())
        content_opf = ET.parse(self.file.open(self.root_filepath))
        return Epub._get_metadata(content_opf)

    @staticmethod
    def _get_metadata(content_opf: ET.ElementTree) -> BookMetadata:
        metadata: Dict[str, Optional[str]] = {}
        for field in dataclasses.fields(BookMetadata):
            element = content_opf.find(f".//DC:{field.name}", Epub.NAMESPACE)
            if element is not None:
                metadata[field.name] = element.text

        return BookMetadata(**metadata)

    @staticmethod
    def _get_contents(content_opf: ET.ElementTree) -> Tuple[str, ...]:
        # cont = ET.parse(self.file.open(self.root_filepath)).getroot()
        manifests: List[Tuple[str, str]] = []
        for manifest_elem in content_opf.findall("OPF:manifest/*", Epub.NAMESPACE):
            # EPUB3
            # if manifest_elem.get("id") != "ncx" and manifest_elem.get("properties") != "nav":
            if (
                manifest_elem.get("media-type") != "application/x-dtbncx+xml"
                and manifest_elem.get("properties") != "nav"
            ):
                manifest_id = manifest_elem.get("id")
                assert manifest_id is not None
                manifest_href = manifest_elem.get("href")
                assert manifest_href is not None
                manifests.append((manifest_id, manifest_href))

        spines: List[str] = []
        contents: List[str] = []
        for spine_elem in content_opf.findall("OPF:spine/*", Epub.NAMESPACE):
            idref = spine_elem.get("idref")
            assert idref is not None
            spines.append(idref)
        for spine in spines:
            for manifest in manifests:
                if spine == manifest[0]:
                    # book_contents.append(root_dirpath + unquote(manifest[1]))
                    contents.append(unquote(manifest[1]))
                    manifests.remove(manifest)
                    # TODO: test is break necessary
                    break

        return tuple(contents)

    @staticmethod
    def _get_tocs(toc: ET.Element, version: str, contents: Sequence[str]) -> Tuple[TocEntry, ...]:
        try:
            # EPUB3
            if version in {"1.0", "2.0"}:
                navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", Epub.NAMESPACE)
            elif version == "3.0":
                navPoints = toc.findall(
                    "XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a", Epub.NAMESPACE
                )

            toc_entries: List[TocEntry] = []
            for navPoint in navPoints:
                if version in {"1.0", "2.0"}:
                    src_elem = navPoint.find("DAISY:content", Epub.NAMESPACE)
                    assert src_elem is not None
                    src = src_elem.get("src")

                    name_elem = navPoint.find("DAISY:navLabel/DAISY:text", Epub.NAMESPACE)
                    assert name_elem is not None
                    name = name_elem.text
                elif version == "3.0":
                    src_elem = navPoint
                    assert src_elem is not None
                    src = src_elem.get("href")

                    name = "".join(list(navPoint.itertext()))

                assert src is not None
                src_id = src.split("#")

                try:
                    idx = contents.index(unquote(src_id[0]))
                except ValueError:
                    continue

                # assert name is not None
                # NOTE: skip empty label
                if name is not None:
                    toc_entries.append(
                        TocEntry(
                            label=name,
                            content_index=idx,
                            section=src_id[1] if len(src_id) == 2 else None,
                        )
                    )
        except AttributeError as e:
            if DEBUG:
                raise e

        return tuple(toc_entries)

    def initialize(self) -> None:
        assert isinstance(self.file, zipfile.ZipFile)

        container = ET.parse(self.file.open("META-INF/container.xml"))
        rootfile_elem = container.find("CONT:rootfiles/CONT:rootfile", Epub.NAMESPACE)
        assert rootfile_elem is not None
        self.root_filepath = rootfile_elem.attrib["full-path"]
        self.root_dirpath = (
            os.path.dirname(self.root_filepath) + "/"
            if os.path.dirname(self.root_filepath) != ""
            else ""
        )

        content_opf = ET.parse(self.file.open(self.root_filepath))
        version = content_opf.getroot().get("version")

        contents = Epub._get_contents(content_opf)
        self.contents = tuple(urljoin(self.root_dirpath, content) for content in contents)

        if version in {"1.0", "2.0"}:
            # "OPF:manifest/*[@id='ncx']"
            relative_toc = content_opf.find(
                "OPF:manifest/*[@media-type='application/x-dtbncx+xml']", Epub.NAMESPACE
            )
        elif version == "3.0":
            relative_toc = content_opf.find("OPF:manifest/*[@properties='nav']", Epub.NAMESPACE)
        else:
            raise RuntimeError(f"Unsupported Epub version: {version}")
        assert relative_toc is not None
        relative_toc_path = relative_toc.get("href")
        assert relative_toc_path is not None
        toc_path = self.root_dirpath + relative_toc_path
        toc = ET.parse(self.file.open(toc_path)).getroot()
        self.toc_entries = Epub._get_tocs(toc, version, contents)  # *self.contents (absolute path)

    def get_raw_text(self, content_path: Union[str, ET.Element]) -> str:
        assert isinstance(self.file, zipfile.ZipFile)
        assert isinstance(content_path, str)

        max_tries: Optional[int] = None  # 1 if DEBUG else None

        # use try-except block to catch
        # zlib.error: Error -3 while decompressing data: invalid distance too far back
        # seems like caused by multiprocessing
        tries = 0
        while True:
            try:
                content = self.file.open(content_path).read()
                break
            except zlib.error as e:
                tries += 1
                if max_tries is not None and tries >= max_tries:
                    raise e

        return content.decode("utf-8")

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        assert isinstance(self.file, zipfile.ZipFile)
        return impath, self.file.read(impath)

    def cleanup(self) -> None:
        pass


class Mobi(Epub):
    def __init__(self, filemobi: str):
        self.path = os.path.abspath(filemobi)
        self.file = tempfile.mkdtemp(prefix="epy-")

        # populate these attribute
        # by calling self.initialize()
        self.root_filepath: str
        self.root_dirpath: str

    def get_meta(self) -> BookMetadata:
        # why self.file.read(self.root_filepath) problematic
        with open(os.path.join(self.root_dirpath, "content.opf")) as f:
            content_opf = ET.parse(f)  # .getroot()
        return Epub._get_metadata(content_opf)

    def initialize(self) -> None:
        assert isinstance(self.file, str)

        with contextlib.redirect_stdout(None):
            unpackBook(self.path, self.file, epubver="A", use_hd=True)
            # TODO: add cleanup here

        self.root_dirpath = os.path.join(self.file, "mobi7")
        self.toc_path = os.path.join(self.root_dirpath, "toc.ncx")
        version = "2.0"

        with open(os.path.join(self.root_dirpath, "content.opf")) as f:
            content_opf = ET.parse(f)  # .getroot()

        contents = Epub._get_contents(content_opf)
        self.contents = tuple(os.path.join(self.root_dirpath, content) for content in contents)

        with open(self.toc_path) as f:
            toc = ET.parse(f).getroot()
        self.toc_entries = Epub._get_tocs(toc, version, contents)  # *self.contents (absolute path)

    def get_raw_text(self, content_path: Union[str, ET.Element]) -> str:
        assert isinstance(content_path, str)
        with open(content_path, encoding="utf8") as f:
            content = f.read()
        # return content.decode("utf-8")
        return content

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        # TODO: test on windows
        # if impath "Images/asdf.png" is problematic
        image_abspath = os.path.join(self.root_dirpath, impath)
        image_abspath = os.path.normpath(image_abspath)  # handle crossplatform path
        with open(image_abspath, "rb") as f:
            src = f.read()
        return impath, src

    def cleanup(self) -> None:
        assert isinstance(self.file, str)
        shutil.rmtree(self.file)
        return


class Azw(Epub):
    def __init__(self, fileepub):
        self.path = os.path.abspath(fileepub)
        self.tmpdir = tempfile.mkdtemp(prefix="epy-")
        basename, _ = os.path.splitext(os.path.basename(self.path))
        self.tmpepub = os.path.join(self.tmpdir, "mobi8", basename + ".epub")

    def initialize(self):
        with contextlib.redirect_stdout(None):
            unpackBook(self.path, self.tmpdir, epubver="A", use_hd=True)
        self.file = zipfile.ZipFile(self.tmpepub, "r")
        Epub.initialize(self)

    def cleanup(self) -> None:
        shutil.rmtree(self.tmpdir)
        return


class FictionBook(Ebook):
    NAMESPACE = {"FB2": "http://www.gribuser.ru/xml/fictionbook/2.0"}

    def __init__(self, filefb: str):
        self.path = os.path.abspath(filefb)
        self.file = filefb

        # populate these attribute
        # by calling self.initialize()
        self.root: ET.Element

    def get_meta(self) -> BookMetadata:
        title_elem = self.root.find(".//FB2:book-title", FictionBook.NAMESPACE)
        first_name_elem = self.root.find(".//FB2:first-name", FictionBook.NAMESPACE)
        last_name_elem = self.root.find(".//FB2:last-name", FictionBook.NAMESPACE)
        date_elem = self.root.find(".//FB2:date", FictionBook.NAMESPACE)
        identifier_elem = self.root.find(".//FB2:id", FictionBook.NAMESPACE)

        author = first_name_elem.text if first_name_elem is not None else None
        if last_name_elem is not None:
            if author is not None and author != "":
                author += f" {last_name_elem.text}"
            else:
                author = last_name_elem.text

        return BookMetadata(
            title=title_elem.text if title_elem is not None else None,
            creator=author,
            date=date_elem.text if date_elem is not None else None,
            identifier=identifier_elem.text if identifier_elem is not None else None,
        )

    def initialize(self) -> None:
        cont = ET.parse(self.file)
        self.root = cont.getroot()

        self.contents = tuple(self.root.findall("FB2:body/*", FictionBook.NAMESPACE))

        # TODO
        toc_entries: List[TocEntry] = []
        for n, i in enumerate(self.contents):
            title = i.find("FB2:title", FictionBook.NAMESPACE)
            if title is not None:
                toc_entries.append(
                    TocEntry(label="".join(title.itertext()), content_index=n, section=None)
                )
        self.toc_entries = tuple(toc_entries)

    def get_raw_text(self, node: Union[str, ET.Element]) -> str:
        assert isinstance(node, ET.Element)
        ET.register_namespace("", "http://www.gribuser.ru/xml/fictionbook/2.0")
        # sys.exit(ET.tostring(node, encoding="utf8", method="html").decode("utf-8").replace("ns1:",""))
        return ET.tostring(node, encoding="utf8", method="html").decode("utf-8").replace("ns1:", "")

    def get_img_bytestr(self, imgid: str) -> Tuple[str, bytes]:
        # TODO: test if image works
        imgid = imgid.replace("#", "")
        img_elem = self.root.find("*[@id='{}']".format(imgid))
        assert img_elem is not None
        imgtype = img_elem.get("content-type")
        img_elem_text = img_elem.text
        assert imgtype is not None
        assert img_elem_text is not None
        return imgid + "." + imgtype.split("/")[1], base64.b64decode(img_elem_text)

    def cleanup(self) -> None:
        return


class URL(Ebook):
    _header = {
        "User-Agent": f"epy/v{__version__}",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
    }

    def __init__(self, url: str):
        self.path = url
        self.file = url
        self.contents = ("_",)
        self.toc_entries = tuple()

    def get_meta(self) -> BookMetadata:
        return BookMetadata()

    def initialize(self) -> None:
        try:
            with urlopen(Request(self.path, headers=URL._header)) as response:
                self.html = response.read().decode()
        except HTTPError as e:
            raise e
        except URLError as e:
            raise e

    def get_raw_text(self, _) -> str:
        return self.html

    def get_img_bytestr(self, src: str) -> Tuple[str, bytes]:
        image_url = src if is_url(src) else urljoin(self.path, src)
        # TODO: catch error on request
        with urlopen(Request(image_url, headers=URL._header)) as response:
            byte_str = response.read()
        return PurePosixPath(urlparse(src).path).name, byte_str

    def cleanup(self) -> None:
        return


# }}}


# HTML & Text Parser {{{


class HTMLtoLines(HTMLParser):
    para = {"p", "div"}
    inde = {"q", "dt", "dd", "blockquote"}
    pref = {"pre"}
    bull = {"li"}
    hide = {"script", "style", "head"}
    ital = {"i", "em"}
    bold = {"b", "strong"}
    # hide = {"script", "style", "head", ", "sub}
    # sup_lookup = "⁰¹²³⁴⁵⁶⁷⁸⁹"
    # sub_lookup = "₀₁₂₃₄₅₆₇₈₉"

    attr_bold = curses.A_BOLD
    try:
        attr_italic = curses.A_ITALIC
    except AttributeError:
        try:
            attr_italic = curses.A_UNDERLINE
        except AttributeError:
            attr_italic = curses.A_NORMAL

    @staticmethod
    def _mark_to_spans(text: Sequence[str], marks: Sequence[TextMark]) -> List[TextSpan]:
        """
        Convert text marks in line of text to per line text span.
        Keeping duplicate spans.
        """
        spans: List[TextSpan] = []
        for mark in marks:
            if mark.is_valid():
                # mypy issue, should be handled by mark.is_valid()
                assert mark.end is not None
                if mark.start.row == mark.end.row:
                    spans.append(
                        TextSpan(start=mark.start, n_letters=mark.end.col - mark.start.col)
                    )
                else:
                    spans.append(
                        TextSpan(
                            start=mark.start, n_letters=len(text[mark.start.row]) - mark.start.col
                        )
                    )
                    for nth_line in range(mark.start.row + 1, mark.end.row):
                        spans.append(
                            TextSpan(
                                start=CharPos(row=nth_line, col=0), n_letters=len(text[nth_line])
                            )
                        )
                    spans.append(
                        TextSpan(start=CharPos(row=mark.end.row, col=0), n_letters=mark.end.col)
                    )

        return spans  # list(set(spans))

    @staticmethod
    def _adjust_wrapped_spans(
        wrapped_lines: Sequence[str],
        span: TextSpan,
        *,
        line_adjustment: int = 0,
        left_adjustment: int = 0,
    ) -> List[TextSpan]:
        """
        Adjust text span to wrapped lines.
        Not perfect, but should be good enough considering
        the limitation on commandline interface.
        """

        # current_row = span.start.row + line_adjustment
        current_row = line_adjustment
        start_col = span.start.col
        end_col = start_col + span.n_letters

        prev = 0  # chars length before current line
        spans: List[TextSpan] = []
        for n, line in enumerate(wrapped_lines):
            # + 1 compensates textwrap.wrap(*args, replace_whitespace=True, drop_whitespace=True)
            line_len = len(line) + 1
            current = prev + line_len  # chars length before next line

            # -:unmarked *:marked
            # |------*****--------|
            if start_col in range(prev, current) and end_col in range(prev, current):
                spans.append(
                    TextSpan(
                        start=CharPos(row=current_row + n, col=start_col - prev + left_adjustment),
                        n_letters=span.n_letters,
                    )
                )

            # |----------*********|
            elif start_col in range(prev, current):
                spans.append(
                    TextSpan(
                        start=CharPos(row=current_row + n, col=start_col - prev + left_adjustment),
                        n_letters=current - start_col - 1,  # -1: dropped whitespace
                    )
                )

            # |********-----------|
            elif end_col in range(prev, current):
                spans.append(
                    TextSpan(
                        start=CharPos(row=current_row + n, col=0 + left_adjustment),
                        n_letters=end_col - prev + 1,  # +1: dropped whitespace
                    )
                )

            # |*******************|
            elif prev in range(start_col, end_col) and current in range(start_col, end_col):
                spans.append(
                    TextSpan(
                        start=CharPos(row=current_row + n, col=0 + left_adjustment),
                        n_letters=line_len - 1,  # -1: dropped whitespace
                    )
                )

            elif prev > end_col:
                break

            prev = current

        return spans

    @staticmethod
    def _group_spans_by_row(blocks: Sequence[TextSpan]) -> Mapping[int, List[TextSpan]]:
        groups: Dict[int, List[TextSpan]] = {}
        for block in blocks:
            row = block.start.row
            if row in groups:
                groups[row].append(block)
            else:
                groups[row] = [block]
        return groups

    def __init__(self, sects={""}):
        HTMLParser.__init__(self)
        self.text = [""]
        self.ishead = False
        self.isinde = False
        self.isbull = False
        self.ispref = False
        self.ishidden = False
        self.idhead = set()
        self.idinde = set()
        self.idbull = set()
        self.idpref = set()
        self.idimgs = set()
        self.sects = sects
        self.sectsindex = {}
        self.italic_marks: List[TextMark] = []
        self.bold_marks: List[TextMark] = []
        self.imgs: Dict[int, str] = dict()

    def handle_starttag(self, tag, attrs):
        if re.match("h[1-6]", tag) is not None:
            self.ishead = True
        elif tag in self.inde:
            self.isinde = True
        elif tag in self.pref:
            self.ispref = True
        elif tag in self.bull:
            self.isbull = True
        elif tag in self.hide:
            self.ishidden = True
        elif tag == "sup":
            self.text[-1] += "^{"
        elif tag == "sub":
            self.text[-1] += "_{"
        # NOTE: "img" and "image"
        # In HTML, both are startendtag (no need endtag)
        # but in XHTML both need endtag
        elif tag in {"img", "image"}:
            for i in attrs:
                if (tag == "img" and i[0] == "src") or (tag == "image" and i[0].endswith("href")):
                    this_line = len(self.text)
                    self.idimgs.add(this_line)
                    self.imgs[this_line] = unquote(i[1])
                    self.text.append("[IMAGE]")
        # formatting
        elif tag in self.ital:
            if len(self.italic_marks) == 0 or self.italic_marks[-1].is_valid():
                char_pos = CharPos(row=len(self.text) - 1, col=len(self.text[-1]))
                self.italic_marks.append(TextMark(start=char_pos))
        elif tag in self.bold:
            if len(self.bold_marks) == 0 or self.bold_marks[-1].is_valid():
                char_pos = CharPos(row=len(self.text) - 1, col=len(self.text[-1]))
                self.bold_marks.append(TextMark(start=char_pos))
        if self.sects != {""}:
            for i in attrs:
                if i[0] == "id" and i[1] in self.sects:
                    # self.text[-1] += " (#" + i[1] + ") "
                    # self.sectsindex.append([len(self.text), i[1]])
                    self.sectsindex[len(self.text) - 1] = i[1]

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.text += [""]
        elif tag in {"img", "image"}:
            for i in attrs:
                #  if (tag == "img" and i[0] == "src")\
                #     or (tag == "image" and i[0] == "xlink:href"):
                if (tag == "img" and i[0] == "src") or (tag == "image" and i[0].endswith("href")):
                    this_line = len(self.text)
                    self.idimgs.add(this_line)
                    self.imgs[this_line] = unquote(i[1])
                    self.text.append("[IMAGE]")
                    self.text.append("")
        # sometimes attribute "id" is inside "startendtag"
        # especially html from mobi module (kindleunpack fork)
        if self.sects != {""}:
            for i in attrs:
                if i[0] == "id" and i[1] in self.sects:
                    # self.text[-1] += " (#" + i[1] + ") "
                    self.sectsindex[len(self.text) - 1] = i[1]

    def handle_endtag(self, tag):
        if re.match("h[1-6]", tag) is not None:
            self.text.append("")
            self.text.append("")
            self.ishead = False
        elif tag in self.para:
            self.text.append("")
        elif tag in self.hide:
            self.ishidden = False
        elif tag in self.inde:
            if self.text[-1] != "":
                self.text.append("")
            self.isinde = False
        elif tag in self.pref:
            if self.text[-1] != "":
                self.text.append("")
            self.ispref = False
        elif tag in self.bull:
            if self.text[-1] != "":
                self.text.append("")
            self.isbull = False
        elif tag in {"sub", "sup"}:
            self.text[-1] += "}"
        elif tag in {"img", "image"}:
            self.text.append("")
        # formatting
        elif tag in self.ital:
            char_pos = CharPos(row=len(self.text) - 1, col=len(self.text[-1]))
            last_mark = self.italic_marks[-1]
            self.italic_marks[-1] = dataclasses.replace(last_mark, end=char_pos)
        elif tag in self.bold:
            char_pos = CharPos(row=len(self.text) - 1, col=len(self.text[-1]))
            last_mark = self.bold_marks[-1]
            self.bold_marks[-1] = dataclasses.replace(last_mark, end=char_pos)

    def handle_data(self, raw):
        if raw and not self.ishidden:
            if self.text[-1] == "":
                tmp = raw.lstrip()
            else:
                tmp = raw
            if self.ispref:
                line = unescape(tmp)
            else:
                line = unescape(re.sub(r"\s+", " ", tmp))
            self.text[-1] += line
            if self.ishead:
                self.idhead.add(len(self.text) - 1)
            elif self.isbull:
                self.idbull.add(len(self.text) - 1)
            elif self.isinde:
                self.idinde.add(len(self.text) - 1)
            elif self.ispref:
                self.idpref.add(len(self.text) - 1)

    def get_structured_text(
        self, textwidth: Optional[int] = 0, starting_line: int = 0
    ) -> Union[Tuple[str, ...], TextStructure]:

        if not textwidth:
            return tuple(self.text)

        # reusable loop indices
        i: Any

        text: List[str] = []
        images: Dict[int, str] = dict()  # {line_num: path/in/zip}
        sect: Dict[str, int] = dict()  # {section_id: line_num}
        formatting: List[InlineStyle] = []

        italic_spans: List[TextSpan] = HTMLtoLines._mark_to_spans(self.text, self.italic_marks)
        bold_spans: List[TextSpan] = HTMLtoLines._mark_to_spans(self.text, self.bold_marks)
        italic_groups = HTMLtoLines._group_spans_by_row(italic_spans)
        bold_groups = HTMLtoLines._group_spans_by_row(bold_spans)

        for n, line in enumerate(self.text):

            startline = len(text)
            # findsect = re.search(r"(?<= \(#).*?(?=\) )", line)
            # if findsect is not None and findsect.group() in self.sects:
            # line = line.replace(" (#" + findsect.group() + ") ", "")
            # # line = line.replace(" (#" + findsect.group() + ") ", " "*(5+len(findsect.group())))
            # sect[findsect.group()] = len(text)
            if n in self.sectsindex.keys():
                sect[self.sectsindex[n]] = starting_line + len(text)
            if n in self.idhead:
                # text += [line.rjust(textwidth // 2 + len(line) // 2)] + [""]
                text += [line.center(textwidth)] + [""]
                formatting += [
                    InlineStyle(
                        row=starting_line + i, col=0, n_letters=len(text[i]), attr=self.attr_bold
                    )
                    for i in range(startline, len(text))
                ]
            elif n in self.idinde:
                text += ["   " + i for i in textwrap.wrap(line, textwidth - 3)] + [""]
            elif n in self.idbull:
                tmp = textwrap.wrap(line, textwidth - 3)
                text += [" - " + i if i == tmp[0] else "   " + i for i in tmp] + [""]
            elif n in self.idpref:
                tmp = line.splitlines()
                wraptmp = []
                for tmp_line in tmp:
                    wraptmp += [i for i in textwrap.wrap(tmp_line, textwidth - 6)]
                text += ["   " + i for i in wraptmp] + [""]
            elif n in self.idimgs:
                images[starting_line + len(text)] = self.imgs[n]
                text += [line.center(textwidth)]
                formatting += [
                    InlineStyle(
                        row=starting_line + len(text) - 1,
                        col=0,
                        n_letters=len(text[-1]),
                        attr=self.attr_bold,
                    )
                ]
                text += [""]
            else:
                text += textwrap.wrap(line, textwidth) + [""]

            endline = len(text)  # -1

            left_adjustment = 3 if n in self.idbull | self.idinde else 0

            for spans in italic_groups.get(n, []):
                italics = HTMLtoLines._adjust_wrapped_spans(
                    text[startline:endline],
                    spans,
                    line_adjustment=startline,
                    left_adjustment=left_adjustment,
                )
                for span in italics:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + span.start.row,
                            col=span.start.col,
                            n_letters=span.n_letters,
                            attr=self.attr_italic,
                        )
                    )

            for spans in bold_groups.get(n, []):
                bolds = HTMLtoLines._adjust_wrapped_spans(
                    text[startline:endline],
                    spans,
                    line_adjustment=startline,
                    left_adjustment=left_adjustment,
                )
                for span in bolds:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + span.start.row,
                            col=span.start.col,
                            n_letters=span.n_letters,
                            attr=self.attr_bold,
                        )
                    )

        # chapter suffix
        text += ["***".center(textwidth)]

        return TextStructure(
            text_lines=tuple(text),
            image_maps=images,
            section_rows=sect,
            formatting=tuple(formatting),
        )


# }}}


# App Configuration {{{


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


class Config(AppData):
    def __init__(self):
        setting_dict = dataclasses.asdict(Settings())
        keymap_dict = dataclasses.asdict(CfgDefaultKeymaps())
        keymap_builtin_dict = dataclasses.asdict(CfgBuiltinKeymaps())

        if os.path.isfile(self.filepath):
            with open(self.filepath) as f:
                cfg_user = json.load(f)
            setting_dict = Config.update_dict(setting_dict, cfg_user["Setting"])
            keymap_dict = Config.update_dict(keymap_dict, cfg_user["Keymap"])
        else:
            self.save({"Setting": setting_dict, "Keymap": keymap_dict})

        keymap_dict_tuple = {k: tuple(v) for k, v in keymap_dict.items()}
        keymap_updated = {
            k: tuple([Key(i) for i in v])
            for k, v in Config.update_keys_tuple(keymap_dict_tuple, keymap_builtin_dict).items()
        }

        if sys.platform == "win32":
            setting_dict["PageScrollAnimation"] = False

        self.setting = Settings(**setting_dict)
        self.keymap = Keymap(**keymap_updated)
        # to build help menu text
        self.keymap_user_dict = keymap_dict

    @property
    def filepath(self) -> str:
        return os.path.join(self.prefix, "configuration.json") if self.prefix else os.devnull

    def save(self, cfg_dict):
        with open(self.filepath, "w") as file:
            json.dump(cfg_dict, file, indent=2)

    @staticmethod
    def update_dict(
        old_dict: Mapping[str, Union[str, int, bool]],
        new_dict: Mapping[str, Union[str, int, bool]],
        place_new=False,
    ) -> Mapping[str, Union[str, int, bool]]:
        """Returns a copy of `old_dict` after updating it with `new_dict`"""

        result = {**old_dict}
        for k, v in new_dict.items():
            if k in result:
                result[k] = new_dict[k]
            elif place_new:
                result[k] = new_dict[k]

        return result

    @staticmethod
    def update_keys_tuple(
        old_keys: Mapping[str, Tuple[str, ...]],
        new_keys: Mapping[str, Tuple[str, ...]],
        place_new: bool = False,
    ) -> Mapping[str, Tuple[str, ...]]:
        """Returns a copy of `old_keys` after updating it with `new_keys`
        by appending the tuple value and removes duplicate"""

        result = {**old_keys}
        for k, v in new_keys.items():
            if k in result:
                result[k] = tuple(set(result[k] + new_keys[k]))
            elif place_new:
                result[k] = tuple(set(new_keys[k]))

        return result


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


# }}}


# Text Board {{{


class InfiniBoard:
    """
    Wrapper for curses screen to render infinite texts.
    The idea is instead of pre render all the text before reading,
    this will only renders part of text on demand by which available
    page on screen.

    And what this does is only drawing text/string on curses screen
    without .clear() or .refresh() to optimize performance.
    """

    def __init__(
        self,
        screen,
        text: Tuple[str, ...],
        textwidth: int = 80,
        default_style: Tuple[InlineStyle, ...] = tuple(),
        spread: int = 1,
    ):
        self.screen = screen
        self.screen_rows, self.screen_cols = self.screen.getmaxyx()
        self.textwidth = textwidth
        self.x = ((self.screen_cols - self.textwidth) // 2) + 1
        self.text = text
        self.total_lines = len(text)
        self.default_style: Tuple[InlineStyle, ...] = default_style
        self.temporary_style: Tuple[InlineStyle, ...] = ()
        self.spread = spread

        if self.spread == 2:
            self.x = DoubleSpreadPadding.LEFT.value
            self.x_alt = (
                DoubleSpreadPadding.LEFT.value + self.textwidth + DoubleSpreadPadding.MIDDLE.value
            )

    def feed_temporary_style(self, styles: Optional[Tuple[InlineStyle, ...]] = None) -> None:
        """Reset styling if `styles` is None"""
        self.temporary_style = styles if styles else ()

    def render_styles(
        self, row: int, styles: Tuple[InlineStyle, ...] = (), bottom_padding: int = 0
    ) -> None:
        for i in styles:
            if i.row in range(row, row + self.screen_rows - bottom_padding):
                self.chgat(row, i.row, i.col, i.n_letters, self.screen.getbkgd() | i.attr)

            if self.spread == 2 and i.row in range(
                row + self.screen_rows - bottom_padding,
                row + 2 * (self.screen_rows - bottom_padding),
            ):
                self.chgat(
                    row,
                    i.row - (self.screen_rows - bottom_padding),
                    -self.x + self.x_alt + i.col,
                    i.n_letters,
                    self.screen.getbkgd() | i.attr,
                )

    def getch(self) -> Union[NoUpdate, Key]:
        input = self.screen.getch()
        if input == -1:
            return NoUpdate()
        return Key(input)

    def getbkgd(self):
        return self.screen.getbkgd()

    def chgat(self, row: int, y: int, x: int, n: int, attr: int) -> None:
        self.screen.chgat(y - row, self.x + x, n, attr)

    def write(self, row: int, bottom_padding: int = 0) -> None:
        for n_row in range(min(self.screen_rows - bottom_padding, self.total_lines - row)):
            text_line = self.text[row + n_row]
            self.screen.addstr(n_row, self.x, text_line)

            if (
                self.spread == 2
                and row + self.screen_rows - bottom_padding + n_row < self.total_lines
            ):
                text_line = self.text[row + self.screen_rows - bottom_padding + n_row]
                # TODO: clean this up
                if re.search("\\[IMG:[0-9]+\\]", text_line):
                    self.screen.addstr(
                        n_row, self.x_alt, text_line.center(self.textwidth), curses.A_BOLD
                    )
                else:
                    self.screen.addstr(n_row, self.x_alt, text_line)

        self.render_styles(row, self.default_style, bottom_padding)
        self.render_styles(row, self.temporary_style, bottom_padding)
        # self.screen.refresh()

    def write_n(
        self,
        row: int,
        n: int = 1,
        direction: Direction = Direction.FORWARD,
        bottom_padding: int = 0,
    ) -> None:
        assert n > 0
        for n_row in range(min(self.screen_rows - bottom_padding, self.total_lines - row)):
            text_line = self.text[row + n_row]
            if direction == Direction.FORWARD:
                # self.screen.addnstr(n_row, self.x + self.textwidth - n, self.text[row+n_row], n)
                # `+ " " * (self.textwidth - len(self.text[row + n_row]))` is workaround to
                # to prevent curses trace because not calling screen.clear()
                self.screen.addnstr(
                    n_row,
                    self.x + self.textwidth - n,
                    text_line + " " * (self.textwidth - len(text_line)),
                    n,
                )

                if (
                    self.spread == 2
                    and row + self.screen_rows - bottom_padding + n_row < self.total_lines
                ):
                    text_line_alt = self.text[row + n_row + self.screen_rows - bottom_padding]
                    self.screen.addnstr(
                        n_row,
                        self.x_alt + self.textwidth - n,
                        text_line_alt + " " * (self.textwidth - len(text_line_alt)),
                        n,
                    )

            else:
                if text_line[self.textwidth - n :]:
                    self.screen.addnstr(n_row, self.x, text_line[self.textwidth - n :], n)

                if (
                    self.spread == 2
                    and row + self.screen_rows - bottom_padding + n_row < self.total_lines
                ):
                    text_line_alt = self.text[row + n_row + self.screen_rows - bottom_padding]
                    self.screen.addnstr(
                        n_row,
                        self.x_alt,
                        text_line_alt[self.textwidth - n :],
                        n,
                    )


# }}}


# Helpers & Utils {{{


def coerce_to_int(string: str) -> Optional[int]:
    try:
        return int(string)
    except ValueError:
        return None


def cleanup_library(state: State) -> None:
    """Cleanup non-existent file from library"""
    library_items = state.get_from_history()
    for item in library_items:
        if not os.path.isfile(item.filepath) and not is_url(item.filepath):
            state.delete_from_library(item.filepath)


def get_nth_file_from_library(state: State, n) -> Optional[LibraryItem]:
    library_items = state.get_from_history()
    try:
        return library_items[n - 1]
    except IndexError:
        return None


def get_matching_library_item(
    state: State, pattern: str, threshold: float = 0.5
) -> Optional[LibraryItem]:
    matches: List[Tuple[LibraryItem, float]] = []  # [(library_item, match_value), ...]
    library_items = state.get_from_history()
    if not library_items:
        return None

    for item in library_items:
        tomatch = f"{item.title} - {item.author}"  # item.filepath
        match_value = sum(
            [i.size for i in SM(None, tomatch.lower(), pattern.lower()).get_matching_blocks()]
        ) / float(len(pattern))
        matches.append(
            (
                item,
                match_value,
            )
        )

    sorted_matches = sorted(matches, key=lambda x: -x[1])
    first_match_item, first_match_value = sorted_matches[0]
    if first_match_item and first_match_value >= threshold:
        return first_match_item
    else:
        return None


def print_reading_history(state: State) -> None:
    termc, _ = shutil.get_terminal_size()
    library_items = state.get_from_history()
    if not library_items:
        print("No Reading History.")
        return

    print("Reading History:")
    dig = len(str(len(library_items) + 1))
    tcols = termc - dig - 2
    for n, item in enumerate(library_items):
        print(
            "{} {}".format(
                str(n + 1).rjust(dig),
                truncate(str(item), "...", tcols, tcols - 3),
            )
        )


def is_url(string: str) -> bool:
    try:
        tmp = urlparse(string)
        return all([tmp.scheme, tmp.netloc])
    except ValueError:
        return False


def construct_speaker(
    preferred: Optional[str] = None, args: List[str] = []
) -> Optional[SpeakerBaseModel]:
    speakers = (
        sorted(SPEAKERS, key=lambda x: int(x.cmd == preferred), reverse=True)
        if preferred
        else SPEAKERS
    )
    speaker = next((speaker for speaker in speakers if speaker.available), None)
    return speaker(args) if speaker else None


def parse_html(
    html_src: str,
    *,
    textwidth: Optional[int] = None,
    section_ids: Optional[Set[str]] = None,
    starting_line: int = 0,
) -> Union[Tuple[str, ...], TextStructure]:
    """
    Parse html string into TextStructure

    :param html_src: html str to parse
    :param textwidth: textwidth to count max length of returned TextStructure
                      if None given, sequence of text as paragraph is returned
    :param section_ids: set of section ids to look for inside html tag attr
    :return: Tuple[str, ...] if textwidth not given else TextStructure
    """
    if not section_ids:
        section_ids = set()

    parser = HTMLtoLines(section_ids)
    # try:
    parser.feed(html_src)
    parser.close()
    # except:
    #     pass

    return parser.get_structured_text(textwidth, starting_line)


def dump_ebook_content(filepath: str) -> None:
    ebook = get_ebook_obj(filepath)
    try:
        try:
            ebook.initialize()
        except Exception as e:
            sys.exit("ERROR: Badly-structured ebook.\n" + str(e))
        for i in ebook.contents:
            content = ebook.get_raw_text(i)
            src_lines = parse_html(content)
            assert isinstance(src_lines, tuple)
            # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
            for j in src_lines:
                sys.stdout.buffer.write((j + "\n\n").encode("utf-8"))
    finally:
        ebook.cleanup()


def merge_text_structures(
    text_structure_first: TextStructure, text_structure_second: TextStructure
) -> TextStructure:
    return TextStructure(
        text_lines=text_structure_first.text_lines + text_structure_second.text_lines,
        image_maps={**text_structure_first.image_maps, **text_structure_second.image_maps},
        section_rows={**text_structure_first.section_rows, **text_structure_second.section_rows},
        formatting=text_structure_first.formatting + text_structure_second.formatting,
    )


def construct_relative_reading_state(
    abs_reading_state: ReadingState, totlines_per_content: Sequence[int]
) -> ReadingState:
    """
    :param abs_reading_state: ReadingState absolute to whole book when Setting.Seamless==True
    :param totlines_per_content: sequence of total lines per book content
    :return: new ReadingState relative to per content of the book
    """
    index = 0
    cumulative_contents_lines = 0
    all_contents_lines = sum(totlines_per_content)
    # for n, content_lines in enumerate(totlines_per_content):
    #     cumulative_contents_lines += content_lines
    #     if cumulative_contents_lines > abs_reading_state.row:
    #         return
    while True:
        content_lines = totlines_per_content[index]
        cumulative_contents_lines += content_lines
        if cumulative_contents_lines > abs_reading_state.row:
            break
        index += 1

    return ReadingState(
        content_index=index,
        textwidth=abs_reading_state.textwidth,
        row=abs_reading_state.row - cumulative_contents_lines + content_lines,
        rel_pctg=abs_reading_state.rel_pctg
        - ((cumulative_contents_lines - content_lines) / all_contents_lines)
        if abs_reading_state.rel_pctg
        else None,
        section=abs_reading_state.section,
    )


def get_ebook_obj(filepath: str) -> Ebook:
    file_ext = os.path.splitext(filepath)[1].lower()
    if is_url(filepath):
        return URL(filepath)
    elif file_ext in {".epub", ".epub3"}:
        return Epub(filepath)
    elif file_ext == ".fb2":
        return FictionBook(filepath)
    elif MOBI_SUPPORT and file_ext == ".mobi":
        return Mobi(filepath)
    elif MOBI_SUPPORT and file_ext in {".azw", ".azw3"}:
        return Azw(filepath)
    elif not MOBI_SUPPORT and file_ext in {".mobi", ".azw3"}:
        sys.exit(
            "ERROR: Format not supported. (Supported: epub, fb2). "
            "To get mobi and azw3 support, install epy via pip. "
        )
    else:
        sys.exit("ERROR: Format not supported. (Supported: epub, fb2)")


def tuple_subtract(tuple_one: Tuple[Any, ...], tuple_two: Tuple[Any, ...]) -> Tuple[Any, ...]:
    """
    Returns tuple with members in tuple_one
    but not in tuple_two
    """
    return tuple(i for i in tuple_one if i not in tuple_two)


def pgup(current_row: int, window_height: int, counter: int = 1) -> int:
    if current_row >= (window_height) * counter:
        return current_row - (window_height) * counter
    else:
        return 0


def pgdn(current_row: int, total_lines: int, window_height: int, counter: int = 1) -> int:
    if current_row + (window_height * counter) <= total_lines - window_height:
        return current_row + (window_height * counter)
    else:
        current_row = total_lines - window_height
        if current_row < 0:
            return 0
        return current_row


def pgend(total_lines: int, window_height: int) -> int:
    if total_lines - window_height >= 0:
        return total_lines - window_height
    else:
        return 0


def truncate(teks: str, subtitution_text: str, maxlen: int, startsub: int = 0) -> str:
    """
    Truncate text

    eg.
    :param teks: 'This is long silly dummy text'
    :param subtitution_text:  '...'
    :param maxlen: 12
    :param startsub: 3
    :return: 'This...ly dummy text'
    """
    if startsub > maxlen:
        raise ValueError("Var startsub cannot be bigger than maxlen.")
    elif len(teks) <= maxlen:
        return teks
    else:
        lensu = len(subtitution_text)
        beg = teks[:startsub]
        mid = (
            subtitution_text
            if lensu <= maxlen - startsub
            else subtitution_text[: maxlen - startsub]
        )
        end = teks[startsub + lensu - maxlen :] if lensu < maxlen - startsub else ""
        return beg + mid + end


def safe_curs_set(state: int) -> None:
    try:
        curses.curs_set(state)
    except:
        return


def resolve_path(current_dir: str, relative_path: str) -> str:
    """
    Resolve path containing dots
    eg. '/foo/bar/book.html' + '../img.png' = '/foo/img.png'
    NOTE: '/' suffix is important to tell that current dir in 'bar'
    """
    # can also using os.path.normpath()
    # but if the image in zipfile then posix path is mandatory
    return urljoin(current_dir, relative_path)


def find_current_content_index(
    toc_entries: Tuple[TocEntry, ...], toc_secid: Mapping[str, int], index: int, y: int
) -> int:
    ntoc = 0
    for n, toc_entry in enumerate(toc_entries):
        if toc_entry.content_index <= index:
            if y >= toc_secid.get(toc_entry.section, 0):  # type: ignore
                ntoc = n
    return ntoc


def count_letters(ebook: Ebook) -> LettersCount:
    per_content_counts: List[int] = []
    cumulative_counts: List[int] = []
    # assert isinstance(ebook.contents, tuple)
    for i in ebook.contents:
        content = ebook.get_raw_text(i)
        src_lines = parse_html(content)
        assert isinstance(src_lines, tuple)
        cumulative_counts.append(sum(per_content_counts))
        per_content_counts.append(sum([len(re.sub(r"\s", "", j)) for j in src_lines]))

    return LettersCount(all=sum(per_content_counts), cumulative=tuple(cumulative_counts))


def count_letters_parallel(ebook: Ebook, child_conn) -> None:
    child_conn.send(count_letters(ebook))
    child_conn.close()


def choice_win(allowdel=False):
    """
    Conjure options window by wrapping a window function
    which has a return type of tuple in the form of
    (title, list_to_chose, initial_active_index, windows_key_to_toggle)
    and return tuple of (returned_key, chosen_index, chosen_index_to_delete)
    """

    def inner_f(listgen):
        @wraps(listgen)
        def wrapper(self, *args, **kwargs):
            rows, cols = self.screen.getmaxyx()
            hi, wi = rows - 4, cols - 4
            Y, X = 2, 2
            chwin = curses.newwin(hi, wi, Y, X)
            if self.is_color_supported:
                chwin.bkgd(self.screen.getbkgd())

            title, ch_list, index, key = listgen(self, *args, **kwargs)

            if len(title) > cols - 8:
                title = title[: cols - 8]

            chwin.box()
            chwin.keypad(True)
            chwin.addstr(1, 2, title)
            chwin.addstr(2, 2, "-" * len(title))
            if allowdel:
                chwin.addstr(3, 2, "HINT: Press 'd' to delete.")
            key_chwin = 0

            totlines = len(ch_list)
            chwin.refresh()
            pad = curses.newpad(totlines, wi - 2)
            if self.is_color_supported:
                pad.bkgd(self.screen.getbkgd())

            pad.keypad(True)

            padhi = rows - 5 - Y - 4 + 1 - (1 if allowdel else 0)
            # padhi = rows - 5 - Y - 4 + 1 - 1
            y = 0
            if index in range(padhi // 2, totlines - padhi // 2):
                y = index - padhi // 2 + 1
            span = []

            for n, i in enumerate(ch_list):
                # strs = "  " + str(n+1).rjust(d) + " " + i[0]
                # remove newline from choice entries
                # mostly happens in FictionBook (.fb2) format
                strs = "  " + i.replace("\n", " ")
                strs = strs[0 : wi - 3]
                pad.addstr(n, 0, strs)
                span.append(len(strs))

            countstring = ""
            while key_chwin not in self.keymap.Quit + key:
                if countstring == "":
                    count = 1
                else:
                    count = int(countstring)
                if key_chwin in tuple(Key(i) for i in range(48, 58)):  # i.e., k is a numeral
                    countstring = countstring + key_chwin.char
                else:
                    if key_chwin in self.keymap.ScrollUp + self.keymap.PageUp:
                        index -= count
                        if index < 0:
                            index = 0
                    elif key_chwin in self.keymap.ScrollDown or key_chwin in self.keymap.PageDown:
                        index += count
                        if index + 1 >= totlines:
                            index = totlines - 1
                    elif key_chwin in self.keymap.Follow:
                        chwin.clear()
                        chwin.refresh()
                        return None, index, None
                    elif key_chwin in self.keymap.BeginningOfCh:
                        index = 0
                    elif key_chwin in self.keymap.EndOfCh:
                        index = totlines - 1
                    elif key_chwin == Key("D") and allowdel:
                        return None, (0 if index == 0 else index - 1), index
                        # chwin.redrawwin()
                        # chwin.refresh()
                    elif key_chwin == Key("d") and allowdel:
                        resk, resp, _ = self.show_win_options(
                            "Delete '{}'?".format(ch_list[index]),
                            ["(Y)es", "(N)o"],
                            0,
                            (Key("n"),),
                        )
                        if resk is not None:
                            key_chwin = resk
                            continue
                        elif resp == 0:
                            return None, (0 if index == 0 else index - 1), index
                        chwin.redrawwin()
                        chwin.refresh()
                    elif key_chwin in {Key(i) for i in ["Y", "y", "N", "n"]} and ch_list == [
                        "(Y)es",
                        "(N)o",
                    ]:
                        if key_chwin in {Key("Y"), Key("y")}:
                            return None, 0, None
                        else:
                            return None, 1, None
                    elif key_chwin in tuple_subtract(self._win_keys, key):
                        chwin.clear()
                        chwin.refresh()
                        return key_chwin, index, None
                    countstring = ""

                while index not in range(y, y + padhi):
                    if index < y:
                        y -= 1
                    else:
                        y += 1

                for n in range(totlines):
                    att = curses.A_REVERSE if index == n else curses.A_NORMAL
                    pre = ">>" if index == n else "  "
                    pad.addstr(n, 0, pre)
                    pad.chgat(n, 0, span[n], pad.getbkgd() | att)

                pad.refresh(y, 0, Y + 4 + (1 if allowdel else 0), X + 4, rows - 5, cols - 6)
                # pad.refresh(y, 0, Y+5, X+4, rows - 5, cols - 6)
                key_chwin = Key(chwin.getch())
                if key_chwin == Key(curses.KEY_MOUSE):
                    mouse_event = curses.getmouse()
                    if mouse_event[4] == curses.BUTTON4_PRESSED:
                        key_chwin = self.keymap.ScrollUp[0]
                    elif mouse_event[4] == 2097152:
                        key_chwin = self.keymap.ScrollDown[0]
                    elif mouse_event[4] == curses.BUTTON1_DOUBLE_CLICKED:
                        if (
                            mouse_event[2] >= 6
                            and mouse_event[2] < rows - 4
                            and mouse_event[2] < 6 + totlines
                        ):
                            index = mouse_event[2] - 6 + y
                        key_chwin = self.keymap.Follow[0]
                    elif (
                        mouse_event[4] == curses.BUTTON1_CLICKED
                        and mouse_event[2] >= 6
                        and mouse_event[2] < rows - 4
                        and mouse_event[2] < 6 + totlines
                    ):
                        if index == mouse_event[2] - 6 + y:
                            key_chwin = self.keymap.Follow[0]
                            continue
                        index = mouse_event[2] - 6 + y
                    elif mouse_event[4] == curses.BUTTON3_CLICKED:
                        key_chwin = self.keymap.Quit[0]

            chwin.clear()
            chwin.refresh()
            return None, None, None

        return wrapper

    return inner_f


def text_win(textfunc):
    @wraps(textfunc)
    def wrapper(self, *args, **kwargs) -> Union[NoUpdate, Key]:
        rows, cols = self.screen.getmaxyx()
        hi, wi = rows - 4, cols - 4
        Y, X = 2, 2
        textw = curses.newwin(hi, wi, Y, X)
        if self.is_color_supported:
            textw.bkgd(self.screen.getbkgd())

        title, raw_texts, key = textfunc(self, *args, **kwargs)

        if len(title) > cols - 8:
            title = title[: cols - 8]

        texts = []
        for i in raw_texts.splitlines():
            texts += textwrap.wrap(i, wi - 6, drop_whitespace=False)

        textw.box()
        textw.keypad(True)
        textw.addstr(1, 2, title)
        textw.addstr(2, 2, "-" * len(title))
        key_textw: Union[NoUpdate, Key] = NoUpdate()

        totlines = len(texts)

        pad = curses.newpad(totlines, wi - 2)
        if self.is_color_supported:
            pad.bkgd(self.screen.getbkgd())

        pad.keypad(True)
        for n, i in enumerate(texts):
            pad.addstr(n, 0, i)
        y = 0
        textw.refresh()
        pad.refresh(y, 0, Y + 4, X + 4, rows - 5, cols - 6)
        padhi = rows - 8 - Y

        while key_textw not in self.keymap.Quit + key:
            if key_textw in self.keymap.ScrollUp and y > 0:
                y -= 1
            elif key_textw in self.keymap.ScrollDown and y < totlines - hi + 6:
                y += 1
            elif key_textw in self.keymap.PageUp:
                y = pgup(y, padhi)
            elif key_textw in self.keymap.PageDown:
                y = pgdn(y, totlines, padhi)
            elif key_textw in self.keymap.BeginningOfCh:
                y = 0
            elif key_textw in self.keymap.EndOfCh:
                y = pgend(totlines, padhi)
            elif key_textw in tuple_subtract(self._win_keys, key):
                textw.clear()
                textw.refresh()
                return key_textw
            pad.refresh(y, 0, 6, 5, rows - 5, cols - 5)
            key_textw = Key(textw.getch())

        textw.clear()
        textw.refresh()
        return NoUpdate()

    return wrapper


# }}}


# Main Reading Interface {{{


class Reader:
    def __init__(self, screen, ebook: Ebook, config: Config, state: State):

        self.setting = config.setting
        self.keymap = config.keymap
        # to build help menu text
        self.keymap_user_dict = config.keymap_user_dict

        self.seamless = self.setting.SeamlessBetweenChapters

        # keys that will make
        # windows exit and return the said key
        self._win_keys = (
            # curses.KEY_RESIZE is a must
            (Key(curses.KEY_RESIZE),)
            + self.keymap.TableOfContents
            + self.keymap.Metadata
            + self.keymap.Help
        )

        # screen initialization
        self.screen = screen
        self.screen.keypad(True)
        safe_curs_set(0)
        if self.setting.MouseSupport:
            curses.mousemask(-1)
        # curses.mouseinterval(0)
        self.screen.clear()

        # screen color
        self.is_color_supported: bool = False
        try:
            curses.use_default_colors()
            curses.init_pair(1, self.setting.DefaultColorFG, self.setting.DefaultColorBG)
            curses.init_pair(2, self.setting.DarkColorFG, self.setting.DarkColorBG)
            curses.init_pair(3, self.setting.LightColorFG, self.setting.LightColorBG)
            self.screen.bkgd(curses.color_pair(1))
            self.is_color_supported = True
        except:
            self.is_color_supported = False

        # show loader and start heavy resources processes
        self.show_loader(subtext="initalizing ebook")

        # main ebook object
        self.ebook = ebook
        try:
            self.ebook.initialize()
        except (KeyboardInterrupt, Exception) as e:
            self.ebook.cleanup()
            if DEBUG:
                raise e
            else:
                sys.exit("ERROR: Badly-structured ebook.\n" + str(e))

        # state
        self.state = state

        # page scroll animation
        self.page_animation: Optional[Direction] = None

        # show reading progress
        self.show_reading_progress: bool = self.setting.ShowProgressIndicator
        self.reading_progress: Optional[float] = None  # calculate after count_letters()

        # search storage
        self.search_data: Optional[SearchData] = None

        # double spread
        self.spread = 2 if self.setting.StartWithDoubleSpread else 1

        # jumps marker container
        self.jump_list: Dict[str, ReadingState] = dict()

        # TTS speaker utils
        self._tts_speaker: Optional[SpeakerBaseModel] = construct_speaker(
            self.setting.PreferredTTSEngine, self.setting.TTSEngineArgs
        )
        self.tts_support: bool = bool(self._tts_speaker)
        self.is_speaking: bool = False

        # multi process & progress percentage
        self._multiprocess_support: bool = False if multiprocessing.cpu_count() == 1 else True
        self._process_counting_letter: Optional[multiprocessing.Process] = None
        self.letters_count: Optional[LettersCount] = None

    def run_counting_letters(self):
        if self._multiprocess_support:
            try:
                self._proc_parent, self._proc_child = multiprocessing.Pipe()
                self._process_counting_letter = multiprocessing.Process(
                    name="epy-subprocess-counting-letters",
                    target=count_letters_parallel,
                    args=(self.ebook, self._proc_child),
                )
                # forking will raise
                # zlib.error: Error -3 while decompressing data: invalid distance too far back
                self._process_counting_letter.start()
            except Exception as e:
                if DEBUG:
                    raise e
                self._multiprocess_support = False
        if not self._multiprocess_support:
            self.letters_count = count_letters(self.ebook)

    def try_assign_letters_count(self, *, force_wait=False) -> None:
        if isinstance(self._process_counting_letter, multiprocessing.Process):
            if force_wait and self._process_counting_letter.is_alive():
                self._process_counting_letter.join()

            if self._process_counting_letter.exitcode == 0:
                self.letters_count = self._proc_parent.recv()
                self._proc_parent.close()
                self._process_counting_letter.terminate()
                self._process_counting_letter.close()
                self._process_counting_letter = None

    def calculate_reading_progress(
        self, letters_per_content: List[int], reading_state: ReadingState
    ) -> None:
        if self.letters_count:
            self.reading_progress = (
                self.letters_count.cumulative[reading_state.content_index]
                + sum(
                    letters_per_content[: reading_state.row + (self.screen_rows * self.spread) - 1]
                )
            ) / self.letters_count.all

    @property
    def screen_rows(self) -> int:
        return self.screen.getmaxyx()[0]

    @property
    def screen_cols(self) -> int:
        return self.screen.getmaxyx()[1]

    @property
    def ext_dict_app(self) -> Optional[str]:
        self._ext_dict_app: Optional[str] = None

        if shutil.which(self.setting.DictionaryClient.split()[0]):
            self._ext_dict_app = self.setting.DictionaryClient
        else:
            for i in DICT_PRESET_LIST:
                if shutil.which(i) is not None:
                    self._ext_dict_app = i
                    break
            if self._ext_dict_app in {"sdcv"}:
                self._ext_dict_app += " -n"

        return self._ext_dict_app

    @property
    def image_viewer(self) -> Optional[str]:
        self._image_viewer: Optional[str] = None

        if shutil.which(self.setting.DefaultViewer.split()[0]) is not None:
            self._image_viewer = self.setting.DefaultViewer
        elif sys.platform == "win32":
            self._image_viewer = "start"
        elif sys.platform == "darwin":
            self._image_viewer = "open"
        else:
            for i in VIEWER_PRESET_LIST:
                if shutil.which(i) is not None:
                    self._image_viewer = i
                    break

        if self._image_viewer in {"gio"}:
            self._image_viewer += " open"

        return self._image_viewer

    def open_image(self, pad, name, bstr):
        sfx = os.path.splitext(name)[1]
        fd, path = tempfile.mkstemp(suffix=sfx)
        try:
            with os.fdopen(fd, "wb") as tmp:
                # tmp.write(epub.file.read(src))
                tmp.write(bstr)
            # run(VWR + " " + path, shell=True)
            subprocess.call(
                self.image_viewer + " " + path,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            k = pad.getch()
        finally:
            os.remove(path)
        return k

    def show_loader(self, *, loader_str: str = "\u231B", subtext: Optional[str] = None):
        self.screen.clear()
        rows, cols = self.screen.getmaxyx()
        middle_row = (rows - 1) // 2
        self.screen.addstr(middle_row, 0, loader_str.center(cols))
        if subtext:
            self.screen.addstr(middle_row + 1, 0, subtext.center(cols))
        # self.screen.addstr(((rows-2)//2)+1, (cols-len(msg))//2, msg)
        self.screen.refresh()

    @choice_win(True)
    def show_win_options(self, title, options, active_index, key_set):
        return title, options, active_index, key_set

    @text_win
    def show_win_error(self, title, msg, key):
        return title, msg, key

    @choice_win()
    def toc(self, toc_entries: Tuple[TocEntry, ...], index: int):
        return (
            "Table of Contents",
            [i.label for i in toc_entries],
            index,
            self.keymap.TableOfContents,
        )

    @text_win
    def show_win_metadata(self):
        if os.path.isfile(self.ebook.path):
            mdata = "[File Info]\nPATH: {}\nSIZE: {} MB\n \n[Book Info]\n".format(
                self.ebook.path, round(os.path.getsize(self.ebook.path) / 1024 ** 2, 2)
            )
        else:
            mdata = "[File Info]\nPATH: {}\n \n[Book Info]\n".format(self.ebook.path)

        book_metadata = self.ebook.get_meta()
        for field in dataclasses.fields(book_metadata):
            value = getattr(book_metadata, field.name)
            if value:
                value = unescape(re.sub("<[^>]*>", "", value))
                mdata += f"{field.name.title()}: {value}\n"

        return "Metadata", mdata, self.keymap.Metadata

    @text_win
    def show_win_help(self):
        src = "Key Bindings:\n"
        dig = max([len(i) for i in self.keymap_user_dict.values()]) + 2
        for i in self.keymap_user_dict.keys():
            src += "{}  {}\n".format(
                self.keymap_user_dict[i].rjust(dig), " ".join(re.findall("[A-Z][^A-Z]*", i))
            )
        return "Help", src, self.keymap.Help

    @text_win
    def define_word(self, word):
        rows, cols = self.screen.getmaxyx()
        hi, wi = 5, 16
        Y, X = (rows - hi) // 2, (cols - wi) // 2

        p = subprocess.Popen(
            "{} {}".format(self.ext_dict_app, word),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )

        dictwin = curses.newwin(hi, wi, Y, X)
        dictwin.box()
        dictwin.addstr((hi - 1) // 2, (wi - 10) // 2, "Loading...")
        dictwin.refresh()

        out, err = p.communicate()

        dictwin.clear()
        dictwin.refresh()

        if err == b"":
            return "Definition: " + word.upper(), out.decode(), self.keymap.DefineWord
        else:
            return "Error: " + self.ext_dict_app, err.decode(), self.keymap.DefineWord

    def show_win_choices_bookmarks(self):
        idx = 0
        while True:
            bookmarks = [i[0] for i in self.state.get_bookmarks(self.ebook)]
            if not bookmarks:
                return self.keymap.ShowBookmarks[0], None

            retk, idx, todel = self.show_win_options(
                "Bookmarks", bookmarks, idx, self.keymap.ShowBookmarks
            )
            if todel is not None:
                self.state.delete_bookmark(self.ebook, bookmarks[todel])
            else:
                return retk, idx

    def show_win_library(self):
        while True:
            library_items = self.state.get_from_history()
            if not library_items:
                return self.keymap.Library[0], None

            retk, choice_index, todel_index = self.show_win_options(
                "Library", [str(item) for item in library_items], 0, self.keymap.Library
            )
            if todel_index is not None:
                self.state.delete_from_library(library_items[todel_index].filepath)
            else:
                return retk, choice_index

    def input_prompt(self, prompt: str) -> Union[NoUpdate, Key, str]:
        """
        :param prompt: prompt text
        :return: NoUpdate if cancelled or interrupted
                 Key if curses.KEY_RESIZE triggered
                 str for successful input
        """
        # prevent pad hole when prompting for input while
        # other window is active
        # pad.refresh(y, 0, 0, x, rows-2, x+width)
        rows, cols = self.screen.getmaxyx()
        stat = curses.newwin(1, cols, rows - 1, 0)
        if self.is_color_supported:
            stat.bkgd(self.screen.getbkgd())
        stat.keypad(True)
        curses.echo(True)
        safe_curs_set(2)

        init_text = ""

        stat.addstr(0, 0, prompt, curses.A_REVERSE)
        stat.addstr(0, len(prompt), init_text)
        stat.refresh()

        try:
            while True:
                # NOTE: getch() only handles ascii
                # to handle wide char like: é, use get_wch()
                ipt = Key(stat.get_wch())
                # get_wch() return ambiguous type
                # str for string input but int for function or special keys
                # if type(ipt) == str:
                #     ipt = ord(ipt)

                if ipt == Key(27):
                    stat.clear()
                    stat.refresh()
                    curses.echo(False)
                    safe_curs_set(0)
                    return NoUpdate()
                elif ipt == Key(10):
                    stat.clear()
                    stat.refresh()
                    curses.echo(False)
                    safe_curs_set(0)
                    return init_text
                elif ipt in (Key(8), Key(127), Key(curses.KEY_BACKSPACE)):
                    init_text = init_text[:-1]
                elif ipt == Key(curses.KEY_RESIZE):
                    stat.clear()
                    stat.refresh()
                    curses.echo(False)
                    safe_curs_set(0)
                    return Key(curses.KEY_RESIZE)
                # elif len(init_text) <= maxlen:
                else:
                    init_text += ipt.char

                stat.clear()
                stat.addstr(0, 0, prompt, curses.A_REVERSE)
                stat.addstr(
                    0,
                    len(prompt),
                    init_text
                    if len(prompt + init_text) < cols
                    else "..." + init_text[len(prompt) - cols + 4 :],
                )
                stat.refresh()
        except KeyboardInterrupt:
            stat.clear()
            stat.refresh()
            curses.echo(False)
            safe_curs_set(0)
            return NoUpdate()

    def searching(
        self, board: InfiniBoard, src: Sequence[str], reading_state: ReadingState, tot
    ) -> Union[NoUpdate, ReadingState, Key]:
        # reusable loop indices
        i: Any
        j: Any

        rows, cols = self.screen.getmaxyx()
        # unnecessary
        # if self.spread == 2:
        #     reading_state = dataclasses.replace(reading_state, textwidth=(cols - 7) // 2)

        x = (cols - reading_state.textwidth) // 2
        if self.spread == 1:
            x = (cols - reading_state.textwidth) // 2
        else:
            x = 2

        if not self.search_data:
            candidate_text = self.input_prompt(" Regex:")
            # if isinstance(candidate_text, str) and candidate_text != "":
            if isinstance(candidate_text, str) and candidate_text:
                self.search_data = SearchData(value=candidate_text)
            else:
                assert isinstance(candidate_text, NoUpdate) or isinstance(candidate_text, Key)
                return candidate_text

        found = []
        try:
            pattern = re.compile(self.search_data.value, re.IGNORECASE)
        except re.error as reerrmsg:
            self.search_data = None
            tmpk = self.show_win_error("!Regex Error", str(reerrmsg), tuple())
            return tmpk

        for n, i in enumerate(src):
            for j in pattern.finditer(i):
                found.append([n, j.span()[0], j.span()[1] - j.span()[0]])

        if not found:
            if (
                self.search_data.direction == Direction.FORWARD
                and reading_state.content_index + 1 < tot
            ):
                return ReadingState(
                    content_index=reading_state.content_index + 1,
                    textwidth=reading_state.textwidth,
                    row=0,
                )
            elif (
                self.search_data.direction == Direction.BACKWARD and reading_state.content_index > 0
            ):
                return ReadingState(
                    content_index=reading_state.content_index - 1,
                    textwidth=reading_state.textwidth,
                    row=0,
                )
            else:
                s: Union[NoUpdate, Key] = NoUpdate()
                while True:
                    if s in self.keymap.Quit:
                        self.search_data = None
                        self.screen.clear()
                        self.screen.refresh()
                        return reading_state
                    # TODO: maybe >= 0?
                    elif s == Key("n") and reading_state.content_index == 0:
                        self.search_data = dataclasses.replace(
                            self.search_data, direction=Direction.FORWARD
                        )
                        return ReadingState(
                            content_index=reading_state.content_index + 1,
                            textwidth=reading_state.textwidth,
                            row=0,
                        )
                    elif s == Key("N") and reading_state.content_index + 1 == tot:
                        self.search_data = dataclasses.replace(
                            self.search_data, direction=Direction.BACKWARD
                        )
                        return ReadingState(
                            content_index=reading_state.content_index - 1,
                            textwidth=reading_state.textwidth,
                            row=0,
                        )

                    self.screen.clear()
                    self.screen.addstr(
                        rows - 1,
                        0,
                        " Finished searching: " + self.search_data.value[: cols - 22] + " ",
                        curses.A_REVERSE,
                    )
                    board.write(reading_state.row, 1)
                    self.screen.refresh()
                    s = board.getch()

        sidx = len(found) - 1
        if self.search_data.direction == Direction.FORWARD:
            if reading_state.row > found[-1][0]:
                return ReadingState(
                    content_index=reading_state.content_index + 1,
                    textwidth=reading_state.textwidth,
                    row=0,
                )
            for n, i in enumerate(found):
                if i[0] >= reading_state.row:
                    sidx = n
                    break

        s = NoUpdate()
        msg = (
            " Searching: "
            + self.search_data.value
            + " --- Res {}/{} Ch {}/{} ".format(
                sidx + 1, len(found), reading_state.content_index + 1, tot
            )
        )
        while True:
            if s in self.keymap.Quit:
                self.search_data = None
                # for i in found:
                #     pad.chgat(i[0], i[1], i[2], pad.getbkgd())
                board.feed_temporary_style()
                # pad.format()
                # self.screen.clear()
                # self.screen.refresh()
                return reading_state
            elif s == Key("n"):
                self.search_data = dataclasses.replace(
                    self.search_data, direction=Direction.FORWARD
                )
                if sidx == len(found) - 1:
                    if reading_state.content_index + 1 < tot:
                        return ReadingState(
                            content_index=reading_state.content_index + 1,
                            textwidth=reading_state.textwidth,
                            row=0,
                        )
                    else:
                        s = NoUpdate()
                        msg = " Finished searching: " + self.search_data.value + " "
                        continue
                else:
                    sidx += 1
                    msg = (
                        " Searching: "
                        + self.search_data.value
                        + " --- Res {}/{} Ch {}/{} ".format(
                            sidx + 1, len(found), reading_state.content_index + 1, tot
                        )
                    )
            elif s == Key("N"):
                self.search_data = dataclasses.replace(
                    self.search_data, direction=Direction.BACKWARD
                )
                if sidx == 0:
                    if reading_state.content_index > 0:
                        return ReadingState(
                            content_index=reading_state.content_index - 1,
                            textwidth=reading_state.textwidth,
                            row=0,
                        )
                    else:
                        s = NoUpdate()
                        msg = " Finished searching: " + self.search_data.value + " "
                        continue
                else:
                    sidx -= 1
                    msg = (
                        " Searching: "
                        + self.search_data.value
                        + " --- Res {}/{} Ch {}/{} ".format(
                            sidx + 1, len(found), reading_state.content_index + 1, tot
                        )
                    )
            elif s == Key(curses.KEY_RESIZE):
                return Key(curses.KEY_RESIZE)

            # if reading_state.row + rows - 1 > pad.chunks[pad.find_chunkidx(reading_state.row)]:
            #     reading_state = dataclasses.replace(
            #         reading_state, row=pad.chunks[pad.find_chunkidx(reading_state.row)] + 1
            #     )

            while found[sidx][0] not in list(
                range(reading_state.row, reading_state.row + (rows - 1) * self.spread)
            ):
                if found[sidx][0] > reading_state.row:
                    reading_state = dataclasses.replace(
                        reading_state, row=reading_state.row + ((rows - 1) * self.spread)
                    )
                else:
                    reading_state = dataclasses.replace(
                        reading_state, row=reading_state.row - ((rows - 1) * self.spread)
                    )
                    if reading_state.row < 0:
                        reading_state = dataclasses.replace(reading_state, row=0)

            # formats = [InlineStyle(row=i[0], col=i[1], n_letters=i[2], attr=curses.A_REVERSE) for i in found]
            # pad.feed_style(formats)
            styles: List[InlineStyle] = []
            for n, i in enumerate(found):
                attr = curses.A_REVERSE if n == sidx else curses.A_NORMAL
                # pad.chgat(i[0], i[1], i[2], pad.getbkgd() | attr)
                styles.append(
                    InlineStyle(row=i[0], col=i[1], n_letters=i[2], attr=board.getbkgd() | attr)
                )
            board.feed_temporary_style(tuple(styles))

            self.screen.clear()
            self.screen.addstr(rows - 1, 0, msg, curses.A_REVERSE)
            self.screen.refresh()
            # pad.refresh(reading_state.row, 0, 0, x, rows - 2, x + reading_state.textwidth)
            board.write(reading_state.row, 1)
            s = board.getch()

    def speaking(self, text):
        self.is_speaking = True
        self.screen.addstr(self.screen_rows - 1, 0, " Speaking! ", curses.A_REVERSE)
        self.screen.refresh()
        self.screen.timeout(1)
        try:
            self._tts_speaker.speak(text)

            while True:
                if self._tts_speaker.is_done():
                    k = self.keymap.PageDown[0]
                    break
                tmp = self.screen.getch()
                k = NoUpdate() if tmp == -1 else Key(tmp)
                if k == Key(curses.KEY_MOUSE):
                    mouse_event = curses.getmouse()
                    if mouse_event[4] == curses.BUTTON2_CLICKED:
                        k = self.keymap.Quit[0]
                    elif mouse_event[4] == curses.BUTTON1_CLICKED:
                        if mouse_event[1] < self.screen_cols // 2:
                            k = self.keymap.PageUp[0]
                        else:
                            k = self.keymap.PageDown[0]
                    elif mouse_event[4] == curses.BUTTON4_PRESSED:
                        k = self.keymap.ScrollUp[0]
                    elif mouse_event[4] == 2097152:
                        k = self.keymap.ScrollDown[0]
                if (
                    k
                    in self.keymap.Quit
                    + self.keymap.PageUp
                    + self.keymap.PageDown
                    + self.keymap.ScrollUp
                    + self.keymap.ScrollDown
                    + (curses.KEY_RESIZE,)
                ):
                    self._tts_speaker.stop()
                    break
        finally:
            self.screen.timeout(-1)
            self._tts_speaker.cleanup()

        if k in self.keymap.Quit:
            self.is_speaking = False
            k = NoUpdate()
        return k

    def savestate(self, reading_state: ReadingState) -> None:
        if self.seamless:
            reading_state = self.convert_absolute_reading_state_to_relative(reading_state)
        self.state.set_last_reading_state(self.ebook, reading_state)
        self.state.update_library(self.ebook, self.reading_progress)

    def cleanup(self) -> None:
        self.ebook.cleanup()

        if isinstance(self._process_counting_letter, multiprocessing.Process):
            if self._process_counting_letter.is_alive():
                self._process_counting_letter.terminate()
                # weird python multiprocessing issue, need to call .join() before .close()
                # ValueError: Cannot close a process while it is still running.
                # You should first call join() or terminate().
                self._process_counting_letter.join()
                self._process_counting_letter.close()

    def convert_absolute_reading_state_to_relative(self, reading_state) -> ReadingState:
        if not self.seamless:
            raise RuntimeError(
                "Reader.convert_absolute_reading_state_to_relative() only implemented when Seamless=True"
            )
        return construct_relative_reading_state(reading_state, self.totlines_per_content)

    def convert_relative_reading_state_to_absolute(
        self, reading_state: ReadingState
    ) -> ReadingState:
        if not self.seamless:
            raise RuntimeError(
                "Reader.convert_relative_reading_state_to_absolute() only implemented when Seamless=True"
            )

        absolute_row = reading_state.row + sum(
            self.totlines_per_content[: reading_state.content_index]
        )
        absolute_pctg = (
            absolute_row / sum(self.totlines_per_content) if reading_state.rel_pctg else None
        )

        return dataclasses.replace(
            reading_state, content_index=0, row=absolute_row, rel_pctg=absolute_pctg
        )

    def get_all_book_contents(
        self, reading_state: ReadingState
    ) -> Tuple[TextStructure, Tuple[TocEntry, ...], Union[Tuple[str, ...], Tuple[ET.Element, ...]]]:
        if not self.seamless:
            raise RuntimeError("Reader.get_all_book_contents() only implemented when Seamless=True")

        contents = self.ebook.contents
        toc_entries = self.ebook.toc_entries

        text_structure: TextStructure = TextStructure(
            text_lines=tuple(), image_maps=dict(), section_rows=dict(), formatting=tuple()
        )
        toc_entries_tmp: List[TocEntry] = []
        section_rows_tmp: Dict[str, int] = dict()

        # self.totlines_per_content only defined when Seamless=True
        self.totlines_per_content: Tuple[int, ...] = tuple()

        for n, content in enumerate(contents):
            self.show_loader(subtext=f"loading contents ({n+1}/{len(contents)})")
            starting_line = sum(self.totlines_per_content)
            assert isinstance(content, str) or isinstance(content, ET.Element)
            text_structure_tmp = parse_html(
                self.ebook.get_raw_text(content),
                textwidth=reading_state.textwidth,
                section_ids=set(toc_entry.section for toc_entry in toc_entries),  # type: ignore
                starting_line=starting_line,
            )
            assert isinstance(text_structure_tmp, TextStructure)
            # self.totlines_per_content.append(len(text_structure_tmp.text_lines))
            self.totlines_per_content += (len(text_structure_tmp.text_lines),)

            for toc_entry in toc_entries:
                if toc_entry.content_index == n:
                    if toc_entry.section:
                        toc_entries_tmp.append(dataclasses.replace(toc_entry, content_index=0))
                    else:
                        section_id_tmp = str(uuid.uuid4())
                        toc_entries_tmp.append(
                            TocEntry(label=toc_entry.label, content_index=0, section=section_id_tmp)
                        )
                        section_rows_tmp[section_id_tmp] = starting_line

            text_structure = merge_text_structures(text_structure, text_structure_tmp)

        text_structure = dataclasses.replace(
            text_structure, section_rows={**text_structure.section_rows, **section_rows_tmp}
        )

        return text_structure, tuple(toc_entries_tmp), (self.ebook.contents[0],)

    def get_current_book_content(
        self, reading_state: ReadingState
    ) -> Tuple[TextStructure, Tuple[TocEntry, ...], Union[Tuple[str, ...], Tuple[ET.Element, ...]]]:
        contents = self.ebook.contents
        toc_entries = self.ebook.toc_entries
        content_path = contents[reading_state.content_index]
        content = self.ebook.get_raw_text(content_path)
        text_structure = parse_html(  # type: ignore
            content,
            textwidth=reading_state.textwidth,
            section_ids=set(toc_entry.section for toc_entry in toc_entries),  # type: ignore
        )
        return text_structure, toc_entries, contents

    def read(self, reading_state: ReadingState) -> Union[ReadingState, Ebook]:
        # reusable loop indices
        i: Any

        k = self.keymap.RegexSearch[0] if self.search_data else NoUpdate()
        rows, cols = self.screen.getmaxyx()

        mincols_doublespr = (
            DoubleSpreadPadding.LEFT.value
            + 22
            + DoubleSpreadPadding.MIDDLE.value
            + 22
            + DoubleSpreadPadding.RIGHT.value
        )
        if cols < mincols_doublespr:
            self.spread = 1
        if self.spread == 2:
            reading_state = dataclasses.replace(
                reading_state,
                textwidth=(
                    cols
                    - sum(
                        [
                            DoubleSpreadPadding.LEFT.value,
                            DoubleSpreadPadding.MIDDLE.value,
                            DoubleSpreadPadding.RIGHT.value,
                        ]
                    )
                )
                // 2,
            )
        x = (cols - reading_state.textwidth) // 2
        if self.spread == 2:
            x = DoubleSpreadPadding.LEFT.value

        self.show_loader(subtext="loading contents")
        # get text structure, toc entries and contents of the book
        if self.seamless:
            text_structure, toc_entries, contents = self.get_all_book_contents(reading_state)
            # adjustment
            reading_state = self.convert_relative_reading_state_to_absolute(reading_state)
        else:
            text_structure, toc_entries, contents = self.get_current_book_content(reading_state)

        totlines = len(text_structure.text_lines)

        if reading_state.row < 0 and totlines <= rows * self.spread:
            reading_state = dataclasses.replace(reading_state, row=0)
        elif reading_state.rel_pctg is not None:
            reading_state = dataclasses.replace(
                reading_state, row=round(reading_state.rel_pctg * totlines)
            )
        else:
            reading_state = dataclasses.replace(reading_state, row=reading_state.row % totlines)

        board = InfiniBoard(
            screen=self.screen,
            text=text_structure.text_lines,
            textwidth=reading_state.textwidth,
            default_style=text_structure.formatting,
            spread=self.spread,
        )

        letters_per_content: List[int] = []
        for i in text_structure.text_lines:
            letters_per_content.append(len(re.sub(r"\s", "", i)))

        self.screen.clear()
        self.screen.refresh()
        # try-except clause if there is issue
        # with curses resize event
        board.write(reading_state.row)

        # if reading_state.section is not None
        # then override reading_state.row to follow the section
        if reading_state.section:
            reading_state = dataclasses.replace(
                reading_state, row=text_structure.section_rows.get(reading_state.section, 0)
            )

        checkpoint_row: Optional[int] = None
        countstring = ""

        try:
            while True:
                if countstring == "":
                    count = 1
                else:
                    count = int(countstring)
                if k in tuple(Key(i) for i in range(48, 58)):  # i.e., k is a numeral
                    countstring = countstring + k.char
                else:
                    if k in self.keymap.Quit:
                        if k == Key(27) and countstring != "":
                            countstring = ""
                        else:
                            self.try_assign_letters_count(force_wait=True)
                            self.calculate_reading_progress(letters_per_content, reading_state)

                            self.savestate(
                                dataclasses.replace(
                                    reading_state, rel_pctg=reading_state.row / totlines
                                )
                            )
                            sys.exit()

                    elif k in self.keymap.TTSToggle and self.tts_support:
                        tospeak = ""
                        for i in text_structure.text_lines[
                            reading_state.row : reading_state.row + (rows * self.spread)
                        ]:
                            if re.match(r"^\s*$", i) is not None:
                                tospeak += "\n. \n"
                            else:
                                tospeak += i + " "
                        k = self.speaking(tospeak)
                        if (
                            totlines - reading_state.row <= rows
                            and reading_state.content_index == len(contents) - 1
                        ):
                            self.is_speaking = False
                        continue

                    elif k in self.keymap.DoubleSpreadToggle:
                        if cols < mincols_doublespr:
                            k = self.show_win_error(
                                "Screen is too small",
                                "Min: {} cols x {} rows".format(mincols_doublespr, 12),
                                (Key("D"),),
                            )
                        self.spread = (self.spread % 2) + 1
                        return ReadingState(
                            content_index=reading_state.content_index,
                            textwidth=reading_state.textwidth,
                            row=reading_state.row,
                            rel_pctg=reading_state.row / totlines,
                        )

                    elif k in self.keymap.ScrollUp:
                        if self.spread == 2:
                            k = self.keymap.PageUp[0]
                            continue
                        if count > 1:
                            checkpoint_row = reading_state.row - 1
                        if reading_state.row >= count:
                            reading_state = dataclasses.replace(
                                reading_state, row=reading_state.row - count
                            )
                        elif reading_state.row == 0 and reading_state.content_index != 0:
                            self.page_animation = Direction.BACKWARD
                            # return -1, width, -rows, None, ""
                            return ReadingState(
                                content_index=reading_state.content_index - 1,
                                textwidth=reading_state.textwidth,
                                row=-rows,
                            )
                        else:
                            reading_state = dataclasses.replace(reading_state, row=0)

                    elif k in self.keymap.PageUp:
                        if reading_state.row == 0 and reading_state.content_index != 0:
                            self.page_animation = Direction.BACKWARD
                            text_structure_content_before = parse_html(
                                self.ebook.get_raw_text(contents[reading_state.content_index - 1]),
                                textwidth=reading_state.textwidth,
                            )
                            assert isinstance(text_structure_content_before, TextStructure)
                            return ReadingState(
                                content_index=reading_state.content_index - 1,
                                textwidth=reading_state.textwidth,
                                row=rows
                                * self.spread
                                * (
                                    len(text_structure_content_before.text_lines)
                                    // (rows * self.spread)
                                ),
                            )
                        else:
                            if reading_state.row >= rows * self.spread * count:
                                self.page_animation = Direction.BACKWARD
                                reading_state = dataclasses.replace(
                                    reading_state,
                                    row=reading_state.row - (rows * self.spread * count),
                                )
                            else:
                                reading_state = dataclasses.replace(reading_state, row=0)

                    elif k in self.keymap.ScrollDown:
                        if self.spread == 2:
                            k = self.keymap.PageDown[0]
                            continue
                        if count > 1:
                            checkpoint_row = reading_state.row + rows - 1
                        if reading_state.row + count <= totlines - rows:
                            reading_state = dataclasses.replace(
                                reading_state, row=reading_state.row + count
                            )
                        elif (
                            reading_state.row >= totlines - rows
                            and reading_state.content_index != len(contents) - 1
                        ):
                            self.page_animation = Direction.FORWARD
                            return ReadingState(
                                content_index=reading_state.content_index + 1,
                                textwidth=reading_state.textwidth,
                                row=0,
                            )

                    elif k in self.keymap.PageDown:
                        if totlines - reading_state.row > rows * self.spread:
                            self.page_animation = Direction.FORWARD
                            reading_state = dataclasses.replace(
                                reading_state, row=reading_state.row + (rows * self.spread)
                            )
                        elif reading_state.content_index != len(contents) - 1:
                            self.page_animation = Direction.FORWARD
                            return ReadingState(
                                content_index=reading_state.content_index + 1,
                                textwidth=reading_state.textwidth,
                                row=0,
                            )

                    # elif k in K["HalfScreenUp"] | K["HalfScreenDown"]:
                    #     countstring = str(rows // 2)
                    #     k = list(K["ScrollUp" if k in K["HalfScreenUp"] else "ScrollDown"])[0]
                    #     continue

                    elif k in self.keymap.NextChapter:
                        ntoc = find_current_content_index(
                            toc_entries,
                            text_structure.section_rows,
                            reading_state.content_index,
                            reading_state.row,
                        )
                        if ntoc < len(toc_entries) - 1:
                            if reading_state.content_index == toc_entries[ntoc + 1].content_index:
                                try:
                                    reading_state = dataclasses.replace(
                                        reading_state,
                                        row=text_structure.section_rows[
                                            toc_entries[ntoc + 1].section  # type: ignore
                                        ],
                                    )
                                except KeyError:
                                    pass
                            else:
                                return ReadingState(
                                    content_index=toc_entries[ntoc + 1].content_index,
                                    textwidth=reading_state.textwidth,
                                    row=0,
                                    section=toc_entries[ntoc + 1].section,
                                )

                    elif k in self.keymap.PrevChapter:
                        ntoc = find_current_content_index(
                            toc_entries,
                            text_structure.section_rows,
                            reading_state.content_index,
                            reading_state.row,
                        )
                        if ntoc > 0:
                            if reading_state.content_index == toc_entries[ntoc - 1].content_index:
                                reading_state = dataclasses.replace(
                                    reading_state,
                                    row=text_structure.section_rows.get(
                                        toc_entries[ntoc - 1].section, 0  # type: ignore
                                    ),
                                )
                            else:
                                return ReadingState(
                                    content_index=toc_entries[ntoc - 1].content_index,
                                    textwidth=reading_state.textwidth,
                                    row=0,
                                    section=toc_entries[ntoc - 1].section,
                                )

                    elif k in self.keymap.BeginningOfCh:
                        ntoc = find_current_content_index(
                            toc_entries,
                            text_structure.section_rows,
                            reading_state.content_index,
                            reading_state.row,
                        )
                        try:
                            reading_state = dataclasses.replace(
                                reading_state,
                                row=text_structure.section_rows[toc_entries[ntoc].section],  # type: ignore
                            )
                        except (KeyError, IndexError):
                            reading_state = dataclasses.replace(reading_state, row=0)

                    elif k in self.keymap.EndOfCh:
                        ntoc = find_current_content_index(
                            toc_entries,
                            text_structure.section_rows,
                            reading_state.content_index,
                            reading_state.row,
                        )
                        try:
                            if (
                                text_structure.section_rows[toc_entries[ntoc + 1].section] - rows  # type: ignore
                                >= 0
                            ):
                                reading_state = dataclasses.replace(
                                    reading_state,
                                    row=text_structure.section_rows[toc_entries[ntoc + 1].section]  # type: ignore
                                    - rows,
                                )
                            else:
                                reading_state = dataclasses.replace(
                                    reading_state,
                                    row=text_structure.section_rows[toc_entries[ntoc].section],  # type: ignore
                                )
                        except (KeyError, IndexError):
                            reading_state = dataclasses.replace(
                                reading_state, row=pgend(totlines, rows)
                            )

                    elif k in self.keymap.TableOfContents:
                        if not toc_entries:
                            k = self.show_win_error(
                                "Table of Contents",
                                "N/A: TableOfContents is unavailable for this book.",
                                self.keymap.TableOfContents,
                            )
                            continue
                        ntoc = find_current_content_index(
                            toc_entries,
                            text_structure.section_rows,
                            reading_state.content_index,
                            reading_state.row,
                        )
                        rettock, fllwd, _ = self.toc(toc_entries, ntoc)
                        if rettock is not None:  # and rettock in WINKEYS:
                            k = rettock
                            continue
                        elif fllwd is not None:
                            if reading_state.content_index == toc_entries[fllwd].content_index:
                                try:
                                    reading_state = dataclasses.replace(
                                        reading_state,
                                        row=text_structure.section_rows[toc_entries[fllwd].section],
                                    )
                                except KeyError:
                                    reading_state = dataclasses.replace(reading_state, row=0)
                            else:
                                return ReadingState(
                                    content_index=toc_entries[fllwd].content_index,
                                    textwidth=reading_state.textwidth,
                                    row=0,
                                    section=toc_entries[fllwd].section,
                                )

                    elif k in self.keymap.Metadata:
                        k = self.show_win_metadata()
                        if k in self._win_keys:
                            continue

                    elif k in self.keymap.Help:
                        k = self.show_win_help()
                        if k in self._win_keys:
                            continue

                    elif (
                        k in self.keymap.Enlarge
                        and (reading_state.textwidth + count) < cols - 4
                        and self.spread == 1
                    ):
                        return dataclasses.replace(
                            reading_state,
                            textwidth=reading_state.textwidth + count,
                            rel_pctg=reading_state.row / totlines,
                        )

                    elif (
                        k in self.keymap.Shrink
                        and reading_state.textwidth >= 22
                        and self.spread == 1
                    ):
                        return dataclasses.replace(
                            reading_state,
                            textwidth=reading_state.textwidth - count,
                            rel_pctg=reading_state.row / totlines,
                        )

                    elif k in self.keymap.SetWidth and self.spread == 1:
                        if countstring == "":
                            # if called without a count, toggle between 80 cols and full width
                            if reading_state.textwidth != 80 and cols - 4 >= 80:
                                return ReadingState(
                                    content_index=reading_state.content_index,
                                    textwidth=80,
                                    row=reading_state.row,
                                    rel_pctg=reading_state.row / totlines,
                                )
                            else:
                                return ReadingState(
                                    content_index=reading_state.content_index,
                                    textwidth=cols - 4,
                                    row=reading_state.row,
                                    rel_pctg=reading_state.row / totlines,
                                )
                        else:
                            reading_state = dataclasses.replace(reading_state, textwidth=count)
                        if reading_state.textwidth < 20:
                            reading_state = dataclasses.replace(reading_state, textwidth=20)
                        elif reading_state.textwidth >= cols - 4:
                            reading_state = dataclasses.replace(reading_state, textwidth=cols - 4)

                        return ReadingState(
                            content_index=reading_state.content_index,
                            textwidth=reading_state.textwidth,
                            row=reading_state.row,
                            rel_pctg=reading_state.row / totlines,
                        )

                    elif k in self.keymap.RegexSearch:
                        ret_object = self.searching(
                            board,
                            text_structure.text_lines,
                            reading_state,
                            len(contents),
                        )
                        if isinstance(ret_object, Key) or isinstance(ret_object, NoUpdate):
                            k = ret_object
                            # k = ret_object.value
                            continue
                        elif isinstance(ret_object, ReadingState) and self.search_data:
                            return ret_object
                        # else:
                        elif isinstance(ret_object, ReadingState):
                            # y = ret_object
                            reading_state = ret_object

                    elif k in self.keymap.OpenImage and self.image_viewer:
                        imgs_in_screen = list(
                            set(
                                range(reading_state.row, reading_state.row + rows * self.spread + 1)
                            )
                            & set(text_structure.image_maps.keys())
                        )
                        if not imgs_in_screen:
                            k = NoUpdate()
                            continue

                        imgs_in_screen.sort()
                        image_path: Optional[str] = None
                        if len(imgs_in_screen) == 1:
                            image_path = text_structure.image_maps[imgs_in_screen[0]]
                        elif len(imgs_in_screen) > 1:
                            imgs_rel_to_row = [i - reading_state.row for i in imgs_in_screen]
                            p: Union[NoUpdate, Key] = NoUpdate()
                            i = 0
                            while p not in self.keymap.Quit and p not in self.keymap.Follow:
                                self.screen.move(
                                    imgs_rel_to_row[i] % rows,
                                    (
                                        x
                                        if imgs_rel_to_row[i] // rows == 0
                                        else cols
                                        - DoubleSpreadPadding.RIGHT.value
                                        - reading_state.textwidth
                                    )
                                    + reading_state.textwidth // 2,
                                )
                                self.screen.refresh()
                                safe_curs_set(2)
                                p = board.getch()
                                if p in self.keymap.ScrollDown:
                                    i += 1
                                elif p in self.keymap.ScrollUp:
                                    i -= 1
                                i = i % len(imgs_rel_to_row)

                            safe_curs_set(0)
                            if p in self.keymap.Follow:
                                image_path = text_structure.image_maps[imgs_in_screen[i]]

                        if image_path:
                            try:
                                # if self.ebook.__class__.__name__ in {"Epub", "Mobi", "Azw"}:
                                if isinstance(self.ebook, (Epub, Mobi, Azw)):
                                    # self.seamless adjustment
                                    if self.seamless:
                                        current_content_index = (
                                            self.convert_absolute_reading_state_to_relative(
                                                reading_state
                                            ).content_index
                                        )
                                    else:
                                        current_content_index = reading_state.content_index
                                        # for n, content in enumerate(self.ebook.contents):
                                        #     content_path = content
                                        #     if reading_state.row < sum(totlines_per_content[:n]):
                                        #         break

                                    content_path = self.ebook.contents[current_content_index]
                                    assert isinstance(content_path, str)
                                    image_path = resolve_path(content_path, image_path)
                                imgnm, imgbstr = self.ebook.get_img_bytestr(image_path)
                                k = self.open_image(board, imgnm, imgbstr)
                                continue
                            except Exception as e:
                                self.show_win_error("Error Opening Image", str(e), tuple())
                                if DEBUG:
                                    raise e

                    elif (
                        k in self.keymap.SwitchColor
                        and self.is_color_supported
                        and countstring in {"", "0", "1", "2"}
                    ):
                        if countstring == "":
                            count_color = curses.pair_number(self.screen.getbkgd())
                            if count_color not in {2, 3}:
                                count_color = 1
                            count_color = count_color % 3
                        else:
                            count_color = count
                        self.screen.bkgd(curses.color_pair(count_color + 1))
                        # pad.format()
                        return ReadingState(
                            content_index=reading_state.content_index,
                            textwidth=reading_state.textwidth,
                            row=reading_state.row,
                        )

                    elif k in self.keymap.AddBookmark:
                        bmname = self.input_prompt(" Add bookmark:")
                        if isinstance(bmname, str) and bmname:
                            try:
                                self.state.insert_bookmark(
                                    self.ebook,
                                    bmname,
                                    dataclasses.replace(
                                        reading_state, rel_pctg=reading_state.row / totlines
                                    ),
                                )
                            except sqlite3.IntegrityError:
                                k = self.show_win_error(
                                    "Error: Add Bookmarks",
                                    f"Bookmark with name '{bmname}' already exists.",
                                    (Key("B"),),
                                )
                                continue
                        else:
                            k = bmname
                            continue

                    elif k in self.keymap.ShowBookmarks:
                        bookmarks = self.state.get_bookmarks(self.ebook)
                        if not bookmarks:
                            k = self.show_win_error(
                                "Bookmarks",
                                "N/A: Bookmarks are not found in this book.",
                                self.keymap.ShowBookmarks,
                            )
                            continue
                        else:
                            retk, idxchoice = self.show_win_choices_bookmarks()
                            if retk is not None:
                                k = retk
                                continue
                            elif idxchoice is not None:
                                bookmark_to_jump = self.state.get_bookmarks(self.ebook)[idxchoice][
                                    1
                                ]
                                if (
                                    bookmark_to_jump.content_index == reading_state.content_index
                                    and bookmark_to_jump.textwidth == reading_state.textwidth
                                ):
                                    reading_state = bookmark_to_jump
                                else:
                                    return ReadingState(
                                        content_index=bookmark_to_jump.content_index,
                                        textwidth=reading_state.textwidth,
                                        row=bookmark_to_jump.row,
                                        rel_pctg=bookmark_to_jump.rel_pctg,
                                    )

                    elif k in self.keymap.DefineWord and self.ext_dict_app:
                        word = self.input_prompt(" Define:")
                        if isinstance(word, str) and word:
                            defin = self.define_word(word)
                            if defin in self._win_keys:
                                k = defin
                                continue
                        else:
                            k = word
                            continue

                    elif k in self.keymap.MarkPosition:
                        jumnum = board.getch()
                        if isinstance(jumnum, Key) and jumnum in tuple(
                            Key(i) for i in range(48, 58)
                        ):
                            self.jump_list[jumnum.char] = reading_state
                        else:
                            k = NoUpdate()
                            continue

                    elif k in self.keymap.JumpToPosition:
                        jumnum = board.getch()
                        if (
                            isinstance(jumnum, Key)
                            and jumnum in tuple(Key(i) for i in range(48, 58))
                            and jumnum.char in self.jump_list
                        ):
                            marked_reading_state = self.jump_list[jumnum.char]
                            return dataclasses.replace(
                                marked_reading_state,
                                textwidth=reading_state.textwidth,
                                rel_pctg=None
                                if marked_reading_state.textwidth == reading_state.textwidth
                                else marked_reading_state.rel_pctg,
                                section="",
                            )
                        else:
                            k = NoUpdate()
                            continue

                    elif k in self.keymap.ShowHideProgress:
                        self.show_reading_progress = not self.show_reading_progress

                    elif k in self.keymap.Library:
                        self.try_assign_letters_count(force_wait=True)
                        self.calculate_reading_progress(letters_per_content, reading_state)

                        self.savestate(
                            dataclasses.replace(
                                reading_state, rel_pctg=reading_state.row / totlines
                            )
                        )
                        library_items = self.state.get_from_history()
                        if not library_items:
                            k = self.show_win_error(
                                "Library",
                                "N/A: No reading history.",
                                self.keymap.Library,
                            )
                            continue
                        else:
                            retk, choice_index = self.show_win_library()
                            if retk is not None:
                                k = retk
                                continue
                            elif choice_index is not None:
                                return get_ebook_obj(library_items[choice_index].filepath)

                    elif k == Key(curses.KEY_RESIZE):
                        self.savestate(
                            dataclasses.replace(
                                reading_state, rel_pctg=reading_state.row / totlines
                            )
                        )
                        # stated in pypi windows-curses page:
                        # to call resize_term right after KEY_RESIZE
                        if sys.platform == "win32":
                            curses.resize_term(rows, cols)
                            rows, cols = self.screen.getmaxyx()
                        else:
                            rows, cols = self.screen.getmaxyx()
                            curses.resize_term(rows, cols)
                        if cols < 22 or rows < 12:
                            sys.exit("ERROR: Screen was too small (min 22cols x 12rows).")
                        if cols <= reading_state.textwidth + 4:
                            return ReadingState(
                                content_index=reading_state.content_index,
                                textwidth=cols - 4,
                                row=reading_state.row,
                                rel_pctg=reading_state.row / totlines,
                            )
                        else:
                            return ReadingState(
                                content_index=reading_state.content_index,
                                textwidth=reading_state.textwidth,
                                row=reading_state.row,
                            )

                    countstring = ""

                if checkpoint_row:
                    board.feed_temporary_style(
                        (
                            InlineStyle(
                                row=checkpoint_row,
                                col=0,
                                n_letters=reading_state.textwidth,
                                attr=curses.A_UNDERLINE,
                            ),
                        )
                    )

                try:
                    if self.setting.PageScrollAnimation and self.page_animation:
                        self.screen.clear()
                        for i in range(1, reading_state.textwidth + 1):
                            curses.napms(1)
                            # self.screen.clear()
                            board.write_n(reading_state.row, i, self.page_animation)
                            self.screen.refresh()
                        self.page_animation = None

                    self.screen.clear()
                    self.screen.addstr(0, 0, countstring)
                    board.write(reading_state.row)

                    # check if letters counting process is done
                    self.try_assign_letters_count()

                    # reading progress
                    self.calculate_reading_progress(letters_per_content, reading_state)

                    # display reading progress
                    if (
                        self.reading_progress
                        and self.show_reading_progress
                        and (cols - reading_state.textwidth - 2) // 2 > 3
                    ):
                        reading_progress_str = "{}%".format(int(self.reading_progress * 100))
                        self.screen.addstr(
                            0, cols - len(reading_progress_str), reading_progress_str
                        )

                    self.screen.refresh()
                except curses.error:
                    pass

                if self.is_speaking:
                    k = self.keymap.TTSToggle[0]
                    continue

                k = board.getch()
                if k == Key(curses.KEY_MOUSE):
                    mouse_event = curses.getmouse()
                    if mouse_event[4] == curses.BUTTON1_CLICKED:
                        if mouse_event[1] < cols // 2:
                            k = self.keymap.PageUp[0]
                        else:
                            k = self.keymap.PageDown[0]
                    elif mouse_event[4] == curses.BUTTON3_CLICKED:
                        k = self.keymap.TableOfContents[0]
                    elif mouse_event[4] == curses.BUTTON4_PRESSED:
                        k = self.keymap.ScrollUp[0]
                    elif mouse_event[4] == 2097152:
                        k = self.keymap.ScrollDown[0]
                    elif mouse_event[4] == curses.BUTTON4_PRESSED + curses.BUTTON_CTRL:
                        k = self.keymap.Enlarge[0]
                    elif mouse_event[4] == 2097152 + curses.BUTTON_CTRL:
                        k = self.keymap.Shrink[0]
                    elif mouse_event[4] == curses.BUTTON2_CLICKED:
                        k = self.keymap.TTSToggle[0]

                if checkpoint_row:
                    board.feed_temporary_style()
                    checkpoint_row = None

        except KeyboardInterrupt:
            self.savestate(
                dataclasses.replace(reading_state, rel_pctg=reading_state.row / totlines)
            )
            sys.exit()


# }}}


# Reading Init {{{


def preread(stdscr, filepath: str):
    global STDSCR
    if DEBUG:
        STDSCR = stdscr

    ebook = get_ebook_obj(filepath)
    state = State()
    config = Config()

    reader = Reader(screen=stdscr, ebook=ebook, config=config, state=state)

    def handle_signal(signum, _):
        """
        Method to raise SystemExit based on signal received
        to trigger `try-finally` clause
        """
        msg = f"[{os.getpid()}] killed"
        if signal.Signals(signum) == signal.SIGTERM:
            msg = f"[{os.getpid()}] terminated"
        sys.exit(msg)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        reader.run_counting_letters()

        reading_state = state.get_last_reading_state(reader.ebook)
        if reader.screen_cols <= reading_state.textwidth + 4:
            reading_state = dataclasses.replace(reading_state, textwidth=reader.screen_cols - 4)
        else:
            reading_state = dataclasses.replace(reading_state, rel_pctg=None)

        while True:
            reading_state_or_ebook = reader.read(reading_state)

            if isinstance(reading_state_or_ebook, Ebook):
                return reading_state_or_ebook.path
            else:
                reading_state = reading_state_or_ebook
                if reader.seamless:
                    reading_state = reader.convert_absolute_reading_state_to_relative(reading_state)

    finally:
        reader.cleanup()


# }}}


# Commandline {{{


def parse_cli_args() -> argparse.Namespace:
    prog = "epy"
    positional_arg_help_str = "[PATH | # | PATTERN | URL]"
    args_parser = argparse.ArgumentParser(
        prog=prog,
        usage=f"%(prog)s [-h] [-r] [-d] [-v] {positional_arg_help_str}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read ebook in terminal",
        epilog=textwrap.dedent(
            f"""\
        examples:
          {prog} /path/to/ebook    read /path/to/ebook file
          {prog} 3                 read #3 file from reading history
          {prog} count monte       read file matching 'count monte'
                                from reading history
        """
        ),
    )
    args_parser.add_argument("-r", "--history", action="store_true", help="print reading history")
    args_parser.add_argument("-d", "--dump", action="store_true", help="dump the content of ebook")
    args_parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"v{__version__}",
        help="print version and exit",
    )
    args_parser.add_argument(
        "ebook",
        action="store",
        nargs="*",
        metavar=positional_arg_help_str,
        help="ebook path, history number, pattern or URL",
    )
    return args_parser.parse_args()


def find_file() -> Tuple[str, bool]:
    args = parse_cli_args()
    state = State()
    cleanup_library(state)

    if args.history:
        print_reading_history(state)
        sys.exit()

    if len(args.ebook) == 0:
        last_read = state.get_last_read()
        if last_read:
            return last_read, args.dump
        else:
            sys.exit("ERROR: Found no last read ebook file.")

    elif len(args.ebook) == 1:
        nth = coerce_to_int(args.ebook[0])
        if nth is not None:
            file = get_nth_file_from_library(state, nth)
            if file:
                return file.filepath, args.dump
            else:
                print(f"ERROR: #{nth} file not found.")
                print_reading_history(state)
                sys.exit(1)
        elif is_url(args.ebook[0]):
            return args.ebook[0], args.dump
        elif os.path.isfile(args.ebook[0]):
            return args.ebook[0], args.dump

    pattern = " ".join(args.ebook)
    match = get_matching_library_item(state, pattern)
    if match:
        return match.filepath, args.dump
    else:
        sys.exit("ERROR: Found no matching ebook from history.")


def main():
    filepath, dump_only = find_file()
    if dump_only:
        sys.exit(dump_ebook_content(filepath))

    while True:
        filepath = curses.wrapper(preread, filepath)


# }}}


if __name__ == "__main__":
    # On Windows, calling this method is necessary
    # On Linux/OSX, this method does nothing
    multiprocessing.freeze_support()
    main()
