import sys
from pathlib import Path

import qrcode
import qrcode.image.svg


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m kid_terminal.pairing OUTPUT.svg")
    value = sys.stdin.read(4096).strip()
    if not value.startswith("aikid://provision?"):
        raise SystemExit("invalid pairing payload")
    image = qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage)
    if sys.argv[1] == "-":
        image.save(sys.stdout.buffer)
    else:
        image.save(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
