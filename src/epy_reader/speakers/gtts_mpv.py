#!/usr/bin/env python3


import shutil
import subprocess

from epy_reader.speakers.base import SpeakerBaseModel


class SpeakerGttsMPV(SpeakerBaseModel):
    cmd = "gtts-mpv"
    available = bool(shutil.which("gtts-cli") and shutil.which("mpv"))

    def speak(self, text: str) -> None:
        self._gtts_process = subprocess.Popen(
            ["gtts-cli", "-", *self.args],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        self._mpv_process = subprocess.Popen(
            ["mpv", "-"],
            stdin=self._gtts_process.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert self._gtts_process.stdin
        self._gtts_process.stdin.write(text)
        self._gtts_process.stdin.close()

    def is_done(self) -> bool:
        return self._mpv_process.poll() is not None

    def stop(self) -> None:
        self._gtts_process.terminate()
        self._mpv_process.terminate()

    def cleanup(self) -> None:
        pass
