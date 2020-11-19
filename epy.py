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


__version__ = "2020.11.19"
__license__ = "GPL-3.0"
__author__ = "Benawi Adha"
__email__ = "benawiadha@gmail.com"
__url__ = "https://github.com/wustho/epy"


import base64
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
import multiprocessing
import xml.etree.ElementTree as ET
from urllib.parse import unquote
from html import unescape
from html.parser import HTMLParser
from difflib import SequenceMatcher as SM
from functools import wraps

try:
    import mobi
    MOBISUPPORT = True
except ModuleNotFoundError:
    MOBISUPPORT = False


# -1 is default terminal fg/bg colors
CFG = {
    "DefaultViewer": "auto",
    "DictionaryClient": "auto",
    "ShowProgressIndicator": True,
    "DarkColorFG": 252,
    "DarkColorBG": 235,
    "LightColorFG": 238,
    "LightColorBG": 253,
    "Keys": {
        "ScrollUp": "k",
        "ScrollDown": "j",
        "PageUp": "h",
        "PageDown": "l",
        "HalfScreenUp": "^u",
        "HalfScreenDown": "C-d",
        "NextChapter": "n",
        "PrevChapter": "p",
        "BeginningOfCh": "g",
        "EndOfCh": "G",
        "Shrink": "-",
        "Enlarge": "+",
        "SetWidth": "=",
        "Metadata": "M",
        "DefineWord": "d",
        "TableOfContents": "t",
        "Follow": "f",
        "OpenImage": "o",
        "RegexSearch": "/",
        "ShowHideProgress": "s",
        "MarkPosition": "m",
        "JumpToPosition": "`",
        "AddBookmark": "b",
        "ShowBookmarks": "B",
        "Quit": "q",
        "Help": "?",
        "SwitchColor": "c"
    }
}
STATE = {
    "LastRead": "",
    "States": {}
}
# default keys
K = {
    "ScrollUp": {curses.KEY_UP},
    "ScrollDown": {curses.KEY_DOWN},
    "PageUp": {curses.KEY_PPAGE, curses.KEY_LEFT},
    "PageDown": {curses.KEY_NPAGE, ord(" "), curses.KEY_RIGHT},
    "BeginningOfCh": {curses.KEY_HOME},
    "EndOfCh": {curses.KEY_END},
    "TableOfContents": {9, ord("\t")},
    "Follow": {10},
    "Quit": {3, 27, 304}
}
WINKEYS = set()
CFGFILE = ""
STATEFILE = ""
COLORSUPPORT = False
LINEPRSRV = 0  # 2
SEARCHPATTERN = None
VWR = None
DICT = None
SCREEN = None
JUMPLIST = {}
SHOWPROGRESS = CFG["ShowProgressIndicator"]
MULTIPROC = False if multiprocessing.cpu_count() == 1 else True
ALLPREVLETTERS = []
SUMALLLETTERS = 0
PROC_COUNTLETTERS = None


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

    def get_meta(self):
        meta = []
        # why self.file.read(self.rootfile) problematic
        cont = ET.fromstring(self.file.open(self.rootfile).read())
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append([re.sub("{.*?}", "", i.tag), i.text])
        return meta

    def initialize(self):
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

        # cont = ET.parse(self.file.open(self.rootfile)).getroot()
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
            try:
                idx = contents.index(unquote(src[0]))
            except ValueError:
                continue
            self.toc_entries[0].append(name)
            self.toc_entries[1].append(idx)
            if len(src) == 2:
                self.toc_entries[2].append(src[1])
            elif len(src) == 1:
                self.toc_entries[2].append("")

    def get_raw_text(self, chpath):
        # using try-except block to catch
        # zlib.error: Error -3 while decompressing data: invalid distance too far back
        # caused by forking PROC_COUNTLETTERS
        while True:
            try:
                content = self.file.open(chpath).read()
                break
            except:
                continue
        return content.decode("utf-8")

    def get_img_bytestr(self, impath):
        return impath, self.file.read(impath)

    def cleanup(self):
        return


class Mobi(Epub):
    def __init__(self, filemobi):
        self.path = os.path.abspath(filemobi)
        self.file, _ = mobi.extract(filemobi)

    def get_meta(self):
        meta = []
        # why self.file.read(self.rootfile) problematic
        with open(os.path.join(self.rootdir, "content.opf")) as f:
            cont = ET.parse(f).getroot()
        for i in cont.findall("OPF:metadata/*", self.NS):
            if i.text is not None:
                meta.append([re.sub("{.*?}", "", i.tag), i.text])
        return meta

    def initialize(self):
        self.rootdir = os.path.join(self.file, "mobi7")
        self.toc = os.path.join(self.rootdir, "toc.ncx")
        self.version = "2.0"

        self.contents = []
        self.toc_entries = [[], [], []]

        with open(os.path.join(self.rootdir, "content.opf")) as f:
            cont = ET.parse(f).getroot()
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
                    self.contents.append(os.path.join(self.rootdir, unquote(j[1])))
                    contents.append(unquote(j[1]))
                    manifest.remove(j)
                    # TODO: test is break necessary
                    break

        with open(self.toc) as f:
            toc = ET.parse(f).getroot()
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
            try:
                idx = contents.index(unquote(src[0]))
            except ValueError:
                continue
            self.toc_entries[0].append(name)
            self.toc_entries[1].append(idx)
            if len(src) == 2:
                self.toc_entries[2].append(src[1])
            elif len(src) == 1:
                self.toc_entries[2].append("")

    def get_raw_text(self, chpath):
        # using try-except block to catch
        # zlib.error: Error -3 while decompressing data: invalid distance too far back
        # caused by forking PROC_COUNTLETTERS
        while True:
            try:
                with open(chpath) as f:
                    content = f.read()
                break
            except:
                continue
        # return content.decode("utf-8")
        return content

    def cleanup(self):
        shutil.rmtree(self.file)
        return


class Azw3(Epub):
    def __init__(self, fileepub):
        self.path = os.path.abspath(fileepub)
        self.tmpdir, self.tmpepub = mobi.extract(fileepub)
        self.file = zipfile.ZipFile(self.tmpepub, "r")

    def cleanup(self):
        shutil.rmtree(self.tmpdir)
        return


class FictionBook:
    NS = {
        "FB2": "http://www.gribuser.ru/xml/fictionbook/2.0"
    }

    def __init__(self, filefb):
        self.path = os.path.abspath(filefb)
        self.file = filefb

    def get_meta(self):
        desc = self.root.find("FB2:description", self.NS)
        alltags = desc.findall("*/*")
        return [[re.sub("{.*?}", "", i.tag), " ".join(i.itertext())] for i in alltags]

    def initialize(self):
        cont = ET.parse(self.file)
        self.root = cont.getroot()

        self.contents = []
        self.toc_entries = [[], [], []]

        self.contents = self.root.findall("FB2:body/*", self.NS)
        # TODO
        for n, i in enumerate(self.contents):
            title = i.find("FB2:title", self.NS)
            if title is not None:
                self.toc_entries[0].append("".join(title.itertext()))
                self.toc_entries[1].append(n)
                self.toc_entries[2].append("")

    def get_raw_text(self, node):
        ET.register_namespace("", "http://www.gribuser.ru/xml/fictionbook/2.0")
        # sys.exit(ET.tostring(node, encoding="utf8", method="html").decode("utf-8").replace("ns1:",""))
        return ET.tostring(node, encoding="utf8", method="html").decode("utf-8").replace("ns1:","")

    def get_img_bytestr(self, imgid):
        imgid = imgid.replace("#", "")
        img = self.root.find("*[@id='{}']".format(imgid))
        imgtype = img.get("content-type").split("/")[1]
        return imgid+"."+imgtype, base64.b64decode(img.text)

    def cleanup(self):
        return


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
                # if i[0] == "xlink:href":
                if i[0].endswith("href"):
                    self.text.append("[IMG:{}]".format(len(self.imgs)))
                    self.imgs.append(unquote(i[1]))
        if self.sects != {""}:
            for i in attrs:
                if i[0] == "id" and i[1] in self.sects:
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
        # sometimes attribute "id" is inside "startendtag"
        # especially html from mobi module (kindleunpack fork)
        if self.sects != {""}:
            for i in attrs:
                if i[0] == "id" and i[1] in self.sects:
                    self.text[-1] += " (#" + i[1] + ") "

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


class Board:
    MAXCHUNKS = 32000  # lines

    def __init__(self, totlines, width):
        self.chunks = [self.MAXCHUNKS*(i+1)-1 for i in range(totlines // self.MAXCHUNKS)]
        self.chunks += [] if totlines % self.MAXCHUNKS == 0 else [totlines % self.MAXCHUNKS + (0 if self.chunks == [] else self.chunks[-1])] # -1
        self.pad = curses.newpad(min([self.MAXCHUNKS, totlines]), width)
        self.pad.keypad(True)
        # self.current_chunk = 0
        self.y = 0
        self.width = width

    def feed(self, textlist):
        self.text = textlist

    def getch(self):
        return self.pad.getch()

    def bkgd(self, bg):
        self.pad.bkgd(SCREEN.getbkgd())

    def find_chunkidx(self, y):
        for n, i in enumerate(self.chunks):
            if y <= i:
                return n

    def paint_text(self, chunkidx=0):
        self.pad.clear()
        start_chunk = 0 if chunkidx == 0 else self.chunks[chunkidx-1]+1
        end_chunk = self.chunks[chunkidx]
        for n, i in enumerate(self.text[start_chunk:end_chunk+1]):
            if re.search("\\[IMG:[0-9]+\\]", i):
                self.pad.addstr(n, self.width//2 - len(i)//2 + 1, i, curses.A_REVERSE)
            else:
                self.pad.addstr(n, 0, i)
        # chapter suffix
        ch_suffix = "***"  # "\u3064\u3065\u304f" つづく
        try:
            self.pad.addstr(n+1, (self.width - len(ch_suffix))//2 + 1, ch_suffix)
        except curses.error:
            pass

    def chgat(self, y, x, n, attr):
        chunkidx = self.find_chunkidx(y)
        start_chunk = 0 if chunkidx == 0 else self.chunks[chunkidx-1]+1
        end_chunk = self.chunks[chunkidx]
        if y in range(start_chunk, end_chunk+1):
            self.pad.chgat(y % self.MAXCHUNKS, x, n, attr)

    def getbkgd(self):
        return self.pad.getbkgd()

    def refresh(self, y, b, c, d, e, f):
        chunkidx = self.find_chunkidx(y)
        if chunkidx != self.find_chunkidx(self.y):
            self.paint_text(chunkidx)
        # TODO: not modulo by self.MAXCHUNKS but self.pad.height
        self.pad.refresh(y % self.MAXCHUNKS, b, c, d, e, f)
        self.y = y


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

        if len(title) > cols-8:
            title = title[:cols-8]

        texts = []
        for i in raw_texts.splitlines():
            texts += textwrap.wrap(i, wi - 6, drop_whitespace=False)

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
            elif key_textw in WINKEYS - key:
                textw.clear()
                textw.refresh()
                return key_textw
            pad.refresh(y, 0, 6, 5, rows - 5, cols - 5)
            key_textw = textw.getch()

        textw.clear()
        textw.refresh()
        return
    return wrapper


def choice_win(allowdel=False):
    def inner_f(listgen):
        @wraps(listgen)
        def wrapper(*args, **kwargs):
            rows, cols = SCREEN.getmaxyx()
            hi, wi = rows - 4, cols - 4
            Y, X = 2, 2
            chwin = curses.newwin(hi, wi, Y, X)
            if COLORSUPPORT:
                chwin.bkgd(SCREEN.getbkgd())

            title, ch_list, index, key = listgen(*args, **kwargs)

            if len(title) > cols-8:
                title = title[:cols-8]

            chwin.box()
            chwin.keypad(True)
            chwin.addstr(1, 2, title)
            chwin.addstr(2, 2, "-"*len(title))
            if allowdel:
                chwin.addstr(3, 2, "HINT: Press 'd' to delete.")
            key_chwin = 0

            totlines = len(ch_list)
            chwin.refresh()
            pad = curses.newpad(totlines, wi - 2)
            if COLORSUPPORT:
                pad.bkgd(SCREEN.getbkgd())

            pad.keypad(True)

            padhi = rows - 5 - Y - 4 + 1 - (1 if allowdel else 0)
            # padhi = rows - 5 - Y - 4 + 1 - 1
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
                        chwin.clear()
                        chwin.refresh()
                        return None, index, None
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
                    elif key_chwin == ord("D") and allowdel:
                        return None, (0 if index == 0 else index-1), index
                        # chwin.redrawwin()
                        # chwin.refresh()
                    elif key_chwin == ord("d") and allowdel:
                        resk, resp, _ = choice_win()(
                            lambda: ("Delete '{}'?".format(
                                ch_list[index]
                                ), ["(Y)es", "(N)o"], 0, {ord("n")})
                            )()
                        if resk is not None:
                            key_chwin = resk
                            continue
                        elif resp == 0:
                            return None, (0 if index == 0 else index-1), index
                        chwin.redrawwin()
                        chwin.refresh()
                    elif key_chwin in {ord(i) for i in ["Y", "y", "N", "n"]}\
                        and ch_list == ["(Y)es", "(N)o"]:
                        if key_chwin in {ord("Y"), ord("y")}:
                            return None, 0, None
                        else:
                            return None, 1, None
                    elif key_chwin in WINKEYS - key:
                        chwin.clear()
                        chwin.refresh()
                        return key_chwin, index, None
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

                pad.refresh(y, 0, Y+4+(1 if allowdel else 0), X+4, rows - 5, cols - 6)
                # pad.refresh(y, 0, Y+5, X+4, rows - 5, cols - 6)
                key_chwin = chwin.getch()

            chwin.clear()
            chwin.refresh()
            return None, None, None
        return wrapper
    return inner_f


def show_loader(scr):
    scr.clear()
    rows, cols = scr.getmaxyx()
    scr.addstr((rows-1)//2, (cols-1)//2, "\u231B")
    # scr.addstr(((rows-2)//2)+1, (cols-len(msg))//2, msg)
    scr.refresh()


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
    for i in CFG["Keys"].keys():
        parsedk = CFG["Keys"][i]
        if len(parsedk) == 1:
            parsedk = ord(parsedk)
        elif parsedk[:-1] in {"^", "C-"}:
            parsedk = ord(parsedk[-1]) - 96  # Reference: ASCII chars
        else:
            sys.exit("ERROR: Keybindings {}".format(i))

        try:
            K[i].add(parsedk)
        except KeyError:
            K[i] = {parsedk}
    WINKEYS = {curses.KEY_RESIZE}|K["Metadata"]|K["Help"]|\
        K["TableOfContents"]|K["ShowBookmarks"]


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

    if MULTIPROC:
        # PROC_COUNTLETTERS.terminate()
        # PROC_COUNTLETTERS.kill()
        # PROC_COUNTLETTERS.join()
        try:
            PROC_COUNTLETTERS.kill()
        except AttributeError:
            PROC_COUNTLETTERS.terminate()


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


@choice_win()
def toc(src, index):
    return "Table of Contents", src, index, K["TableOfContents"]


@text_win
def meta(ebook):
    mdata = "[File Info]\nPATH: {}\nSIZE: {} MB\n \n[Book Info]\n".format(
        ebook.path, round(os.path.getsize(ebook.path)/1024**2, 2)
    )
    for i in ebook.get_meta():
        data = re.sub("<[^>]*>", "", i[1])
        mdata += i[0].upper() + ": " + data + "\n"
        data = re.sub("\t", "", data)
        # mdata += textwrap.wrap(i[0].upper() + ": " + data, wi - 6)
    return "Metadata", mdata, K["Metadata"]


@text_win
def help():
    src = "Key Bindings:\n"
    dig = max([len(i) for i in CFG["Keys"].values()]) + 2
    for i in CFG["Keys"].keys():
        src += "{}  {}\n".format(
                CFG["Keys"][i].rjust(dig),
                " ".join(re.findall("[A-Z][^A-Z]*", i))
                )
    return "Help", src, K["Help"]


@text_win
def errmsg(title, msg, key):
    return title, msg, key


def bookmarks(ebookpath):
    idx = 0
    while True:
        bmarkslist = [
            i[0] for i in STATE["States"][ebookpath]["bmarks"]
        ]
        if bmarkslist == []:
            return list(K["ShowBookmarks"])[0], None
        retk, idx, todel = choice_win(True)(lambda:
        ("Bookmarks", bmarkslist, idx, {ord("B")})
        )()
        if todel is not None:
            del STATE["States"][ebookpath]["bmarks"][todel]
        else:
            return retk, idx


def truncate(teks, subte, maxlen, startsub=0):
    if startsub > maxlen:
        raise ValueError("Var startsub cannot be bigger than maxlen.")
    elif len(teks) <= maxlen:
        return teks
    else:
        lensu = len(subte)
        beg = teks[:startsub]
        mid = subte if lensu <= maxlen - startsub else subte[:maxlen-startsub]
        end = teks[startsub+lensu-maxlen:] if lensu < maxlen - startsub else ""
        return beg+mid+end

def safe_curs_set(state):
    try:
        curses.curs_set(state)
    except:
        return

def input_prompt(prompt):
    # prevent pad hole when prompting for input while
    # other window is active
    # pad.refresh(y, 0, 0, x, rows-2, x+width)
    rows, cols = SCREEN.getmaxyx()
    stat = curses.newwin(1, cols, rows-1, 0)
    if COLORSUPPORT:
        stat.bkgd(SCREEN.getbkgd())
    stat.keypad(True)
    curses.echo(1)
    safe_curs_set(1)

    init_text = ""

    stat.addstr(0, 0, prompt, curses.A_REVERSE)
    stat.addstr(0, len(prompt), init_text)
    stat.refresh()

    try:
        while True:
            ipt = stat.getch()
            if ipt == 27:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                safe_curs_set(0)
                return
            elif ipt == 10:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                safe_curs_set(0)
                return init_text
            elif ipt in {8, curses.KEY_BACKSPACE}:
                init_text = init_text[:-1]
            elif ipt == curses.KEY_RESIZE:
                stat.clear()
                stat.refresh()
                curses.echo(0)
                safe_curs_set(0)
                return curses.KEY_RESIZE
            # elif len(init_text) <= maxlen:
            else:
                init_text += chr(ipt)

            stat.clear()
            stat.addstr(0, 0, prompt, curses.A_REVERSE)
            stat.addstr(
                0, len(prompt),
                init_text if len(prompt+init_text) < cols else "..."+init_text[len(prompt)-cols+4:]
                )
            stat.refresh()
    except KeyboardInterrupt:
        stat.clear()
        stat.refresh()
        curses.echo(0)
        safe_curs_set(0)
        return


def det_ebook_cls(file):
    filext = os.path.splitext(file)[1]
    if filext == ".epub":
        return Epub(file)
    elif filext == ".fb2":
        return FictionBook(file)
    elif MOBISUPPORT and filext == ".mobi":
        return Mobi(file)
    elif MOBISUPPORT and filext == ".azw3":
        return Azw3(file)
    elif not MOBISUPPORT and filext in {".mobi", ".azw3"}:
        sys.exit("""ERROR: Format not supported. (Supported: epub, fb2).
To get mobi and azw3 support, install mobi module from pip.
   $ pip install mobi""")
    else:
        sys.exit("ERROR: Format not supported. (Supported: epub, fb2)")


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


def find_dict_client():
    global DICT
    if shutil.which(CFG["DictionaryClient"].split()[0]) is not None:
        DICT = CFG["DictionaryClient"]
    else:
        DICT_LIST = [
            "sdcv",
            "dict"
        ]
        for i in DICT_LIST:
            if shutil.which(i) is not None:
                DICT = i
                break
        if DICT in {"sdcv"}:
            DICT += " -n"


def find_media_viewer():
    global VWR
    if shutil.which(CFG["DefaultViewer"].split()[0]) is not None:
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


def open_media(scr, name, bstr):
    sfx = os.path.splitext(name)[1]
    fd, path = tempfile.mkstemp(suffix=sfx)
    try:
        with os.fdopen(fd, "wb") as tmp:
            # tmp.write(epub.file.read(src))
            tmp.write(bstr)
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


@text_win
def define_word(word):
    rows, cols = SCREEN.getmaxyx()
    hi, wi = 5, 16
    Y, X = (rows - hi)//2, (cols - wi)//2

    p = subprocess.Popen(
        "{} {}".format(DICT, word),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True
    )

    dictwin = curses.newwin(hi, wi, Y, X)
    dictwin.box()
    dictwin.addstr((hi-1)//2, (wi-10)//2, "Loading...")
    dictwin.refresh()

    out, err = p.communicate()

    dictwin.clear()
    dictwin.refresh()

    if err == b"":
        return "Definition: " + word.upper(), out.decode(), K["DefineWord"]
    else:
        return "Error: " + DICT, err.decode(), K["DefineWord"]


def searching(pad, src, width, y, ch, tot):
    global SEARCHPATTERN
    rows, cols = SCREEN.getmaxyx()
    x = (cols - width) // 2
    if SEARCHPATTERN is None:
        candtext = input_prompt(" Regex:")
        if candtext is None:
            return y
        elif isinstance(candtext, str):
            SEARCHPATTERN = "/" + candtext
        elif candtext == curses.KEY_RESIZE:
            return candtext

    if SEARCHPATTERN in {"?", "/"}:
        SEARCHPATTERN = None
        return y

    found = []
    try:
        pattern = re.compile(SEARCHPATTERN[1:], re.IGNORECASE)
    except re.error as reerrmsg:
        SEARCHPATTERN = None
        tmpk = errmsg("!Regex Error", str(reerrmsg), set())
        return tmpk

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
                    " Finished searching: " + SEARCHPATTERN[1:cols-22] + " ",
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

        # TODO
        if y+rows-1 > pad.chunks[pad.find_chunkidx(y)]:
            y = pad.chunks[pad.find_chunkidx(y)] + 1

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


def count_pct_async(ebook, allprev, sumlet):
    perch = []
    for n, i in enumerate(ebook.contents):
        content = ebook.get_raw_text(i)
        parser = HTMLtoLines()
        # try:
        parser.feed(content)
        parser.close()
        # except:
        #     pass
        src_lines = parser.get_lines()
        allprev[n] = sum(perch)
        perch.append(sum([len(re.sub("\s", "", j)) for j in src_lines]))
    sumlet.value = sum(perch)


def count_pct(ebook):
    perch = []
    allprev = []
    for i in ebook.contents:
        content = ebook.get_raw_text(i)
        parser = HTMLtoLines()
        # try:
        parser.feed(content)
        parser.close()
        # except:
        #     pass
        src_lines = parser.get_lines()
        allprev.append(sum(perch))
        perch.append(sum([len(re.sub("\s", "", j)) for j in src_lines]))
    sumlet = sum(perch)
    return allprev, sumlet


def count_max_reading_pg(ebook):
    global ALLPREVLETTERS, SUMALLLETTERS, PROC_COUNTLETTERS, MULTIPROC

    if MULTIPROC:
        try:
            ALLPREVLETTERS = multiprocessing.Array("i", len(ebook.contents))
            SUMALLLETTERS = multiprocessing.Value("i", 0)
            PROC_COUNTLETTERS = multiprocessing.Process(
                    target=count_pct_async, args=(
                        ebook,
                        ALLPREVLETTERS,
                        SUMALLLETTERS
                        )
                    )
            # forking PROC_COUNTLETTERS will raise
            # zlib.error: Error -3 while decompressing data: invalid distance too far back
            PROC_COUNTLETTERS.start()
        except:
            MULTIPROC = False
    if not MULTIPROC:
        ALLPREVLETTERS, SUMALLLETTERS = count_pct(ebook)


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
    content = ebook.get_raw_text(chpath)

    parser = HTMLtoLines(set(toc_sect))
    # parser = HTMLtoLines()
    # try:
    parser.feed(content)
    parser.close()
    # except:
    #     pass

    src_lines, imgs, toc_secid = parser.get_lines(width)
    totlines = len(src_lines) + 1  # 1 extra line for suffix

    if y < 0 and totlines <= rows:
        y = 0
    elif pctg is not None:
        y = round(pctg*totlines)
    else:
        y = y % totlines

    pad = Board(totlines, width)
    pad.feed(src_lines)

    # this make curses.A_REVERSE not working
    # put before paint_text
    if COLORSUPPORT:
        pad.bkgd(SCREEN.getbkgd())

    pad.paint_text(0)

    LOCALPCTG = []
    for i in src_lines:
        LOCALPCTG.append(len(re.sub("\s", "", i)))

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
    svline = "dontsave"
    try:
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
                    if count > 1:
                        svline = y - 1
                    if y >= count:
                        y -= count
                    elif y == 0 and index != 0:
                        return -1, width, -rows, None, ""
                    else:
                        y = 0
                elif k in K["PageUp"]:
                    if y == 0 and index != 0:
                        return -1, width, -rows, None, ""
                    else:
                        y = pgup(y, rows, LINEPRSRV, count)
                elif k in K["ScrollDown"]:
                    if count > 1:
                        svline = y + rows - 1
                    if y + count <= totlines - rows:
                        y += count
                    elif y == totlines - rows and index != len(contents)-1:
                        return 1, width, 0, None, ""
                    else:
                        y = totlines - rows
                elif k in K["PageDown"]:
                    if totlines - y - LINEPRSRV > rows:
                        if y+rows > pad.chunks[pad.find_chunkidx(y)]:
                            y = pad.chunks[pad.find_chunkidx(y)] + 1
                        else:
                            y += rows - LINEPRSRV
                        # SCREEN.clear()
                        # SCREEN.refresh()
                    elif index != len(contents)-1:
                        return 1, width, 0, None, ""
                elif k in K["HalfScreenUp"]|K["HalfScreenDown"]:
                    countstring = str(rows//2)
                    k = list(K["ScrollUp" if k in K["HalfScreenUp"] else "ScrollDown"])[0]
                    continue
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
                    except (KeyError, IndexError):
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
                elif k in K["TableOfContents"]:
                    if ebook.toc_entries == [[], [], []]:
                        k = errmsg(
                            "Table of Contents",
                            "N/A: TableOfContents is unavailable for this book.",
                            K["TableOfContents"]
                        )
                        continue
                    ntoc = find_curr_toc_id(toc_idx, toc_sect, toc_secid, index, y)
                    rettock, fllwd, _ = toc(toc_name, ntoc)
                    if rettock is not None:  # and rettock in WINKEYS:
                        k = rettock
                        continue
                    elif fllwd is not None:
                        if index == toc_idx[fllwd]:
                            try:
                                y = toc_secid[toc_sect[fllwd]]
                            except KeyError:
                                y = 0
                        else:
                            return toc_idx[fllwd] - index, width, 0, None, toc_sect[fllwd]
                elif k in K["Metadata"]:
                    k = meta(ebook)
                    if k in WINKEYS:
                        continue
                elif k in K["Help"]:
                    k = help()
                    if k in WINKEYS:
                        continue
                elif k in K["Enlarge"] and (width + count) < cols - 4:
                    width += count
                    return 0, width, 0, y/totlines, ""
                elif k in K["Shrink"] and width >= 22:
                    width -= count
                    return 0, width, 0, y/totlines, ""
                elif k in K["SetWidth"]:
                    if countstring == "":
                        # if called without a count, toggle between 80 cols and full width
                        if width != 80 and cols - 4 >= 80:
                            return 0, 80, 0, y/totlines, ""
                        else:
                            return 0, cols - 4, 0, y/totlines, ""
                    else:
                        width = count
                    if width < 20:
                        width = 20
                    elif width >= cols - 4:
                        width = cols - 4
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
                    if fs in WINKEYS or fs is None:
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
                            safe_curs_set(1)
                            p = pad.getch()
                            if p in K["ScrollDown"]:
                                i += 1
                            elif p in K["ScrollUp"]:
                                i -= 1
                            i = i % len(gambar)

                        safe_curs_set(0)
                        if p in K["Follow"]:
                            impath = imgs[int(gambar[i])]

                    if impath != "":
                        if ebook.__class__.__name__ in {"Epub", "Azw3"}:
                            impath = dots_path(chpath, impath)
                        imgnm, imgbstr = ebook.get_img_bytestr(impath)
                        k = open_media(pad, imgnm, imgbstr)
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
                elif k in K["AddBookmark"]:
                    defbmname_suffix = 1
                    defbmname = "Bookmark " + str(defbmname_suffix)
                    occupiedbmnames = [i[0] for i in STATE["States"][ebook.path]["bmarks"]]
                    while defbmname in occupiedbmnames:
                        defbmname_suffix += 1
                        defbmname = "Bookmark " + str(defbmname_suffix)
                    bmname = input_prompt(" Add bookmark ({}):".format(defbmname))
                    if bmname is not None:
                        if bmname.strip() == "":
                            bmname = defbmname
                        STATE["States"][ebook.path]["bmarks"].append(
                            [bmname, index, y, y/totlines]
                        )
                elif k in K["ShowBookmarks"]:
                    if STATE["States"][ebook.path]["bmarks"] == []:
                        k = text_win(lambda: (
                            "Bookmarks",
                            "N/A: Bookmarks are not found in this book.",
                            {ord("B")}
                        ))()
                        continue
                    else:
                        retk, idxchoice = bookmarks(ebook.path)
                        if retk is not None:
                            k = retk
                            continue
                        elif idxchoice is not None:
                            bmtojump = STATE["States"][ebook.path]["bmarks"][idxchoice]
                            return bmtojump[1]-index, width, bmtojump[2], bmtojump[3], ""
                elif k in K["DefineWord"] and DICT is not None:
                    word = input_prompt(" Define:")
                    if word == curses.KEY_RESIZE:
                        k = word
                        continue
                    elif word is not None:
                        defin = define_word(word)
                        if defin in WINKEYS:
                            k = defin
                            continue
                elif k in K["MarkPosition"]:
                    jumnum = pad.getch()
                    if jumnum in range(49, 58):
                        JUMPLIST[chr(jumnum)] = [index, width, y, y/totlines]
                    else:
                        k = jumnum
                        continue
                elif k in K["JumpToPosition"]:
                    jumnum = pad.getch()
                    if jumnum in range(49, 58) and chr(jumnum) in JUMPLIST.keys():
                        tojumpidxdiff = JUMPLIST[chr(jumnum)][0]-index
                        tojumpy = JUMPLIST[chr(jumnum)][2]
                        tojumpctg = None if JUMPLIST[chr(jumnum)][1] == width else JUMPLIST[chr(jumnum)][3]
                        return tojumpidxdiff, width, tojumpy, tojumpctg, ""
                    else:
                        k = jumnum
                        continue
                elif k in K["ShowHideProgress"]:
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
                    if cols < 22 or rows < 12:
                        sys.exit("ERROR: Screen was too small (min 22cols x 12rows).")
                    if cols <= width + 4:
                        return 0, cols - 4, 0, y/totlines, ""
                    else:
                        return 0, width, y, None, ""
                countstring = ""

            if svline != "dontsave":
                pad.chgat(svline, 0, width, curses.A_UNDERLINE)

            try:
                SCREEN.clear()
                SCREEN.addstr(0, 0, countstring)
                LOCALSUMALLL = SUMALLLETTERS.value if MULTIPROC else SUMALLLETTERS
                if SHOWPROGRESS and (cols-width-2)//2 > 3 and LOCALSUMALLL != 0:
                    PROGRESS = (ALLPREVLETTERS[index] + sum(LOCALPCTG[:y+rows-1])) / LOCALSUMALLL
                    PROGRESSTR = "{}%".format(int(PROGRESS*100))
                    SCREEN.addstr(0, cols-len(PROGRESSTR), PROGRESSTR)
                SCREEN.refresh()
                if totlines - y < rows:
                    pad.refresh(y, 0, 0, x, totlines-y, x+width)
                else:
                    pad.refresh(y, 0, 0, x, rows-1, x+width)
            except curses.error:
                pass
            k = pad.getch()

            if svline != "dontsave":
                pad.chgat(svline, 0, width, curses.A_NORMAL)
                svline = "dontsave"
    except KeyboardInterrupt:
        savestate(ebook.path, index, width, y, y/totlines)
        sys.exit()


def preread(stdscr, file):
    global COLORSUPPORT, SHOWPROGRESS, SCREEN

    try:
        curses.use_default_colors()
        curses.init_pair(1, -1, -1)
        curses.init_pair(2, CFG["DarkColorFG"], CFG["DarkColorBG"])
        curses.init_pair(3, CFG["LightColorFG"], CFG["LightColorBG"])
        COLORSUPPORT = True
    except:
        COLORSUPPORT  = False

    SCREEN = stdscr

    SCREEN.keypad(True)
    safe_curs_set(0)
    SCREEN.clear()
    rows, cols = SCREEN.getmaxyx()
    show_loader(SCREEN)

    ebook = det_ebook_cls(file)

    try:
        if ebook.path in STATE["States"]:
            idx = STATE["States"][ebook.path]["index"]
            width = STATE["States"][ebook.path]["width"]
            y = STATE["States"][ebook.path]["pos"]
        else:
            STATE["States"][ebook.path] = {}
            STATE["States"][ebook.path]["bmarks"] = []
            idx = 0
            y = 0
            width = 80
        pctg = None

        if cols <= width + 4:
            width = cols - 4
            pctg = STATE["States"][ebook.path].get("pctg", None)

        try:
            ebook.initialize()
        except Exception as e:
            sys.exit("ERROR: Badly-structured ebook.\n"+str(e))
        find_media_viewer()
        find_dict_client()
        parse_keys()
        SHOWPROGRESS = CFG["ShowProgressIndicator"]
        count_max_reading_pg(ebook)

        sec = ""
        while True:
            incr, width, y, pctg, sec = reader(
                ebook, idx, width, y, pctg, sec
            )
            idx += incr
            show_loader(SCREEN)
    finally:
        ebook.cleanup()


def main():
    termc, termr = shutil.get_terminal_size()

    args = []
    if sys.argv[1:] != []:
        args += sys.argv[1:]

    if len({"-h", "--help"} & set(args)) != 0:
        print(__doc__.rstrip())
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
                xitmsg = "ERROR: No matching file found in history."

        if xitmsg != 0 or "-r" in args:
            print("Reading history:")
            dig = len(str(len(STATE["States"].keys())+1))
            tcols = termc - dig - 2
            for n, i in enumerate(STATE["States"].keys()):
                p = i.replace(os.getenv("HOME"), "~")
                print("{}{} {}".format(
                    str(n+1).rjust(dig),
                    "*" if i == STATE["LastRead"] else " ",
                    truncate(p, "...", tcols, 7)
                    ))
            sys.exit(xitmsg)

    if dump:
        ebook = det_ebook_cls(file)
        try:
            try:
                ebook.initialize()
            except Exception as e:
                sys.exit("ERROR: Badly-structured ebook.\n"+str(e))
            for i in ebook.contents:
                content = ebook.get_raw_text(i)
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
        finally:
            ebook.cleanup()
        sys.exit()

    else:
        if termc < 22 or termr < 12:
            sys.exit("ERROR: Screen was too small (min 22cols x 12rows).")
        curses.wrapper(preread, file)


if __name__ == "__main__":
    main()
