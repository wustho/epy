import sys
from setuptools import setup

with open("README.md", "r") as fh:
    long_description = fh.read()

requirements = ["mobi"]
if sys.platform == "win32":
    requirements.append("windows-curses")

setup(
    name="epy-reader",
    version="2021.4.1",
    description="Terminal/CLI Ebook (epub, fb2, mobi, azw3) Reader",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/wustho/epy",
    author="Benawi Adha",
    author_email="benawiadha@gmail.com",
    license="GPL-3.0",
    keywords=["epub", "epub3", "fb2", "mobi", "azw3", "CLI", "Terminal", "Reader"],
    install_requires=requirements,
    python_requires="~=3.0",
    py_modules=["epy"],
    entry_points={ "console_scripts": ["epy = epy:main"] },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ]
)
