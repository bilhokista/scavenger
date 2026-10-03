import argparse

from scavenger import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scavenger")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv=None) -> None:
    build_parser().parse_args(argv)


if __name__ == "__main__":
    main()
