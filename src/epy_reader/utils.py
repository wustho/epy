import curses
import os
import re
import sys
import textwrap
from functools import wraps
from typing import List, Mapping, Sequence, Tuple, Union

from epy_reader.ebooks import URL, Azw, Ebook, Epub, FictionBook, Mobi
from epy_reader.lib import is_url, tuple_subtract
from epy_reader.models import Key, LettersCount, NoUpdate, ReadingState, TextStructure, TocEntry
from epy_reader.parser import parse_html


def get_ebook_obj(filepath: str) -> Ebook:
    file_ext = os.path.splitext(filepath)[1].lower()
    if is_url(filepath):
        return URL(filepath)
    elif file_ext in {".epub", ".epub3"}:
        return Epub(filepath)
    elif file_ext == ".fb2":
        return FictionBook(filepath)
    elif file_ext == ".mobi":
        return Mobi(filepath)
    elif file_ext in {".azw", ".azw3"}:
        return Azw(filepath)
    else:
        sys.exit("ERROR: Format not supported. (Supported: epub, fb2)")


def safe_curs_set(state: int) -> None:
    try:
        curses.curs_set(state)
    except:
        return


def find_current_content_index(
    toc_entries: Tuple[TocEntry, ...], toc_secid: Mapping[str, int], index: int, y: int
) -> int:
    ntoc = 0
    for n, toc_entry in enumerate(toc_entries):
        if toc_entry.content_index <= index:
            if y >= toc_secid.get(toc_entry.section, 0):  # type: ignore
                ntoc = n
    return ntoc


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
