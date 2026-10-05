import os

import bevo

KNOWN_PARAM = os.environ.get("KNOWN_PARAM", "x")
# BUG (deliberate, for the validator fixture suite): SECRET_PARAM is not
# declared in recipe.json's params.properties.
SECRET_PARAM = os.environ.get("SECRET_PARAM", "")


def main():
    for tick in bevo.ticks():
        bevo.log(f"{KNOWN_PARAM} {SECRET_PARAM}")


if __name__ == "__main__":
    main()
