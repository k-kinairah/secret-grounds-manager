import faulthandler

with open(
    r"C:\Users\Kin\secret-grounds-system\logs\production-webview-live.threads.log",
    "a",
    encoding="utf-8",
) as output:
    faulthandler.dump_traceback(file=output, all_threads=True)
