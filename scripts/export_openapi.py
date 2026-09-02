from __future__ import annotations

import argparse
import json
from pathlib import Path

from echo.api.app import create_api_app
from echo.application import EchoApplication
from echo.core.config import EchoConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export ECHO's OpenAPI document")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated/openapi.json"),
        help="Destination for the generated OpenAPI JSON document",
    )
    return parser.parse_args()


def main() -> None:
    output = parse_args().output
    output.parent.mkdir(parents=True, exist_ok=True)
    application = EchoApplication(EchoConfig())
    schema = create_api_app(application).openapi()
    output.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
