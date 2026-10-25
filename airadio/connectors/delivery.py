"""Getting finished audio into the automation system's carts (Part 4).

`FolderDelivery` is the stand-in for AudioVAULT's import: a file named after the cart
(e.g. AI2203.wav) appears in a folder. Whether the real station uses a watch folder or an
API is parked until their engineer confirms — write another class with `deliver()` then.

Rule: write under a temporary name, then rename, so the importer never sees a half-written file.
"""
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("airadio.delivery")


class DeliveryError(RuntimeError):
    pass


class FolderDelivery:
    name = "folder"

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def cart_path(self, cart: str) -> Path:
        return self.folder / f"{cart}.wav"

    def deliver(self, cart: str, wav_file: Path) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        final = self.cart_path(cart)
        partial = self.folder / f".{cart}.partial"
        shutil.copyfile(wav_file, partial)
        # On Windows the rename fails while another program has the target open; retry briefly.
        for attempt in range(10):
            try:
                os.replace(partial, final)
                return final
            except PermissionError:
                time.sleep(0.3 * (attempt + 1))
        try:
            partial.unlink()
        except OSError:
            pass
        raise DeliveryError(f"could not place {final} (file locked)")


def get_delivery(folder: Path) -> FolderDelivery:
    return FolderDelivery(folder)
