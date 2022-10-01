import curses
from html.parser import HTMLParser
import textwrap
import re
import dataclasses
from html import unescape
from urllib.parse import unquote
from typing import Optional, Tuple, Set, Union, Sequence, List, Mapping, Dict

from epy_reader.models import TextStructure, TextMark, TextSpan, CharPos, InlineStyle


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
