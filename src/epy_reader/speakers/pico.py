import shutil
import subprocess
import sys
import tempfile

from epy_reader.speakers.base import SpeakerBaseModel


class SpeakerPico(SpeakerBaseModel):
    cmd = "pico2wave"
    available = all([shutil.which(dep) for dep in ["pico2wave", "play"]])

    def speak(self, text: str) -> None:
        _, self.tmp_path = tempfile.mkstemp(suffix=".wav")

        try:
            subprocess.run(
                [self.cmd, *self.args, "-w", self.tmp_path, text],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            if "invalid pointer" not in e.output:
                sys.exit(e.output)

        self.process = subprocess.Popen(
            ["play", self.tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def is_done(self) -> bool:
        return self.process.poll() is not None

    def stop(self) -> None:
        self.process.terminate()
        # self.process.kill()

    def cleanup(self) -> None:
        os.remove(self.tmp_path)
