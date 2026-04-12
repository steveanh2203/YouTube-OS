"""Compatibility entrypoint for the Tauri/FastAPI desktop stack."""

from autocapcut.api.server import main as run_api


def main() -> None:
    run_api()


if __name__ == "__main__":
    main()
