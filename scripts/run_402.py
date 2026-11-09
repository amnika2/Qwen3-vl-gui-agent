import argparse
import shlex
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Run GUIQA on allocated server402 GPUs over SSH")
    parser.add_argument("--gpu", choices=["0", "1"], default="0")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    command = ["env", "CUDA_VISIBLE_DEVICES=" + args.gpu,
               f"LD_LIBRARY_PATH={root}/vendor/runtime/root/usr/lib/x86_64-linux-gnu",
               str(root / ".model-venv/bin/python"), "-m", "guiqa", "--root", str(root), *args.command]
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "402", shlex.join(command)]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
