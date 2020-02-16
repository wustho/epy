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


__version__ = "2020.2.17"
__license__ = "MIT"
__author__ = "Benawi Adha"
__url__ = "https://github.com/wustho/epy"


import curses
import zipfile
import sys
import re
import os
import textwrap
import json
import tempfile
import shutil
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from html import unescape
# from subprocess import run
from html.parser import HTMLParser
from difflib import SequenceMatcher as SM
from functools import wraps


# -1 is default terminal fg/bg colors
CFG = {
    "DefaultViewer": "Default",
    "EnableProgressIndicator": True,
    "DarkColorFG": 252,
    "DarkColorBG": 235,
    "LightColorFG": 238,
    "LightColorBG": 253,
    "Keys": {
        "ScrollUp": "k",
        "ScrollDown": "j",
        "PageUp": "h",
        "PageDown": "l",
        "NextChapter": "n",
        "PrevChapter": "p",
        "BeginningOfCh": "g",
        "EndOfCh": "G",
        "Shrink": "-",
        "Enlarge": "+",
        "SetWidth": "=",
        "Metadata": "m",
        "ToC": "t",
        "Follow": "f",
        "OpenImage": "o",
        "RegexSearch": "/",
        "ShowHideProgress": "s",
        "Quit": "q",
        "Help": "?",
        "SwitchColor": "c"
    }
}
STATE = {
    "LastRead": "",
    "States": {}
}
K = {
    "ScrollUp": {curses.KEY_UP},
    "ScrollDown": {curses.KEY_DOWN},
    "PageUp": {curses.KEY_PPAGE, curses.KEY_LEFT},
    "PageDown": {curses.KEY_NPAGE, ord(" "), curses.KEY_RIGHT},
    "NextChapter": set(),
    "PrevChapter": set(),
    "BeginningOfCh": {curses.KEY_HOME},
    "EndOfCh": {curses.KEY_END},
    "Shrink": set(),
    "Enlarge": set(),
    "SetWidth": set(),
    "Metadata": set(),
    "ToC": {9, ord("\t")},
    "Follow": {10},
    "OpenImage": set(),
    "RegexSearch": set(),
    "ShowHideProgress": set(),
    "Quit": {3, 27, 304},
    "Help": set(),
    "SwitchColor": set()
}
WINKEYS = set()
CFGFILE = ""
STATEFILE = ""
COLORSUPPORT = False
LINEPRSRV = 0  # 2
SEARCHPATTERN = None
VWR = None
SCREEN = None
PERCENTAGE = []
SHOWPROGRESS = CFG["EnableProgressIndicator"]


class Epub:
    NS = {
        "DAISY": "http://www.daisy.org/z3986/2005/ncx/",
        "OPF": "http://www.idpf.org/2007/opf",
        "CONT": "urn:oasis:names:tc:opendocument:xmlns:container",
        "XHTML": "http://www.w3.org/1999/xhtml",
        "EPUB": "http://www.idpf.org/2007/ops"
    }

    def __init__(self, fileepub):
        self.path = os.path.abspath(fileepub)
        self.file = zipfile.ZipFile(fileepub, "r")
        cont = ET.parse(self.file.open("META-INF/container.xml"))
        self.rootfile = cont.find(
            "CONT:rootfiles/CONT:rootfile",
            self.NS
        ).attrib["full-path"]
        self.rootdir = os.path.dirname(self.rootfile)\
            + "/" if os.path.dirname(self.rootfile) != "" else ""
        cont = ET.parse(self.file.open(self.rootfile))
        # EPUB3
        self.version = cont.getroot().get("version")
        if self.version == "2.0":
            # "OPF:manifest/*[@id='ncx']"
            self.toc = self.rootdir\
                + cont.find(
                    "OPF:manifest/*[@media-type='application/x-dtbncx+xml']",
                    self.NS
                ).get("href")
        elif self.version == "3.0":
            self.toc = self.rootdir\
                + cont.find(
                    "OPF:manifest/*[@properties='nav']",
                    self.NS
                ).get("href")

        self.contents = []
        self.toc_entries = [[], [], []]

    def get_meta(self):
        meta = []
        # why self.file.read(self.rootfile) problematic
        cont = ET.fromstring(self.file.open(self.rootfile).read())
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append([re.sub("{.*?}", "", i.tag), i.text])
        return meta

    def initialize(self):
        cont = ET.parse(self.file.open(self.rootfile)).getroot()
        manifest = []
        for i in cont.findall("OPF:manifest/*", self.NS):
            # EPUB3
            # if i.get("id") != "ncx" and i.get("properties") != "nav":
            if i.get("media-type") != "application/x-dtbncx+xml"\
               and i.get("properties") != "nav":
                manifest.append([
                    i.get("id"),
                    i.get("href")
                ])

        spine, contents = [], []
        for i in cont.findall("OPF:spine/*", self.NS):
            spine.append(i.get("idref"))
        for i in spine:
            for j in manifest:
                if i == j[0]:
                    self.contents.append(self.rootdir+unquote(j[1]))
                    contents.append(unquote(j[1]))
                    manifest.remove(j)
                    # TODO: test is break necessary
                    break

        toc = ET.parse(self.file.open(self.toc)).getroot()
        # EPUB3
        if self.version == "2.0":
            navPoints = toc.findall("DAISY:navMap//DAISY:navPoint", self.NS)
        elif self.version == "3.0":
            navPoints = toc.findall(
                "XHTML:body//XHTML:nav[@EPUB:type='toc']//XHTML:a",
                self.NS
            )
        for i in navPoints:
            if self.version == "2.0":
                src = i.find("DAISY:content", self.NS).get("src")
                name = i.find("DAISY:navLabel/DAISY:text", self.NS).text
            elif self.version == "3.0":
                src = i.get("href")
                name = "".join(list(i.itertext()))
            src = src.split("#")
            idx = contents.index(unquote(src[0]))
            self.toc_entries[0].append(name)
            self.toc_entries[1].append(idx)
            if len(src) == 2:
                self.toc_entries[2].append(src[1])
            elif len(src) == 1:
                self.toc_entries[2].append("")


class HTMLtoLines(HTMLParser):
    para = {"p", "div"}
    inde = {"q", "dt", "dd", "blockquote"}
    pref = {"pre"}
    bull = {"li"}
    hide = {"script", "style", "head"}
    # hide = {"script", "style", "head", ", "sub}

    def __init__(self, sects={""}):
        HTMLParser.__init__(self)
        self.text = [""]
        self.imgs = []
        self.ishead = False
        self.isinde = False
        self.isbull = False
        self.ispref = False
        self.ishidden = False
        self.idhead = set()
        self.idinde = set()
        self.idbull = set()
        self.idpref = set()
        self.sects = sects

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
        elif tag == "image":
            for i in attrs:
                if i[0] == "xlink:href":
                    self.text.append("[IMG:{}]".format(len(self.imgs)))
                    self.imgs.append(unquote(i[1]))
        if self.sects != {""}:
            for i in attrs:
                if i[1] in self.sects:
                    self.text[-1] += " (#" + i[1] + ") "

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.text += [""]
        elif tag in {"img", "image"}:
            for i in attrs:
                if (tag == "img" and i[0] == "src")\
                   or (tag == "image" and i[0] == "xlink:href"):
                    self.text.append("[IMG:{}]".format(len(self.imgs)))
                    self.imgs.append(unquote(i[1]))
                    self.text.append("")

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
        elif tag == "image":
            self.text.append("")

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
                self.idhead.add(len(self.text)-1)
            elif self.isbull:
                self.idbull.add(len(self.text)-1)
            elif self.isinde:
                self.idinde.add(len(self.text)-1)
            elif self.ispref:
                self.idpref.add(len(self.text)-1)

    def get_lines(self, width=0):
        text, sect = [], {}
        if width == 0:
            return self.text
        for n, i in enumerate(self.text):
            findsect = re.search(r"(?<= \(#).*?(?=\) )", i)
            if findsect is not None and findsect.group() in self.sects:
                i = i.replace(" (#" + findsect.group() + ") ", "")
                sect[findsect.group()] = len(text)
            if n in self.idhead:
                text += [i.rjust(width//2 + len(i)//2)] + [""]
            elif n in self.idinde:
                text += [
                    "   "+j for j in textwrap.wrap(i, width - 3)
                ] + [""]
            elif n in self.idbull:
                tmp = textwrap.wrap(i, width - 3)
                text += [
                    " - "+j if j == tmp[0] else "   "+j for j in tmp
                ] + [""]
            elif n in self.idpref:
                tmp = i.splitlines()
                wraptmp = []
                for line in tmp:
                    wraptmp += [j for j in textwrap.wrap(line, width - 6)]
                text += ["   "+j for j in wraptmp] + [""]
            else:
                text += textwrap.wrap(i, width) + [""]
        return text, self.imgs, sect


def text_win(textfunc):
    @wraps(textfunc)
    def wrapper(*args, **kwargs):
        rows, cols = SCREEN.getmaxyx()
        hi, wi = rows - 4, cols - 4
        Y, X = 2, 2
        textw = curses.newwin(hi, wi, Y, X)
        if COLORSUPPORT:
            textw.bkgd(SCREEN.getbkgd())

        title, raw_texts, key = textfunc(*args, **kwargs)

        texts = []
        for i in raw_texts.splitlines():
            texts += textwrap.wrap(i, wi - 6)

        textw.box()
        textw.keypad(True)
        textw.addstr(1, 2, title)
        textw.addstr(2, 2, "-"*len(title))
        key_textw = 0

        totlines = len(texts)

        pad = curses.newpad(totlines, wi - 2)
        if COLORSUPPORT:
            pad.bkgd(SCREEN.getbkgd())

        pad.keypad(True)
        for n, i in enumerate(texts):
            pad.addstr(n, 0, i)
        y = 0
        textw.refresh()
        pad.refresh(y, 0, Y+4, X+4, rows - 5, cols - 6)
        padhi = rows - 8 - Y

        while key_textw not in K["Quit"]|key:
            if key_textw in K["ScrollUp"] and y > 0:
                y -= 1
            elif key_textw in K["ScrollDown"] and y < totlines - hi + 6:
                y += 1
            elif key_textw in K["PageUp"]:
                y = pgup(y, padhi)
            elif key_textw in K["PageDown"]:
                y = pgdn(y, totlines, padhi)
            elif key_textw in K["BeginningOfCh"]:
                y = 0
            elif key_textw in K["EndOfCh"]:
                y = pgend(totlines, padhi)
            elif key_textw in {curses.KEY_RESIZE}|WINKEYS - key:
                return key_textw
            pad.refresh(y, 0, 6, 5, rows - 5, cols - 5)
            key_textw = textw.getch()

        textw.clear()
        textw.refresh()
        return
    return wrapper


def choice_win(listgen):
    @wraps(listgen)
    def wrapper(*args, **kwargs):
        rows, cols = SCREEN.getmaxyx()
        hi, wi = rows - 4, cols - 4
        Y, X = 2, 2
        chwin = curses.newwin(hi, wi, Y, X)
        if COLORSUPPORT:
            chwin.bkgd(SCREEN.getbkgd())

        title, ch_list, index, key = listgen(*args, **kwargs)

        chwin.box()
        chwin.keypad(True)
        chwin.addstr(1, 2, title)
        chwin.addstr(2, 2, "-"*len(title))
        key_chwin = 0

        totlines = len(ch_list)
        chwin.refresh()
        pad = curses.newpad(totlines, wi - 2)
        if COLORSUPPORT:
            pad.bkgd(SCREEN.getbkgd())

        pad.keypad(True)

        padhi = rows - 5 - Y - 4 + 1
        y = 0
        if index in range(padhi//2, totlines - padhi//2):
            y = index - padhi//2 + 1
        span = []

        for n, i in enumerate(ch_list):
            # strs = "  " + str(n+1).rjust(d) + " " + i[0]
            strs = "  " + i
            strs = strs[0:wi-3]
            pad.addstr(n, 0, strs)
            span.append(len(strs))

        countstring = ""
        while key_chwin not in K["Quit"]|key:
            if countstring == "":
                count = 1
            else:
                count = int(countstring)
            if key_chwin in range(48, 58): # i.e., k is a numeral
                countstring = countstring + chr(key_chwin)
            else:
                if key_chwin in K["ScrollUp"] or key_chwin in K["PageUp"]:
                    index -= count
                    if index < 0:
                        index = 0
                elif key_chwin in K["ScrollDown"] or key_chwin in K["PageDown"]:
                    index += count
                    if index + 1 >= totlines:
                        index = totlines - 1
                elif key_chwin in K["Follow"]:
                    return index
                # elif key_chwin in K["PageUp"]:
                #     index -= 3
                #     if index < 0:
                #         index = 0
                # elif key_chwin in K["PageDown"]:
                #     index += 3
                #     if index >= totlines:
                #         index = totlines - 1
                elif key_chwin in K["BeginningOfCh"]:
                    index = 0
                elif key_chwin in K["EndOfCh"]:
                    index = totlines - 1
                elif key_chwin in {curses.KEY_RESIZE}|WINKEYS - key:
                    return key_chwin
                countstring = ""

            while index not in range(y, y+padhi):
                if index < y:
                    y -= 1
                else:
                    y += 1

            for n in range(totlines):
                att = curses.A_REVERSE if index == n else curses.A_NORMAL
                pre = ">>" if index == n else "  "
                pad.addstr(n, 0, pre)
                pad.chgat(n, 0, span[n], pad.getbkgd() | att)

            pad.refresh(y, 0, Y+4, X+4, rows - 5, cols - 6)
            key_chwin = chwin.getch()

        chwin.clear()
        chwin.refresh()
        return
    return wrapper


def loadstate():
    global CFG, STATE, CFGFILE, STATEFILE
    prefix = ""
    if os.getenv("HOME") is not None:
        homedir = os.getenv("HOME")
        if os.path.isdir(os.path.join(homedir, ".config")):
            prefix = os.path.join(homedir, ".config", "epy")
        else:
            prefix = os.path.join(homedir, ".epy")
    elif os.getenv("USERPROFILE") is not None:
        prefix = os.path.join(os.getenv("USERPROFILE"), ".epy")
    else:
        CFGFILE = os.devnull
        STATEFILE = os.devnull
    os.makedirs(prefix, exist_ok=True)
    CFGFILE = os.path.join(prefix, "config.json")
    STATEFILE = os.path.join(prefix, "state.json")

    try:
        with open(CFGFILE) as f:
            CFG = json.load(f)
        with open(STATEFILE) as f:
            STATE = json.load(f)
    except FileNotFoundError:
        pass


def parse_keys():
    global WINKEYS
    for i in K.keys():
        K[i] = K[i]|{ord(CFG["Keys"][i])}
    WINKEYS = K["Metadata"]|K["Help"]|K["ToC"]


def savestate(file, index, width, pos, pctg):
    with open(CFGFILE, "w") as f:
        json.dump(CFG, f, indent=2)
    STATE["LastRead"] = file
    STATE["States"][file]["index"] = index
    STATE["States"][file]["width"] = width
    STATE["States"][file]["pos"] = pos
    STATE["States"][file]["pctg"] = pctg
    with open(STATEFILE, "w") as f:
        json.dump(STATE, f, indent=4)


def pgup(pos, winhi, preservedline=0, c=1):
    if pos >= (winhi - preservedline) * c:
        return pos - (winhi + preservedline) * c
    else:
        return 0


def pgdn(pos, tot, winhi, preservedline=0, c=1):
    if pos + (winhi * c) <= tot - winhi:
        return pos + (winhi * c)
    else:
        pos = tot - winhi
        if pos < 0:
            return 0
        return pos


def pgend(tot, winhi):
    if tot - winhi >= 0:
        return tot - winhi
    else:
        return 0


@choice_win
def toc(src, index):
    return "Table of Contents", src, index, K["ToC"]


@text_win
def meta(ebook):
    mdata = ""
    for i in ebook.get_meta():
        data = re.sub("<[^>]*>", "", i[1])
        mdata += i[0].upper() + ": " + data + "\n"
        data = re.sub("\t", "", data)
        # mdata += textwrap.wrap(i[0].upper() + ": " + data, wi - 6)
    return "Metadata", mdata, K["Metadata"]


@text_win
def help():
    # src = re.search("Key Bind(\n|.)*", __doc__).group()
    src = "Key Bindings\n"
    for i in CFG["Keys"].keys():
        src += "  " + i + ": " + CFG["Keys"][i] + "\n"
    return "Help", src, K["Help"]


def dots_path(curr, tofi):
    candir = curr.split("/")
    tofi = tofi.split("/")
    alld = tofi.count("..")
    t = len(candir)
    candir = candir[0:t-alld-1]
    try:
        while True:
            tofi.remove("..")
    except ValueError:
        pass
    return "/".join(candir+tofi)


def find_media_viewer():
    global VWR
    if shutil.which(CFG["DefaultViewer"]) is not None:
        VWR = CFG["DefaultViewer"]
    elif sys.platform == "win32":
        VWR = "start"
    elif sys.platform == "darwin":
        VWR = "open"
    else:
        VWR_LIST = [
            "feh",
            "gio",
            "gnome-open",
            "gvfs-open",
            "xdg-open",
            "kde-open",
            "firefox"
        ]
        for i in VWR_LIST:
            if shutil.which(i) is not None:
                VWR = i
                break

    if VWR in {"gio"}:
        VWR += " open"


def open_media(scr, epub, src):
    sfx = os.path.splitext(src)[1]
    fd, path = tempfile.mkstemp(suffix=sfx)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(epub.file.read(src))
        # run(VWR + " " + path, shell=True)
        subprocess.call(
            VWR + " " + path,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        k = scr.getch()
    finally:
        os.remove(path)
    return k


def searching(pad, src, width, y, ch, tot):
    global SEARCHPATTERN
    rows, cols = SCREEN.getmaxyx()
    x = (cols - width) // 2
    if SEARCHPATTERN is None:
        stat = curses.newwin(1, cols, rows-1, 0)
        if COLORSUPPORT:
            stat.bkgd(SCREEN.getbkgd())
        stat.keypad(True)
        curses.echo(1)
        curses.curs_set(1)
        SEARCHPATTERN = ""
        stat.addstr(0, 0, " Regex:", curses.A_REVERSE)
        stat.addstr(0, 7, SEARCHPATTERN)
        stat.refresh()
        while True:
            ipt = stat.getch()
            if ipt == 27:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                curses.curs_set(0)
                SEARCHPATTERN = None
                return y
            elif ipt == 10:
                SEARCHPATTERN = "/"+SEARCHPATTERN
                stat.clear()
                stat.refresh()
                curses.echo(0)
                curses.curs_set(0)
                break
            # TODO: why different behaviour unix dos or win lin
            elif ipt in {8, curses.KEY_BACKSPACE}:
                SEARCHPATTERN = SEARCHPATTERN[:-1]
            elif ipt == curses.KEY_RESIZE:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                curses.curs_set(0)
                SEARCHPATTERN = None
                return curses.KEY_RESIZE
            else:
                SEARCHPATTERN += chr(ipt)

            stat.clear()
            stat.addstr(0, 0, " Regex:", curses.A_REVERSE)
            stat.addstr(0, 7, SEARCHPATTERN)
            stat.refresh()

    if SEARCHPATTERN in {"?", "/"}:
        SEARCHPATTERN = None
        return y

    found = []
    pattern = re.compile(SEARCHPATTERN[1:], re.IGNORECASE)
    for n, i in enumerate(src):
        for j in pattern.finditer(i):
            found.append([n, j.span()[0], j.span()[1] - j.span()[0]])

    if found == []:
        if SEARCHPATTERN[0] == "/" and ch + 1 < tot:
            return 1
        elif SEARCHPATTERN[0] == "?" and ch > 0:
            return -1
        else:
            s = 0
            while True:
                if s in K["Quit"]:
                    SEARCHPATTERN = None
                    SCREEN.clear()
                    SCREEN.refresh()
                    return y
                elif s == ord("n") and ch == 0:
                    SEARCHPATTERN = "/"+SEARCHPATTERN[1:]
                    return 1
                elif s == ord("N") and ch + 1 == tot:
                    SEARCHPATTERN = "?"+SEARCHPATTERN[1:]
                    return -1

                SCREEN.clear()
                SCREEN.addstr(
                    rows-1, 0,
                    " Finished searching: " + SEARCHPATTERN[1:] + " ",
                    curses.A_REVERSE
                )
                SCREEN.refresh()
                pad.refresh(y, 0, 0, x, rows-2, x+width)
                s = pad.getch()

    sidx = len(found) - 1
    if SEARCHPATTERN[0] == "/":
        if y > found[-1][0]:
            return 1
        for n, i in enumerate(found):
            if i[0] >= y:
                sidx = n
                break

    s = 0
    msg = " Searching: "\
        + SEARCHPATTERN[1:]\
        + " --- Res {}/{} Ch {}/{} ".format(
            sidx + 1,
            len(found),
            ch+1, tot
        )
    while True:
        if s in K["Quit"]:
            SEARCHPATTERN = None
            for i in found:
                pad.chgat(i[0], i[1], i[2], pad.getbkgd())
            SCREEN.clear()
            SCREEN.refresh()
            return y
        elif s == ord("n"):
            SEARCHPATTERN = "/"+SEARCHPATTERN[1:]
            if sidx == len(found) - 1:
                if ch + 1 < tot:
                    return 1
                else:
                    s = 0
                    msg = " Finished searching: " + SEARCHPATTERN[1:] + " "
                    continue
            else:
                sidx += 1
                msg = " Searching: "\
                    + SEARCHPATTERN[1:]\
                    + " --- Res {}/{} Ch {}/{} ".format(
                        sidx + 1,
                        len(found),
                        ch+1, tot
                    )
        elif s == ord("N"):
            SEARCHPATTERN = "?"+SEARCHPATTERN[1:]
            if sidx == 0:
                if ch > 0:
                    return -1
                else:
                    s = 0
                    msg = " Finished searching: " + SEARCHPATTERN[1:] + " "
                    continue
            else:
                sidx -= 1
                msg = " Searching: "\
                    + SEARCHPATTERN[1:]\
                    + " --- Res {}/{} Ch {}/{} ".format(
                        sidx + 1,
                        len(found),
                        ch+1, tot
                    )
        elif s == curses.KEY_RESIZE:
            return s

        while found[sidx][0] not in list(range(y, y+rows-1)):
            if found[sidx][0] > y:
                y += rows - 1
            else:
                y -= rows - 1
                if y < 0:
                    y = 0

        for n, i in enumerate(found):
            attr = curses.A_REVERSE if n == sidx else curses.A_NORMAL
            pad.chgat(i[0], i[1], i[2], pad.getbkgd() | attr)

        SCREEN.clear()
        SCREEN.addstr(rows-1, 0, msg, curses.A_REVERSE)
        SCREEN.refresh()
        pad.refresh(y, 0, 0, x, rows-2, x+width)
        s = pad.getch()


def find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y):
    ntoc = 0
    for n, (i, j) in enumerate(zip(toc_idx, toc_sect)):
        if i <= index:
            if y >= toc_secid.get(j, 0):
                ntoc = n
        else:
            break
    return ntoc


def reader(ebook, index, width, y, pctg, sect):
    global SHOWPROGRESS

    k = 0 if SEARCHPATTERN is None else ord("/")
    rows, cols = SCREEN.getmaxyx()
    x = (cols - width) // 2

    contents = ebook.contents
    toc_name = ebook.toc_entries[0]
    toc_idx = ebook.toc_entries[1]
    toc_sect = ebook.toc_entries[2]
    toc_secid = {}
    chpath = contents[index]
    content = ebook.file.open(chpath).read()
    content = content.decode("utf-8")

    parser = HTMLtoLines(set(toc_sect))
    # parser = HTMLtoLines()
    # try:
    parser.feed(content)
    parser.close()
    # except:
    #     pass

    src_lines, imgs, toc_secid = parser.get_lines(width)
    totlines = len(src_lines)

    if y < 0 and totlines <= rows:
        y = 0
    elif pctg is not None:
        y = round(pctg*totlines)
    else:
        y = y % totlines

    pad = curses.newpad(totlines, width + 2)  # + 2 unnecessary
    if COLORSUPPORT:
        pad.bkgd(SCREEN.getbkgd())

    pad.keypad(True)

    LOCALPCTG = []
    for n, i in enumerate(src_lines):
        if re.search("\\[IMG:[0-9]+\\]", i):
            pad.addstr(n, width//2 - len(i)//2, i, curses.A_REVERSE)
        else:
            pad.addstr(n, 0, i)
        if CFG["EnableProgressIndicator"]:
            LOCALPCTG.append(len(re.sub("\s", "", i)))

    if CFG["EnableProgressIndicator"]:
        TOTALPCTG = sum(PERCENTAGE)
        TOTALLOCALPCTG = sum(PERCENTAGE[:index])

    SCREEN.clear()
    SCREEN.refresh()
    # try except to be more flexible on terminal resize
    try:
        pad.refresh(y, 0, 0, x, rows-1, x+width)
    except curses.error:
        pass

    if sect != "":
        y = toc_secid.get(sect, 0)

    countstring = ""
    while True:
        if countstring == "":
            count = 1
        else:
            count = int(countstring)
        if k in range(48, 58): # i.e., k is a numeral
            countstring = countstring + chr(k)
        else:
            if k in K["Quit"]:
                if k == 27 and countstring != "":
                    countstring = ""
                else:
                    savestate(ebook.path, index, width, y, y/totlines)
                    sys.exit()
            elif k in K["ScrollUp"]:
                if y >= count:
                    y -= count
                elif index != 0:
                    return -1, width, -rows, None, ""
            elif k in K["PageUp"]:
                if y == 0 and index != 0:
                    return -1, width, -rows, None, ""
                else:
                    y = pgup(y, rows, LINEPRSRV, count)
            elif k in K["ScrollDown"]:
                if y + count <= totlines - rows:
                    y += count
                elif index != len(contents)-1:
                    return 1, width, 0, None, ""
            elif k in K["PageDown"]:
                if totlines - y - LINEPRSRV > rows:
                    y += rows - LINEPRSRV
                    # SCREEN.clear()
                    # SCREEN.refresh()
                elif index != len(contents)-1:
                    return 1, width, 0, None, ""
            elif k in K["NextChapter"]:
                ntoc = find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y)
                if ntoc < len(toc_idx) - 1:
                    if index == toc_idx[ntoc+1]:
                        try:
                            y = toc_secid[toc_sect[ntoc+1]]
                        except KeyError:
                            pass
                    else:
                        return toc_idx[ntoc+1]-index, width, 0, None, toc_sect[ntoc+1]
            elif k in K["PrevChapter"]:
                ntoc = find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y)
                if ntoc > 0:
                    if index == toc_idx[ntoc-1]:
                        y = toc_secid.get(toc_sect[ntoc-1], 0)
                    else:
                        return toc_idx[ntoc-1]-index, width, 0, None, toc_sect[ntoc-1]
            elif k in K["BeginningOfCh"]:
                ntoc = find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y)
                try:
                    y = toc_secid[toc_sect[ntoc]]
                except KeyError:
                    y = 0
            elif k in K["EndOfCh"]:
                ntoc = find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y)
                try:
                    if toc_secid[toc_sect[ntoc+1]] - rows >= 0:
                        y = toc_secid[toc_sect[ntoc+1]] - rows
                    else:
                        y = toc_secid[toc_sect[ntoc]]
                except (KeyError, IndexError):
                    y = pgend(totlines, rows)
            elif k in K["ToC"]:
                ntoc = find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y)
                fllwd = toc(toc_name, ntoc)
                if fllwd is not None:
                    if fllwd in {curses.KEY_RESIZE}|K["Help"]|K["Metadata"]:
                        k = fllwd
                        continue
                    if index == toc_idx[fllwd]:
                        try:
                            y = toc_secid[toc_sect[fllwd]]
                        except KeyError:
                            y = 0
                    else:
                        return toc_idx[fllwd] - index, width, 0, None, toc_sect[fllwd]
            elif k in K["Metadata"]:
                k = meta(ebook)
                if k in {curses.KEY_RESIZE}|K["Help"]|K["ToC"]:
                    continue
            elif k in K["Help"]:
                k = help()
                if k in {curses.KEY_RESIZE}|K["Metadata"]|K["ToC"]:
                    continue
            elif k in K["Enlarge"] and (width + count) < cols - 2:
                width += count
                return 0, width, 0, y/totlines, ""
            elif k in K["Shrink"] and width >= 22:
                width -= count
                return 0, width, 0, y/totlines, ""
            elif k in K["SetWidth"]:
                if countstring == "":
                    # if called without a count, toggle between 80 cols and full width
                    if width != 80 and cols - 2 >= 80:
                        return 0, 80, 0, y/totlines, ""
                    else:
                        return 0, cols - 2, 0, y/totlines, ""
                else:
                    width = count
                if width < 20:
                    width = 20
                elif width >= cols -2:
                    width = cols - 2
                return 0, width, 0, y/totlines, ""
            # elif k == ord("0"):
            #     if width != 80 and cols - 2 >= 80:
            #         return 0, 80, 0, y/totlines, ""
            #     else:
            #         return 0, cols - 2, 0, y/totlines, ""
            elif k in K["RegexSearch"]:
                fs = searching(
                    pad,
                    src_lines,
                    width, y,
                    index, len(contents)
                )
                if fs == curses.KEY_RESIZE:
                    k = fs
                    continue
                elif SEARCHPATTERN is not None:
                    return fs, width, 0, None, ""
                else:
                    y = fs
            elif k in K["OpenImage"] and VWR is not None:
                gambar, idx = [], []
                for n, i in enumerate(src_lines[y:y+rows]):
                    img = re.search("(?<=\\[IMG:)[0-9]+(?=\\])", i)
                    if img is not None:
                        gambar.append(img.group())
                        idx.append(n)

                impath = ""
                if len(gambar) == 1:
                    impath = imgs[int(gambar[0])]
                elif len(gambar) > 1:
                    p, i = 0, 0
                    while p not in K["Quit"] and p not in K["Follow"]:
                        SCREEN.move(idx[i], x + width//2 + len(gambar[i]) + 1)
                        SCREEN.refresh()
                        curses.curs_set(1)
                        p = pad.getch()
                        if p in K["ScrollDown"]:
                            i += 1
                        elif p in K["ScrollUp"]:
                            i -= 1
                        i = i % len(gambar)

                    curses.curs_set(0)
                    if p in K["Follow"]:
                        impath = imgs[int(gambar[i])]

                if impath != "":
                    imgsrc = dots_path(chpath, impath)
                    k = open_media(pad, ebook, imgsrc)
                    continue
            elif k in K["SwitchColor"] and COLORSUPPORT and countstring in {"", "0", "1", "2"}:
                if countstring == "":
                    count_color = curses.pair_number(SCREEN.getbkgd())
                    if count_color not in {2, 3}: count_color = 1
                    count_color = count_color % 3
                else:
                    count_color = count
                SCREEN.bkgd(curses.color_pair(count_color+1))
                return 0, width, y, None, ""
            elif k in K["ShowHideProgress"] and CFG["EnableProgressIndicator"]:
                SHOWPROGRESS = not SHOWPROGRESS
            elif k == curses.KEY_RESIZE:
                savestate(ebook.path, index, width, y, y/totlines)
                # stated in pypi windows-curses page:
                # to call resize_term right after KEY_RESIZE
                if sys.platform == "win32":
                    curses.resize_term(rows, cols)
                    rows, cols = SCREEN.getmaxyx()
                else:
                    rows, cols = SCREEN.getmaxyx()
                    curses.resize_term(rows, cols)
                if cols <= width:
                    return 0, cols - 2, 0, y/totlines, ""
                else:
                    return 0, width, y, None, ""
            countstring = ""

        if CFG["EnableProgressIndicator"]:
            PROGRESS = (TOTALLOCALPCTG + sum(LOCALPCTG[:y+rows-1])) / TOTALPCTG
            PROGRESSTR = "{}%".format(int(PROGRESS*100))

        try:
            SCREEN.clear()
            SCREEN.addstr(0, 0, countstring)
            if SHOWPROGRESS and (cols-width-2)//2 > 3:
                SCREEN.addstr(0, cols-len(PROGRESSTR), PROGRESSTR)
            SCREEN.refresh()
            if totlines - y < rows:
                pad.refresh(y, 0, 0, x, totlines-y, x+width)
            else:
                pad.refresh(y, 0, 0, x, rows-1, x+width)
        except curses.error:
            pass
        k = pad.getch()


def preread(stdscr, file):
    global COLORSUPPORT, SHOWPROGRESS, PERCENTAGE, SCREEN

    curses.use_default_colors()
    try:
        curses.init_pair(1, -1, -1)
        curses.init_pair(2, CFG["DarkColorFG"], CFG["DarkColorBG"])
        curses.init_pair(3, CFG["LightColorFG"], CFG["LightColorBG"])
        COLORSUPPORT = True
    except:
        COLORSUPPORT  = False

    SCREEN = stdscr

    SCREEN.keypad(True)
    curses.curs_set(0)
    SCREEN.clear()
    rows, cols = SCREEN.getmaxyx()
    SCREEN.addstr(rows-1, 0, "Loading...")
    SCREEN.refresh()

    epub = Epub(file)

    if epub.path in STATE["States"]:
        idx = STATE["States"][epub.path]["index"]
        width = STATE["States"][epub.path]["width"]
        y = STATE["States"][epub.path]["pos"]
    else:
        STATE["States"][epub.path] = {}
        idx = 0
        y = 0
        width = 80
    pctg = None

    if cols <= width:
        width = cols - 2
        pctg = STATE["States"][epub.path].get("pctg", None)

    epub.initialize()
    find_media_viewer()
    parse_keys()

    SHOWPROGRESS = CFG["EnableProgressIndicator"]
    if SHOWPROGRESS:
        for i in epub.contents:
            content = epub.file.open(i).read()
            content = content.decode("utf-8")
            parser = HTMLtoLines()
            # try:
            parser.feed(content)
            parser.close()
            # except:
            #     pass
            src_lines = parser.get_lines()
            PERCENTAGE.append(sum([len(re.sub("\s", "", j)) for j in src_lines]))

    sec = ""
    while True:
        incr, width, y, pctg, sec = reader(
            epub, idx, width, y, pctg, sec
        )
        idx += incr


def main():
    args = []
    if sys.argv[1:] != []:
        args += sys.argv[1:]

    if len({"-h", "--help"} & set(args)) != 0:
        hlp = __doc__.rstrip()
        if "-h" in args:
            hlp = re.search("(\n|.)*(?=\n\nKey)", hlp).group()
        print(hlp)
        sys.exit()

    loadstate()

    if len({"-v", "--version", "-V"} & set(args)) != 0:
        print("Startup file loaded:")
        print(CFGFILE)
        print(STATEFILE)
        print()
        print("v" + __version__)
        print(__license__, "License")
        print("Copyright (c) 2019", __author__)
        print(__url__)
        sys.exit()

    if len({"-d"} & set(args)) != 0:
        args.remove("-d")
        dump = True
    else:
        dump = False

    if args == []:
        file = STATE["LastRead"]
        if not os.path.isfile(file):
            # print(__doc__)
            sys.exit("ERROR: Found no last read file.")

    elif os.path.isfile(args[0]):
        file = args[0]

    else:
        file = None
        todel = []
        xitmsg = 0

        val = 0
        for i in STATE["States"].keys():
            if not os.path.exists(i):
                todel.append(i)
            else:
                match_val = sum([
                    j.size for j in SM(
                        None, i.lower(), " ".join(args).lower()
                    ).get_matching_blocks()
                ])
                if match_val >= val:
                    val = match_val
                    file = i
        if val == 0:
            xitmsg = "\nERROR: No matching file found in history."

        for i in todel:
            del STATE["States"][i]
        with open(STATEFILE, "w") as f:
            json.dump(STATE, f, indent=4)

        if len(args) == 1 and re.match(r"[0-9]+", args[0]) is not None:
            try:
                file = list(STATE["States"].keys())[int(args[0])-1]
                xitmsg = 0
            except IndexError:
                xitmsg = "\nERROR: No matching file found in history."

        if xitmsg != 0 or "-r" in args:
            print("Reading history:")
            dig = len(str(len(STATE["States"].keys())+1))
            for n, i in enumerate(STATE["States"].keys()):
                print(str(n+1).rjust(dig)
                      + ("* " if STATE["LastRead"] == i else "  ") + i)
            sys.exit(xitmsg)

    if dump:
        epub = Epub(file)
        epub.initialize()
        for i in epub.contents:
            content = epub.file.open(i).read()
            content = content.decode("utf-8")
            parser = HTMLtoLines()
            # try:
            parser.feed(content)
            parser.close()
            # except:
            #     pass
            src_lines = parser.get_lines()
            # sys.stdout.reconfigure(encoding="utf-8")  # Python>=3.7
            for j in src_lines:
                sys.stdout.buffer.write((j+"\n\n").encode("utf-8"))
        sys.exit()

    else:
        curses.wrapper(preread, file)


if __name__ == "__main__":
    main()
