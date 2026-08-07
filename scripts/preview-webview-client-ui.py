import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.webview_ui import run_webview_customer_overlay_preview, run_webview_customer_ui_preview


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preview the local Secret Grounds WebView2 customer UI.")
    parser.add_argument("--view", choices=("lock", "open", "package", "member", "pill"), default="lock")
    parser.add_argument("--right-offset", type=int, default=16)
    args = parser.parse_args()
    if args.view == "lock":
        run_webview_customer_ui_preview()
    else:
        run_webview_customer_overlay_preview(args.view, right_offset=max(16, args.right_offset))
