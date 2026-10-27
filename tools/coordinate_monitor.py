"""Standalone executable entry. Importing this module does not open a window."""
from pathlib import Path
import sys


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from utils.ui.native_coordinate_monitor import main
    from utils.flows.coordinate_player import drive_points
    from utils.flows.native_guidance_follow import drive_guidance
    from utils.flows.cooking import run_cooking
    main(playback_runner=drive_points, cooking_runner=run_cooking, guidance_runner=drive_guidance)
