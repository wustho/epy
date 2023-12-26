import shutil
import subprocess

from epy_reader.speakers.base import SpeakerBaseModel


class SpeakerSpeechd(SpeakerBaseModel):
    cmd = "spd-say"
    available = bool(shutil.which("spd-say"))

    def speak(self, text: str) -> None:
        self.process = subprocess.Popen(
            [self.cmd, *self.args, "--application-name=epy", "--pipe-mode", "-w" ],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        assert self.process.stdin
        self.process.stdin.write(text)
        self.process.stdin.close()

    def is_done(self) -> bool:
        return self.process.poll() is not None

    def stop(self) -> None:
        self.process.terminate()
        # self.process.kill()

    def cleanup(self) -> None:
        pass
