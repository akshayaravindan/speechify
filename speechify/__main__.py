"""python -m speechify [--sim all|glasses|speech] [--neon-ip IP] [--port 8000]"""

import argparse
import os
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="speechify", description="Wearable speech coach")
    parser.add_argument("--sim", choices=["all", "glasses", "speech"],
                        help="simulate hardware: all, just the glasses (use the real mic), or just the speech")
    parser.add_argument("--neon-ip", default=os.environ.get("NEON_IP"),
                        help="Neon Companion phone IP, if auto-discovery doesn't find it")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    from . import server

    server.SIM = args.sim
    server.NEON_IP = args.neon_ip
    url = f"http://localhost:{args.port}"
    print(f"Speechify dashboard: {url}")
    threading.Timer(1.5, webbrowser.open, [url]).start()
    uvicorn.run(server.app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
