"""Read-only checks of packaged vision libraries and one essential template."""
from pathlib import Path

import cv2


def check_runtime(template_folder):
    folder = Path(template_folder)
    for suffix in ('.webp', '.png', '.jpg'):
        path = folder / ('finish_fighting' + suffix)
        if path.is_file():
            frame = cv2.imread(str(path))
            if frame is not None and frame.size:
                return cv2.__version__
    raise RuntimeError('Missing or unreadable main-interface template')
