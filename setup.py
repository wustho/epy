import sys
from setuptools import setup
from epy import __version__, __author__, __email__, __url__, __license__

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="epy-reader",
    version=__version__,
    description="Terminal/CLI Ebook (epub, fb2, mobi, azw3) Reader",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url=__url__,
    author=__author__,
    author_email=__email__,
    license=__license__,
    keywords=["epub", "epub3", "fb2", "mobi", "azw3", "CLI", "Terminal", "Reader"],
    install_requires=["mobi"] + (["windows-curses"] if sys.platform == "win32" else []),
    python_requires="~=3.0",
    py_modules=["epy"],
    entry_points={ "console_scripts": ["epy = epy:main"] },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ]
)
