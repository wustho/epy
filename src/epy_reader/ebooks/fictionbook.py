import base64
import os
import xml.etree.ElementTree as ET
from typing import List, Union, Tuple

from epy_reader.ebooks import Ebook
from epy_reader.models import BookMetadata, TocEntry


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
