"""콘솔 없는 게임 실행 진입점. 치명적 오류만 파일에 남긴다."""

import ctypes
from datetime import datetime
import os
from pathlib import Path
import traceback


def main():
    try:
        from .__main__ import main as run_game

        run_game([])
    except Exception:
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RevenantLost" / "logs"
        message = traceback.format_exc()
        try:
            root.mkdir(parents=True, exist_ok=True)
            path = root / "startup.log"
            with path.open("a", encoding="utf-8") as output:
                output.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{message}")
            detail = f"게임 실행 중 오류가 발생했습니다.\n로그: {path}"
        except OSError:
            detail = f"게임 실행 중 오류가 발생했습니다.\n\n{message[-1200:]}"
        ctypes.windll.user32.MessageBoxW(None, detail, "REVENANT : L.O.S.T", 0x10)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
