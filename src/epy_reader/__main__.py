import multiprocessing
import sys
import curses

def main():
    # On Windows, calling this method is necessary
    # On Linux/OSX, this method does nothing
    multiprocessing.freeze_support()
    filepath, dump_only = find_file()
    if dump_only:
        sys.exit(dump_ebook_content(filepath))

    while True:
        filepath = curses.wrapper(preread, filepath)
