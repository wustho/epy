from enum import Enum
import curses
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from epy_reader.models import Key


class DoubleSpreadPadding(Enum):
    LEFT = 10
    MIDDLE = 7
    RIGHT = 10


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
