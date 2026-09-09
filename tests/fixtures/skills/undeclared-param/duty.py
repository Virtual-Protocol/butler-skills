import os

import bevo

KNOWN_PARAM = os.environ.get("KNOWN_PARAM", "x")
# BUG (deliberate, for the validator fixture suite): SECRET_PARAM is not declared in params.
SECRET_PARAM = os.environ.get("SECRET_PARAM", "")


def main():
    for ev in bevo.events():
        bevo.log(f"{KNOWN_PARAM} {SECRET_PARAM}")


if __name__ == "__main__":
    main()
