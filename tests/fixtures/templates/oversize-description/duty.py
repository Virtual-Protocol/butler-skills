import bevo


def main():
    for tick in bevo.ticks():
        bevo.log("fixture only")


if __name__ == "__main__":
    main()
