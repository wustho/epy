import copy
import curses
import dataclasses
import multiprocessing
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import epy_reader.settings as settings
from epy_reader.board import InfiniBoard
from epy_reader.config import Config
from epy_reader.ebooks import Azw, Ebook, Epub, Mobi
from epy_reader.lib import resolve_path
from epy_reader.models import (
    Direction,
    InlineStyle,
    Key,
    LettersCount,
    NoUpdate,
    ReadingState,
    SearchData,
    TextStructure,
    TocEntry,
)
from epy_reader.parser import parse_html
from epy_reader.settings import DoubleSpreadPadding
from epy_reader.speakers import SpeakerBaseModel
from epy_reader.state import State
from epy_reader.utils import (
    choice_win,
    construct_relative_reading_state,
    construct_speaker,
    count_letters,
    count_letters_parallel,
    find_current_content_index,
    get_ebook_obj,
    merge_text_structures,
    pgend,
    safe_curs_set,
    text_win,
)


# TODO: to be deprecated
DEBUG = False


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
                    args=(copy.deepcopy(self.ebook), self._proc_child),
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
            for i in settings.DICT_PRESET_LIST:
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
            for i in settings.VIEWER_PRESET_LIST:
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
                self.ebook.path, round(os.path.getsize(self.ebook.path) / 1024**2, 2)
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
                    return init_text if init_text else NoUpdate()
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
            if isinstance(candidate_text, str):
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


def start_reading(stdscr, filepath: str):

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
