"""Standalone executable entry. Importing this module does not open a window."""
from pathlib import Path
import sys


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if len(sys.argv) == 3 and sys.argv[1] == '--check-navigation-process-output':
        import json
        from utils.drivers.navigation_process import probe_navigation_process
        from utils.core.scene_read import issue_summary
        resources = Path(sys._MEIPASS)/'monitor' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]/'webui/coordinate-monitor'
        try:
            result = probe_navigation_process(resources/'profile.json')
        except (OSError,ValueError) as exc:
            result = {'state':'error','read_only':True,'input_sent':False,'issue':issue_summary(exc)}
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        raise SystemExit(0 if result.get('state')=='ready' else 1)
    from utils.ui.native_coordinate_monitor import main
    from utils.flows.coordinate_player import drive_points
    from utils.flows.native_guidance_follow import drive_guidance
    from utils.flows.cooking import run_cooking
    main(playback_runner=drive_points, cooking_runner=run_cooking, guidance_runner=drive_guidance)
