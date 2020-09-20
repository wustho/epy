import sys
from setuptools import setup
# from epy import __version__, __author__, __url__, __license__

setup(
    name = "epy",
    version = "2020.9.20",
    description = "Terminal/CLI Epub Reader (Fork of https://github.com/wustho/epr with Reading Pctg)",
    url = "https://github.com/wustho/epy",
    author = "Benawi Adha",
    license = "GPL-3.0",
    keywords = ["EPUB", "EPUB3", "CLI", "Terminal", "Reader"],
    install_requires = ["mobi"] + (["windows-curses"] if sys.platform == "win32" else []),
    python_requires = "~=3.0",
    py_modules = ["epy"],
    entry_points = { "console_scripts": ["epy = epy:main"] }
)
