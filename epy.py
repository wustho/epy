#!/usr/bin/env python3
"""\
Usages:
    epy             read last epub
    epy EPUBFILE    read EPUBFILE
    epy STRINGS     read matched STRINGS from history
    epy NUMBER      read file from history
                    with associated NUMBER

Options:
    -r              print reading history
    -d              dump epub
    -h, --help      print short, long help
"""


__version__ = "2021.12.18"
__license__ = "GPL-3.0"
__author__ = "Benawi Adha"
__email__ = "benawiadha@gmail.com"
__url__ = "https://github.com/wustho/epy"


import base64
import curses
import dataclasses
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import uuid
import xml.etree.ElementTree as ET
import zipfile

from typing import Optional, Union, Sequence, Tuple, List, Mapping, Set, Any
from dataclasses import dataclass
from difflib import SequenceMatcher as SM
from enum import Enum
from functools import wraps
from html import unescape
from html.parser import HTMLParser
from urllib.parse import unquote

try:
    import mobi

    MOBI_SUPPORT = True
except ModuleNotFoundError:
    MOBI_SUPPORT = False

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


class Direction(Enum):
    FORWARD = "forward"
    BACKWARD = "backward"


class DoubleSpreadPadding(Enum):
    LEFT = 10
    MIDDLE = 7
    RIGHT = 10


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
    TTSSpeed: int = 1
    TTSLang: str = "en-US"
    # -1 is default terminal fg/bg colors
    DarkColorFG: int = 252
    DarkColorBG: int = 235
    LightColorFG: int = 238
    LightColorBG: int = 253
    SeamlessBetweenChapters: bool = False


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


class Epub:
    NS = {
        "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
        "OPF": "http://www.idpf.org/2007/opf",
        "CONT": "urn:oasis:names:tc:opendocument:xmlns:container",
        "XHTML": "http://www.w3.org/1999/xhtml",
        "EPUB": "http://www.idpf.org/2007/ops",
    }

    def __init__(self, fileepub: str):
        self.path: str = os.path.abspath(fileepub)
        self.file: zipfile.ZipFile = zipfile.ZipFile(fileepub, "r")

        # populate these attribute
        # by calling self.initialize()
        self.version: str
        self.root_filepath: str
        self.root_dirpath: str
        self.toc_path: str
        self.contents: Tuple[str, ...] = tuple()
        self.toc_entries: Tuple[TocEntry, ...] = tuple()

    def get_meta(self) -> Tuple[Tuple[str, str], ...]:
        meta: List[Tuple[str, str]] = []
        # why self.file.read(self.root_filepath) problematic
        cont = ET.fromstring(self.file.open(self.root_filepath).read())
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append((re.sub("{.*?}", "", i.tag), i.text))
        return tuple(meta)

    def initialize(self) -> None:
        cont = ET.parse(self.file.open("META-INF/container.xml"))
        self.root_filepath = cont.find("CONT:rootfiles/CONT:rootfile", self.NS).attrib["full-path"]
        self.root_dirpath = (
            os.path.dirname(self.root_filepath) + "/"
            if os.path.dirname(self.root_filepath) != ""
            else ""
        )
        cont = ET.parse(self.file.open(self.root_filepath))
        # EPUB3
        self.version = cont.getroot().get("version")
        if self.version == "2.0":
            # "OPF:manifest/*[@id='ncx']"
            self.toc_path = self.root_dirpath + cont.find(
                "OPF:manifest/*[@media-type='application/x-dtbncx+xml']", self.NS
            ).get("href")
        elif self.version == "3.0":
            self.toc_path = self.root_dirpath + cont.find(
                "OPF:manifest/*[@properties='nav']", self.NS
            ).get("href")

        # cont = ET.parse(self.file.open(self.root_filepath)).getroot()
        manifest = []
        for i in cont.findall("OPF:manifest/*", self.NS):
            # EPUB3
            # if i.get("id") != "ncx" and i.get("properties") != "nav":
            if i.get("media-type") != "application/x-dtbncx+xml" and i.get("properties") != "nav":
                manifest.append([i.get("id"), i.get("href")])

        book_contents: List[str] = []
        spine: List[str] = []
        contents: List[str] = []
        for i in cont.findall("OPF:spine/*", self.NS):
            spine.append(i.get("idref"))
        for i in spine:
            for j in manifest:
                if i == j[0]:
                    book_contents.append(self.root_dirpath + unquote(j[1]))
                    contents.append(unquote(j[1]))
                    manifest.remove(j)
                    # TODO: test is break necessary
                    break
        self.contents = tuple(book_contents)

        try:
            toc = ET.parse(self.file.open(self.toc_path)).getroot()
            # EPUB3
            if self.version == "2.0":
                navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", self.NS)
            elif self.version == "3.0":
                navPoints = toc.findall("XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a", self.NS)

            toc_entries: List[TocEntry] = []
            for i in navPoints:
                if self.version == "2.0":
                    src = i.find("DAISY:content", self.NS).get("src")
                    name = i.find("DAISY:navLabel/DAISY:text", self.NS).text
                elif self.version == "3.0":
                    src = i.get("href")
                    name = "".join(list(i.itertext()))
                src = src.split("#")
                try:
                    idx = contents.index(unquote(src[0]))
                except ValueError:
                    continue

                toc_entries.append(
                    TocEntry(
                        label=name, content_index=idx, section=src[1] if len(src) == 2 else None
                    )
                )
            self.toc_entries = tuple(toc_entries)
        except AttributeError:
            pass

    def get_raw_text(self, content_path: str) -> str:
        # using try-except block to catch
        # zlib.error: Error -3 while decompressing data: invalid distance too far back
        # caused by forking PROC_COUNTLETTERS
        while True:
            try:
                content = self.file.open(content_path).read()
                break
            except:
                continue
        return content.decode("utf-8")

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        return impath, self.file.read(impath)

    def cleanup(self) -> None:
        return


class Mobi(Epub):
    def __init__(self, filemobi: str):
        self.path = os.path.abspath(filemobi)
        self.file, _ = mobi.extract(filemobi)

        # populate these attribute
        # by calling self.initialize()
        self.version: str
        self.root_filepath: str
        self.root_dirpath: str
        self.toc_path: str
        self.contents: Tuple[str] = ()
        self.toc_entries: Tuple[TocEntry] = ()

    def get_meta(self) -> Tuple[Tuple[str, str], ...]:
        meta: List[Tuple[str, str]] = []
        # why self.file.read(self.root_filepath) problematic
        with open(os.path.join(self.root_dirpath, "content.opf")) as f:
            cont = ET.parse(f).getroot()
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append((re.sub("{.*?}", "", i.tag), i.text))
        return tuple(meta)

    def initialize(self) -> None:
        self.root_dirpath = os.path.join(self.file, "mobi7")
        self.toc_path = os.path.join(self.root_dirpath, "toc.ncx")
        self.version = "2.0"

        with open(os.path.join(self.root_dirpath, "content.opf")) as f:
            cont = ET.parse(f).getroot()
        manifest = []
        for i in cont.findall("OPF:manifest/*", self.NS):
            # EPUB3
            # if i.get("id") != "ncx" and i.get("properties") != "nav":
            if i.get("media-type") != "application/x-dtbncx+xml" and i.get("properties") != "nav":
                manifest.append([i.get("id"), i.get("href")])

        book_contents: List[str] = []
        spine: List[str] = []
        contents: List[str] = []
        for i in cont.findall("OPF:spine/*", self.NS):
            spine.append(i.get("idref"))
        for i in spine:
            for j in manifest:
                if i == j[0]:
                    book_contents.append(os.path.join(self.root_dirpath, unquote(j[1])))
                    contents.append(unquote(j[1]))
                    manifest.remove(j)
                    # TODO: test is break necessary
                    break
        self.contents = tuple(book_contents)

        with open(self.toc_path) as f:
            toc = ET.parse(f).getroot()
        # EPUB3
        if self.version == "2.0":
            navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", self.NS)
        elif self.version == "3.0":
            navPoints = toc.findall("XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a", self.NS)

        toc_entries: List[TocEntry] = []
        for i in navPoints:
            if self.version == "2.0":
                src = i.find("DAISY:content", self.NS).get("src")
                name = i.find("DAISY:navLabel/DAISY:text", self.NS).text
            elif self.version == "3.0":
                src = i.get("href")
                name = "".join(list(i.itertext()))
            src = src.split("#")
            try:
                idx = contents.index(unquote(src[0]))
            except ValueError:
                continue

            toc_entries.append(
                TocEntry(label=name, content_index=idx, section=src[1] if len(src) == 2 else None)
            )
        self.toc_entries = tuple(toc_entries)

    def get_raw_text(self, content_path: str) -> str:
        # using try-except block to catch
        # zlib.error: Error -3 while decompressing data: invalid distance too far back
        # caused by forking PROC_COUNTLETTERS
        while True:
            try:
                with open(content_path) as f:
                    content = f.read()
                break
            except:
                continue
        # return content.decode("utf-8")
        return content

    def get_img_bytestr(self, impath: str) -> Tuple[str, bytes]:
        # TODO: test on windows
        # if impath "Images/asdf.png" is problematic
        with open(os.path.join(self.root_dirpath, impath), "rb") as f:
            src = f.read()
        return impath, src

    def cleanup(self) -> None:
        shutil.rmtree(self.file)
        return


class Azw3(Epub):
    def __init__(self, fileepub):
        self.path = os.path.abspath(fileepub)
        self.tmpdir, self.tmpepub = mobi.extract(fileepub)
        self.file = zipfile.ZipFile(self.tmpepub, "r")

    def cleanup(self) -> None:
        shutil.rmtree(self.tmpdir)
        return


class FictionBook:
    NS = {"FB2": "http://www.gribuser.ru/xml/fictionbook/2.0"}

    def __init__(self, filefb: str):
        self.path = os.path.abspath(filefb)
        self.file = filefb

        # populate these attribute
        # by calling self.initialize()
        self.root: str
        self.contents: Tuple[str] = ()
        self.toc_entries: Tuple[TocEntry] = ()

    def get_meta(self) -> Tuple[Tuple[str, str], ...]:
        desc = self.root.find("FB2:description", self.NS)
        alltags = desc.findall("*/*")
        return tuple((re.sub("{.*?}", "", i.tag), " ".join(i.itertext())) for i in alltags)

    def initialize(self) -> None:
        cont = ET.parse(self.file)
        self.root = cont.getroot()

        self.contents = tuple(self.root.findall("FB2:body/*", self.NS))

        # TODO
        toc_entries: List[TocEntry] = []
        for n, i in enumerate(self.contents):
            title = i.find("FB2:title", self.NS)
            if title is not None:
                toc_entries.append(
                    TocEntry(label="".join(title.itertext()), content_index=n, section=None)
                )
        self.toc_entries = tuple(toc_entries)

    def get_raw_text(self, node) -> str:
        ET.register_namespace("", "http://www.gribuser.ru/xml/fictionbook/2.0")
        # sys.exit(ET.tostring(node, encoding="utf8", method="html").decode("utf-8").replace("ns1:",""))
        return ET.tostring(node, encoding="utf8", method="html").decode("utf-8").replace("ns1:", "")

    def get_img_bytestr(self, imgid: str) -> Tuple[str, bytes]:
        imgid = imgid.replace("#", "")
        img = self.root.find("*[@id='{}']".format(imgid))
        imgtype = img.get("content-type").split("/")[1]
        return imgid + "." + imgtype, base64.b64decode(img.text)

    def cleanup(self) -> None:
        return


class HTMLtoLines(HTMLParser):
    para = {"p", "div"}
    inde = {"q", "dt", "dd", "blockquote"}
    pref = {"pre"}
    bull = {"li"}
    hide = {"script", "style", "head"}
    ital = {"i", "em"}
    bold = {"b", "strong"}
    # hide = {"script", "style", "head", ", "sub}

    attr_bold = curses.A_BOLD
    try:
        attr_italic = curses.A_ITALIC
    except AttributeError:
        try:
            attr_italic = curses.A_UNDERLINE
        except AttributeError:
            attr_italic = curses.A_NORMAL

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
        self.initital = []
        self.initbold = []
        self.imgs: Mapping[int, str] = dict()

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
            if len(self.initital) == 0 or len(self.initital[-1]) == 4:
                self.initital.append([len(self.text) - 1, len(self.text[-1])])
        elif tag in self.bold:
            if len(self.initbold) == 0 or len(self.initbold[-1]) == 4:
                self.initbold.append([len(self.text) - 1, len(self.text[-1])])
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
            if len(self.initital[-1]) == 2:
                self.initital[-1] += [len(self.text) - 1, len(self.text[-1])]
            elif len(self.initital[-1]) == 4:
                self.initital[-1][2:4] = [len(self.text) - 1, len(self.text[-1])]
        elif tag in self.bold:
            if len(self.initbold[-1]) == 2:
                self.initbold[-1] += [len(self.text) - 1, len(self.text[-1])]
            elif len(self.initbold[-1]) == 4:
                self.initbold[-1][2:4] = [len(self.text) - 1, len(self.text[-1])]

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
        text: List[str] = []
        images: Mapping[int, str] = dict()  # {line_num: path/in/zip}
        sect: Mapping[str, int] = dict()  # {section_id: line_num}
        formatting: List[InlineStyle] = []

        tmpital = []
        for i in self.initital:
            # handle uneven markup
            # like <i> but no </i>
            if len(i) == 4:
                if i[0] == i[2]:
                    tmpital.append([i[0], i[1], i[3] - i[1]])
                elif i[0] == i[2] - 1:
                    tmpital.append([i[0], i[1], len(self.text[i[0]]) - i[1]])
                    tmpital.append([i[2], 0, i[3]])
                elif i[2] - i[0] > 1:
                    tmpital.append([i[0], i[1], len(self.text[i[0]]) - i[1]])
                    for j in range(i[0] + 1, i[2]):
                        tmpital.append([j, 0, len(self.text[j])])
                    tmpital.append([i[2], 0, i[3]])
        tmpbold = []
        for i in self.initbold:
            if len(i) == 4:
                if i[0] == i[2]:
                    tmpbold.append([i[0], i[1], i[3] - i[1]])
                elif i[0] == i[2] - 1:
                    tmpbold.append([i[0], i[1], len(self.text[i[0]]) - i[1]])
                    tmpbold.append([i[2], 0, i[3]])
                elif i[2] - i[0] > 1:
                    tmpbold.append([i[0], i[1], len(self.text[i[0]]) - i[1]])
                    for j in range(i[0] + 1, i[2]):
                        tmpbold.append([j, 0, len(self.text[j])])
                    tmpbold.append([i[2], 0, i[3]])

        if not textwidth:
            return tuple(self.text)

        for n, i in enumerate(self.text):
            startline = len(text)
            # findsect = re.search(r"(?<= \(#).*?(?=\) )", i)
            # if findsect is not None and findsect.group() in self.sects:
            # i = i.replace(" (#" + findsect.group() + ") ", "")
            # # i = i.replace(" (#" + findsect.group() + ") ", " "*(5+len(findsect.group())))
            # sect[findsect.group()] = len(text)
            if n in self.sectsindex.keys():
                sect[self.sectsindex[n]] = starting_line + len(text)
            if n in self.idhead:
                # text += [i.rjust(textwidth // 2 + len(i) // 2)] + [""]
                text += [i.center(textwidth)] + [""]
                formatting += [
                    InlineStyle(
                        row=starting_line + j, col=0, n_letters=len(text[j]), attr=self.attr_bold
                    )
                    for j in range(startline, len(text))
                ]
            elif n in self.idinde:
                text += ["   " + j for j in textwrap.wrap(i, textwidth - 3)] + [""]
            elif n in self.idbull:
                tmp = textwrap.wrap(i, textwidth - 3)
                text += [" - " + j if j == tmp[0] else "   " + j for j in tmp] + [""]
            elif n in self.idpref:
                tmp = i.splitlines()
                wraptmp = []
                for line in tmp:
                    wraptmp += [j for j in textwrap.wrap(line, textwidth - 6)]
                text += ["   " + j for j in wraptmp] + [""]
            elif n in self.idimgs:
                images[starting_line + len(text)] = self.imgs[n]
                text += [i.center(textwidth)]
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
                text += textwrap.wrap(i, textwidth) + [""]

            # TODO: inline formats for indents
            endline = len(text)  # -1
            tmp_filtered = [j for j in tmpital if j[0] == n]
            for j in tmp_filtered:
                tmp_count = 0
                # for k in text[startline:endline]:
                for k in range(startline, endline):
                    if n in self.idbull | self.idinde:
                        if tmp_count <= j[1]:
                            tmp_start = [k, j[1] - tmp_count + 3]
                        if tmp_count <= j[1] + j[2]:
                            tmp_end = [k, j[1] + j[2] - tmp_count + 3]
                        tmp_count += len(text[k]) - 2
                    else:
                        if tmp_count <= j[1]:
                            tmp_start = [k, j[1] - tmp_count]
                        if tmp_count <= j[1] + j[2]:
                            tmp_end = [k, j[1] + j[2] - tmp_count]
                        tmp_count += len(text[k]) + 1
                if tmp_start[0] == tmp_end[0]:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_start[0],
                            col=tmp_start[1],
                            n_letters=tmp_end[1] - tmp_start[1],
                            attr=self.attr_italic,
                        )
                    )
                elif tmp_start[0] == tmp_end[0] - 1:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_start[0],
                            col=tmp_start[1],
                            n_letters=len(text[tmp_start[0]]) - tmp_start[1] + 1,
                            attr=self.attr_italic,
                        )
                    )
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_end[0],
                            col=0,
                            n_letters=tmp_end[1],
                            attr=self.attr_italic,
                        )
                    )
                # elif tmp_start[0]-tmp_end[1] > 1:
                else:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_start[0],
                            col=tmp_start[1],
                            n_letters=len(text[tmp_start[0]]) - tmp_start[1] + 1,
                            attr=self.attr_italic,
                        )
                    )
                    for l in range(tmp_start[0] + 1, tmp_end[0]):
                        formatting.append(
                            InlineStyle(
                                row=starting_line + l,
                                col=0,
                                n_letters=len(text[l]),
                                attr=self.attr_italic,
                            )
                        )
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_end[0],
                            col=0,
                            n_letters=tmp_end[1],
                            attr=self.attr_italic,
                        )
                    )
            tmp_filtered = [j for j in tmpbold if j[0] == n]
            for j in tmp_filtered:
                tmp_count = 0
                # for k in text[startline:endline]:
                for k in range(startline, endline):
                    if n in self.idbull | self.idinde:
                        if tmp_count <= j[1]:
                            tmp_start = [k, j[1] - tmp_count + 3]
                        if tmp_count <= j[1] + j[2]:
                            tmp_end = [k, j[1] + j[2] - tmp_count + 3]
                        tmp_count += len(text[k]) - 2
                    else:
                        if tmp_count <= j[1]:
                            tmp_start = [k, j[1] - tmp_count]
                        if tmp_count <= j[1] + j[2]:
                            tmp_end = [k, j[1] + j[2] - tmp_count]
                        tmp_count += len(text[k]) + 1
                if tmp_start[0] == tmp_end[0]:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_start[0],
                            col=tmp_start[1],
                            n_letters=tmp_end[1] - tmp_start[1],
                            attr=self.attr_bold,
                        )
                    )
                elif tmp_start[0] == tmp_end[0] - 1:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_start[0],
                            col=tmp_start[1],
                            n_letters=len(text[tmp_start[0]]) - tmp_start[1] + 1,
                            attr=self.attr_bold,
                        )
                    )
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_end[0],
                            col=0,
                            n_letters=tmp_end[1],
                            attr=self.attr_bold,
                        )
                    )
                # elif tmp_start[0]-tmp_end[1] > 1:
                else:
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_start[0],
                            col=tmp_start[1],
                            n_letters=len(text[tmp_start[0]]) - tmp_start[1] + 1,
                            attr=self.attr_bold,
                        )
                    )
                    for l in range(tmp_start[0] + 1, tmp_end[0]):
                        formatting.append(
                            InlineStyle(
                                row=starting_line + l,
                                col=0,
                                n_letters=len(text[l]),
                                attr=self.attr_bold,
                            )
                        )
                    formatting.append(
                        InlineStyle(
                            row=starting_line + tmp_end[0],
                            col=0,
                            n_letters=tmp_end[1],
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

    def get_from_history(self) -> Tuple[str]:
        try:
            conn = sqlite3.connect(self.filepath)
            cur = conn.cursor()
            cur.execute("SELECT filepath FROM reading_states")
            results = cur.fetchall()
            return tuple(i[0] for i in results)
        finally:
            conn.close()

    def delete_from_history(self, filepath: str) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute("DELETE FROM reading_states WHERE filepath=?", (filepath,))
            conn.commit()
        finally:
            conn.close()

    def get_last_read(self) -> Optional[str]:
        try:
            conn = sqlite3.connect(self.filepath)
            cur = conn.cursor()
            cur.execute("SELECT filepath FROM last_read WHERE id=0")
            res = cur.fetchone()
            if res:
                return res[0]
            return None
        finally:
            conn.close()

    def set_last_read(self, ebook: Union[Epub, Mobi, Azw3, FictionBook]) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute("INSERT OR REPLACE INTO last_read VALUES (0, ?)", (ebook.path,))
            conn.commit()
        finally:
            conn.close()

    def get_last_reading_state(self, ebook: Union[Epub, Mobi, Azw3, FictionBook]) -> ReadingState:
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

    def set_last_reading_state(
        self, ebook: Union[Epub, Mobi, Azw3, FictionBook], reading_state: ReadingState
    ) -> None:
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

    def insert_bookmark(
        self, ebook: Union[Epub, Mobi, Azw3, FictionBook], name: str, reading_state: ReadingState
    ) -> None:
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

    def delete_bookmark(self, ebook: Union[Epub, Mobi, Azw3, FictionBook], name: str) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute("DELETE FROM bookmarks WHERE filepath=? AND name=?", (ebook.path, name))
            conn.commit()
        finally:
            conn.close()

    def delete_bookmarks_by_filepath(self, filepath: str) -> None:
        try:
            conn = sqlite3.connect(self.filepath)
            conn.execute("DELETE FROM bookmarks WHERE filepath=?", (filepath,))
            conn.commit()
        finally:
            conn.close()

    def get_bookmarks(
        self, ebook: Union[Epub, Mobi, Azw3, FictionBook]
    ) -> List[Tuple[str, ReadingState]]:
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
            conn.execute(
                """
                CREATE TABLE last_read (
                    id INTEGER PRIMARY KEY,
                    filepath TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE reading_states (
                    filepath TEXT PRIMARY KEY,
                    content_index INTEGER,
                    textwidth INTEGER,
                    row INTEGER,
                    rel_pctg REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE bookmarks (
                    id TEXT PRIMARY KEY,
                    filepath TEXT,
                    name TEXT,
                    content_index INTEGER,
                    textwidth INTEGER,
                    row INTEGER,
                    rel_pctg REAL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


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
        text: Tuple[str],
        textwidth: int = 80,
        default_style: Tuple[InlineStyle] = (),
        spread: int = 1,
    ):
        self.screen = screen
        self.screen_rows, self.screen_cols = self.screen.getmaxyx()
        self.textwidth = textwidth
        self.x = ((self.screen_cols - self.textwidth) // 2) + 1
        self.text = text
        self.total_lines = len(text)
        self.default_style: Tuple[InlineStyle, ...] = default_style
        self.temporary_style = ()
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
    cumulative_contents_lines = 0
    all_contents_lines = sum(totlines_per_content)
    for n, content_lines in enumerate(totlines_per_content):
        cumulative_contents_lines += content_lines
        if cumulative_contents_lines > abs_reading_state.row:
            return ReadingState(
                content_index=n,
                textwidth=abs_reading_state.textwidth,
                row=abs_reading_state.row - cumulative_contents_lines + content_lines,
                rel_pctg=abs_reading_state.rel_pctg
                - ((cumulative_contents_lines - content_lines) / all_contents_lines)
                if abs_reading_state.rel_pctg
                else None,
                section=abs_reading_state.section,
            )


def get_ebook_obj(filepath: str) -> Union[Epub, Mobi, Azw3, FictionBook]:
    file_ext = os.path.splitext(filepath)[1]
    if file_ext == ".epub":
        return Epub(filepath)
    elif file_ext == ".fb2":
        return FictionBook(filepath)
    elif MOBI_SUPPORT and file_ext == ".mobi":
        return Mobi(filepath)
    elif MOBI_SUPPORT and file_ext == ".azw3":
        return Azw3(filepath)
    elif not MOBI_SUPPORT and file_ext in {".mobi", ".azw3"}:
        sys.exit(
            "ERROR: Format not supported. (Supported: epub, fb2). "
            "To get mobi and azw3 support, install mobi module from pip. "
            "$ pip install mobi"
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


def dots_path(curr, tofi):
    candir = curr.split("/")
    tofi = tofi.split("/")
    alld = tofi.count("..")
    t = len(candir)
    candir = candir[0 : t - alld - 1]
    try:
        while True:
            tofi.remove("..")
    except ValueError:
        pass
    return "/".join(candir + tofi)


def find_current_content_index(
    toc_entries: Tuple[TocEntry], toc_secid: Mapping[str, int], index: int, y: int
) -> int:
    ntoc = 0
    for n, toc_entry in enumerate(toc_entries):
        if toc_entry.content_index <= index:
            if y >= toc_secid.get(toc_entry.section, 0):
                ntoc = n
    return ntoc


def count_letters(ebook: Union[Epub, Mobi, Azw3, FictionBook]) -> LettersCount:
    per_content_counts: List[int] = []
    cumulative_counts: List[int] = []
    for i in ebook.contents:
        content = ebook.get_raw_text(i)
        src_lines = parse_html(content)
        cumulative_counts.append(sum(per_content_counts))
        per_content_counts.append(sum([len(re.sub("\s", "", j)) for j in src_lines]))

    return LettersCount(all=sum(per_content_counts), cumulative=tuple(cumulative_counts))


def count_letters_parallel(ebook: Union[Epub, Mobi, Azw3, FictionBook], child_conn) -> None:
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


class Reader:
    def __init__(
        self, screen, ebook: Union[Epub, Mobi, Azw3, FictionBook], config: Config, state: State
    ):

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
            curses.init_pair(1, -1, -1)
            curses.init_pair(2, self.setting.DarkColorFG, self.setting.DarkColorBG)
            curses.init_pair(3, self.setting.LightColorFG, self.setting.LightColorBG)
            self.is_color_supported = True
        except:
            self.is_color_supported = False

        # show loader and start heavy resources processes
        self.show_loader()

        # main ebook object
        self.ebook = ebook
        try:
            self.ebook.initialize()
        except Exception as e:
            sys.exit("ERROR: Badly-structured ebook.\n" + str(e))

        # state
        self.state = state

        # page scroll animation
        self.page_animation: Optional[Direction] = None

        # show reading progress
        self.show_reading_progress = self.setting.ShowProgressIndicator

        # search storage
        self.search_data: Optional[SearchData] = None

        # double spread
        self.spread = 2 if self.setting.StartWithDoubleSpread else 1

        # jumps marker container
        self.jump_list: Mapping[str, ReadingState] = dict()

        # TTS speaker utils
        self._tts_support: bool = any([shutil.which("pico2wave"), shutil.which("play")])
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
            except:
                self._multiprocess_support = False
        if not self._multiprocess_support:
            self.letters_count = count_letters(self.ebook)

    @property
    def screen_rows(self) -> int:
        return self.screen.getmaxyx()[0]

    @property
    def screen_cols(self) -> int:
        return self.screen.getmaxyx()[1]

    @property
    def ext_dict_app(self) -> Optional[str]:
        self._ext_dict_app: Optional[str] = None
        dict_app_preset_list = ["sdcv", "dict"]

        if shutil.which(self.setting.DictionaryClient.split()[0]):
            self._ext_dict_app = self.setting.DictionaryClient
        else:
            for i in dict_app_preset_list:
                if shutil.which(i) is not None:
                    self._ext_dict_app = i
                    break
            if self._ext_dict_app in {"sdcv"}:
                self._ext_dict_app += " -n"

        return self._ext_dict_app

    @property
    def image_viewer(self) -> str:
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

    def show_loader(self):
        self.screen.clear()
        rows, cols = self.screen.getmaxyx()
        self.screen.addstr((rows - 1) // 2, (cols - 1) // 2, "\u231B")
        # self.screen.addstr(((rows-2)//2)+1, (cols-len(msg))//2, msg)
        self.screen.refresh()

    @choice_win(True)
    def show_win_options(self, title, options, active_index, key_set):
        return title, options, active_index, key_set

    @text_win
    def show_win_error(self, title, msg, key):
        return title, msg, key

    @choice_win()
    def toc(self, toc_entries: Tuple[TocEntry], index: int):
        return (
            "Table of Contents",
            [i.label for i in toc_entries],
            index,
            self.keymap.TableOfContents,
        )

    @text_win
    def show_win_metadata(self):
        mdata = "[File Info]\nPATH: {}\nSIZE: {} MB\n \n[Book Info]\n".format(
            self.ebook.path, round(os.path.getsize(self.ebook.path) / 1024 ** 2, 2)
        )
        for i in self.ebook.get_meta():
            data = re.sub("<[^>]*>", "", i[1])
            mdata += i[0].upper() + ": " + data + "\n"
            # data = re.sub("\t", "", data)
            # mdata += textwrap.wrap(i[0].upper() + ": " + data, wi - 6)
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

    def input_prompt(self, prompt: str) -> Union[NoUpdate, Key, str]:
        # prevent pad hole when prompting for input while
        # other window is active
        # pad.refresh(y, 0, 0, x, rows-2, x+width)
        rows, cols = self.screen.getmaxyx()
        stat = curses.newwin(1, cols, rows - 1, 0)
        if self.is_color_supported:
            stat.bkgd(self.screen.getbkgd())
        stat.keypad(True)
        curses.echo(1)
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
                    curses.echo(0)
                    safe_curs_set(0)
                    return NoUpdate()
                elif ipt == Key(10):
                    stat.clear()
                    stat.refresh()
                    curses.echo(0)
                    safe_curs_set(0)
                    return init_text
                elif ipt in (Key(8), Key(127), Key(curses.KEY_BACKSPACE)):
                    init_text = init_text[:-1]
                elif ipt == Key(curses.KEY_RESIZE):
                    stat.clear()
                    stat.refresh()
                    curses.echo(0)
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
            curses.echo(0)
            safe_curs_set(0)
            return NoUpdate()

    def searching(
        self, board: InfiniBoard, src: Sequence[str], reading_state: ReadingState, tot
    ) -> Union[NoUpdate, ReadingState, Key]:

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
            if isinstance(candidate_text, str) and candidate_text:
                self.search_data = SearchData(value=candidate_text)
            else:
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
                s = 0
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

        s = 0
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
                        s = 0
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
                        s = 0
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
            _, path = tempfile.mkstemp(suffix=".wav")
            subprocess.call(
                ["pico2wave", f"--lang={self.setting.TTSLang}", "-w", path, text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            speaker = subprocess.Popen(
                ["play", path, "tempo", str(self.setting.TTSSpeed)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            while True:
                if speaker.poll() is not None:
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
                    speaker.terminate()
                    # speaker.kill()
                    break
        finally:
            self.screen.timeout(-1)
            os.remove(path)

        if k in self.keymap.Quit:
            self.is_speaking = False
            k = NoUpdate()
        return k

    def savestate(self, reading_state: ReadingState) -> None:
        if self.seamless:
            reading_state = Reader.adjust_seamless_reading_state(reading_state)
        self.state.set_last_read(self.ebook)
        self.state.set_last_reading_state(self.ebook, reading_state)

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

    def read(self, reading_state: ReadingState) -> ReadingState:

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

        contents = self.ebook.contents
        toc_entries = self.ebook.toc_entries
        if self.seamless:
            text_structure: TextStructure = TextStructure(
                text_lines=tuple(), image_maps=dict(), section_rows=dict(), formatting=tuple()
            )
            toc_entries_tmp: List[TocEntry] = []
            section_rows_tmp: Mapping[str, int] = dict()
            totlines_per_content: Sequence[int] = []  # only defined when Seamless==True
            for n, content in enumerate(contents):
                starting_line = sum(totlines_per_content)
                text_structure_tmp = parse_html(
                    self.ebook.get_raw_text(content),
                    textwidth=reading_state.textwidth,
                    section_ids=set(toc_entry.section for toc_entry in toc_entries),
                    starting_line=starting_line,
                )
                totlines_per_content.append(len(text_structure_tmp.text_lines))

                for toc_entry in toc_entries:
                    if toc_entry.content_index == n:
                        if toc_entry.section:
                            toc_entries_tmp.append(dataclasses.replace(toc_entry, content_index=0))
                        else:
                            section_id_tmp = str(uuid.uuid4())
                            toc_entries_tmp.append(
                                TocEntry(
                                    label=toc_entry.label, content_index=0, section=section_id_tmp
                                )
                            )
                            section_rows_tmp[section_id_tmp] = starting_line

                text_structure = merge_text_structures(text_structure, text_structure_tmp)

            # adjustment
            contents = [contents[0]]
            toc_entries = toc_entries_tmp
            text_structure = dataclasses.replace(
                text_structure, section_rows={**text_structure.section_rows, **section_rows_tmp}
            )
            reading_state = dataclasses.replace(
                reading_state,
                content_index=0,
                row=reading_state.row + sum(totlines_per_content[: reading_state.content_index]),
                rel_pctg=(
                    reading_state.row + sum(totlines_per_content[: reading_state.content_index])
                )
                / len(text_structure.text_lines)
                if reading_state.rel_pctg
                else None,
            )
            # objects that only exist when Setting.Seamless==True
            totlines_per_content = tuple(totlines_per_content)
            Reader.adjust_seamless_reading_state = staticmethod(
                lambda reading_state: construct_relative_reading_state(
                    reading_state, totlines_per_content
                )
            )

        else:
            content_path = contents[reading_state.content_index]
            content = self.ebook.get_raw_text(content_path)
            text_structure = parse_html(
                content,
                textwidth=reading_state.textwidth,
                section_ids=set(toc_entry.section for toc_entry in toc_entries),
            )

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

        LOCALPCTG = []
        for i in text_structure.text_lines:
            LOCALPCTG.append(len(re.sub("\s", "", i)))

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
                            self.savestate(
                                dataclasses.replace(
                                    reading_state, rel_pctg=reading_state.row / totlines
                                )
                            )
                            sys.exit()

                    elif k in self.keymap.TTSToggle and self._tts_support:
                        # tospeak = "\n".join(text_structure.text_lines[y:y+rows-1])
                        tospeak = ""
                        for i in text_structure.text_lines[
                            reading_state.row : reading_state.row + (rows * self.spread)
                        ]:
                            if re.match(r"^\s*$", i) is not None:
                                tospeak += "\n. \n"
                            else:
                                tospeak += re.sub(r"\[IMG:[0-9]+\]", "Image", i) + " "
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
                        else:
                            reading_state = dataclasses.replace(reading_state, row=totlines - rows)

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
                                            toc_entries[ntoc + 1].section
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
                                        toc_entries[ntoc - 1].section, 0
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
                                row=text_structure.section_rows[toc_entries[ntoc].section],
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
                                text_structure.section_rows[toc_entries[ntoc + 1].section] - rows
                                >= 0
                            ):
                                reading_state = dataclasses.replace(
                                    reading_state,
                                    row=text_structure.section_rows[toc_entries[ntoc + 1].section]
                                    - rows,
                                )
                            else:
                                reading_state = dataclasses.replace(
                                    reading_state,
                                    row=text_structure.section_rows[toc_entries[ntoc].section],
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
                                if self.ebook.__class__.__name__ in {"Epub", "Mobi", "Azw3"}:
                                    # self.seamless adjustment
                                    if self.seamless:
                                        content_path = self.ebook.contents[
                                            Reader.adjust_seamless_reading_state(
                                                reading_state
                                            ).content_index
                                        ]
                                        # for n, content in enumerate(self.ebook.contents):
                                        #     content_path = content
                                        #     if reading_state.row < sum(totlines_per_content[:n]):
                                        #         break

                                    image_path = dots_path(content_path, image_path)
                                imgnm, imgbstr = self.ebook.get_img_bytestr(image_path)
                                k = self.open_image(board, imgnm, imgbstr)
                                continue
                            except Exception as e:
                                self.show_win_error("Error Opening Image", str(e), tuple())

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
                        if jumnum in tuple(Key(i) for i in range(48, 58)):
                            self.jump_list[jumnum.char] = reading_state
                        else:
                            k = jumnum
                            continue

                    elif k in self.keymap.JumpToPosition:
                        jumnum = board.getch()
                        if (
                            jumnum in tuple(Key(i) for i in range(48, 58))
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

                    # check self._process
                    if isinstance(self._process_counting_letter, multiprocessing.Process):
                        if self._process_counting_letter.exitcode == 0:
                            self.letters_count = self._proc_parent.recv()
                            self._proc_parent.close()
                            self._process_counting_letter.terminate()
                            self._process_counting_letter.close()
                            self._process_counting_letter = None

                    if (
                        self.show_reading_progress
                        and (cols - reading_state.textwidth - 2) // 2 > 3
                        and self.letters_count
                    ):
                        reading_progress = (
                            self.letters_count.cumulative[reading_state.content_index]
                            + sum(LOCALPCTG[: reading_state.row + (rows * self.spread) - 1])
                        ) / self.letters_count.all
                        reading_progress_str = "{}%".format(int(reading_progress * 100))
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


def preread(stdscr, filepath: str):

    ebook = get_ebook_obj(filepath)
    state = State()
    config = Config()

    reader = Reader(screen=stdscr, ebook=ebook, config=config, state=state)

    try:
        reader.run_counting_letters()

        reading_state = state.get_last_reading_state(reader.ebook)
        if reader.screen_cols <= reading_state.textwidth + 4:
            reading_state = dataclasses.replace(reading_state, textwidth=reader.screen_cols - 4)
        else:
            reading_state = dataclasses.replace(reading_state, rel_pctg=None)

        while True:
            reading_state = reader.read(reading_state)
            reader.show_loader()
            if reader.seamless:
                reading_state = Reader.adjust_seamless_reading_state(reading_state)
    finally:
        reader.cleanup()


def parse_cli_args() -> str:
    """
    Try parsing cli args and return filepath of ebook to read
    or quitting based on args and app state
    """
    termc, termr = shutil.get_terminal_size()

    args = []
    if sys.argv[1:] != []:
        args += sys.argv[1:]

    if len({"-h", "--help"} & set(args)) != 0:
        print(__doc__.rstrip())
        sys.exit()

    if len({"-v", "--version", "-V"} & set(args)) != 0:
        print("v" + __version__)
        print(__license__, "License")
        print("Copyright (c) 2019", __author__)
        print(__url__)
        sys.exit()

    app_state = State()

    # trying finding file and keep it in candidate
    # which has the form of candidate = (filepath, error_msg)
    # if filepath is None or error_msg is None then
    # the app failed and exit with error_msg
    candidate: Tuple[Optional[str], Optional[str]]

    last_read_in_history = app_state.get_last_read()

    # clean up history from missing file
    reading_history = app_state.get_from_history()
    is_history_modified = False
    for file in reading_history:
        if not os.path.isfile(file):
            app_state.delete_from_history(file)
            app_state.delete_bookmarks_by_filepath(file)
            is_history_modified = True
    if is_history_modified:
        reading_history = app_state.get_from_history()

    if len({"-d"} & set(args)) != 0:
        args.remove("-d")
        dump = True
    else:
        dump = False

    if not args:
        candidate = (last_read_in_history, None)
        if not candidate[0] or not os.path.isfile(candidate[0]):
            # instant fail
            sys.exit("ERROR: Found no last read file.")

    elif os.path.isfile(args[0]):
        candidate = (args[0], None)

    else:
        candidate = (None, "ERROR: No matching file found in history.")

        # find file from history with index number
        if len(args) == 1 and re.match(r"[0-9]+", args[0]) is not None:
            try:
                # file = list(STATE["States"].keys())[int(args[0]) - 1]
                candidate = (reading_history[int(args[0]) - 1], None)
            except IndexError:
                pass

        # find file from history by string matching
        if (not candidate[0]) or candidate[1]:
            matching_value = 0
            for file in reading_history:
                this_file_match_value = sum(
                    [
                        i.size
                        for i in SM(
                            None, file.lower(), " ".join(args).lower()
                        ).get_matching_blocks()
                    ]
                )
                if this_file_match_value >= matching_value:
                    matching_value = this_file_match_value
                    candidate = (file, None)

            if matching_value == 0:
                candidate = (None, "\nERROR: No matching file found in history.")

        if (not candidate[0]) or candidate[1] or "-r" in args:
            print("Reading history:")
            # dig = len(str(len(STATE["States"].keys()) + 1))
            dig = len(str(len(reading_history) + 1))
            tcols = termc - dig - 2
            for n, i in enumerate(reading_history):
                p = i.replace(os.getenv("HOME"), "~") if os.getenv("HOME") else i
                print(
                    "{}{} {}".format(
                        str(n + 1).rjust(dig),
                        "*" if i == last_read_in_history else " ",
                        truncate(p, "...", tcols, 7),
                    )
                )
            if "-r" in args:
                sys.exit()

    filepath, error_msg = candidate
    if (not filepath) or error_msg:
        sys.exit(error_msg)

    if dump:
        ebook = get_ebook_obj(filepath)
        try:
            try:
                ebook.initialize()
            except Exception as e:
                sys.exit("ERROR: Badly-structured ebook.\n" + str(e))
            for i in ebook.contents:
                content = ebook.get_raw_text(i)
                src_lines = parse_html(content)
                # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
                for j in src_lines:
                    sys.stdout.buffer.write((j + "\n\n").encode("utf-8"))
        finally:
            ebook.cleanup()
        sys.exit()

    else:
        if termc < 22 or termr < 12:
            sys.exit("ERROR: Screen was too small (min 22cols x 12rows).")

        return filepath


def main():
    filepath = parse_cli_args()
    curses.wrapper(preread, filepath)


if __name__ == "__main__":
    main()
