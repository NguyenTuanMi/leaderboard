"""Generate CARLA leaderboard routes from a natural-language scenario description.

Pipeline: inference layer (ask the model for route_index choices) -> validation
layer (check against the real CARLA route; errors are fed back to the model) ->
helper (write the XML). Run from anywhere:

    python3 scenario_generator/inference_server.py --template-route 0
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

# Allow `python3 scenario_generator/inference_server.py` (sys.path[0] would be this folder).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenario_generator.config.inference_params import INTERACTIVE_HELP, parse_args
from scenario_generator.scenario_generation.layers.inference_layer import request_routes
from scenario_generator.scenario_generation.layers.validation_layer import validate_route
from scenario_generator.scenario_generation.scenario_helper import (
    DenseRoute,
    GenerationError,
    build_carla_route,
    build_prompt,
    confirm_and_write,
    copy_template,
)


def perform_generation(
    args: argparse.Namespace, description: str, town: str, dense_route: DenseRoute
) -> list[tuple[list[dict[str, str]], list[dict[str, Any]]]]:
    """Ask the model and validate, feeding validation errors back until max_requests."""
    base_prompt = build_prompt(args, town, dense_route, description)
    feedback = ""
    for attempt in range(1, args.max_requests + 1):
        try:
            routes = request_routes(args, base_prompt + feedback)
            return [
                validate_route(route, town, dense_route, args.max_scenarios, args.min_spacing)
                for route in routes
            ]
        except GenerationError as error:
            if attempt == args.max_requests:
                raise GenerationError(
                    f"Model output failed validation after {args.max_requests} requests: {error}"
                ) from error
            feedback = (
                "\n\nVALIDATION RETRY\n"
                f"Attempt {attempt} failed with: {error}\n"
                "Return the complete corrected JSON object. Correct every "
                "listed scenario (use valid route_index values); do not merely explain the fix.\n"
            )
            print(f"Attempt {attempt} rejected: {error}; retrying.")
    raise GenerationError("No valid route was generated")

def run_session(args, root, template, output, town, dense_route, interactive) -> None:
    if not interactive:
        generated = perform_generation(args, args.description, town, dense_route)
        confirm_and_write(args, root, template, output, generated)
        return
    try:
        import readline  # noqa: F401  (line editing and history for input())
    except ImportError:
        pass
    print(INTERACTIVE_HELP.format(count=args.count))
    while True:
        try:
            line = input("scenario> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit", "quit", "exit"):
            return
        if line == "/help":
            print(INTERACTIVE_HELP.format(count=args.count))
            continue
        if line.startswith("/count"):
            value = line.split()[1:]
            if len(value) == 1 and value[0].isdigit() and int(value[0]) >= 1:
                args.count = int(value[0])
                print(f"count = {args.count}")
            else:
                print("Usage: /count N (N >= 1)")
            continue
        try:
            generated = perform_generation(args, line, town, dense_route)
            confirm_and_write(args, root, template, output, generated)
        except GenerationError as error:
            print(f"ERROR: {error}")  # keep the session alive for the next prompt
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")

def main() -> int:
    args = parse_args()
    if args.count < 1 or args.max_scenarios < 0 or args.max_requests < 1:
        raise GenerationError(
            "--count and --max-requests must be positive; --max-scenarios cannot be negative"
        )
    interactive = not args.description
    output = Path(args.output)
    try:
        tree = ET.parse(output)
        root = tree.getroot()
        template = copy_template(root, args.template_route)
        town = template.attrib.get("town")
        waypoints = template.find("waypoints")
        if not town or waypoints is None:
            raise GenerationError("Template route must contain a town and waypoints")
        # Built once: loading the town and routing is the slow part.
        dense_route = build_carla_route(
            list(waypoints), town, args.carla_host, args.carla_port, args.carla_timeout
        )
        run_session(args, root, template, output, town, dense_route, interactive)
        return 0
    except (ET.ParseError, OSError) as error:
        raise GenerationError(f"Could not read or write {output}: {error}") from error


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GenerationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
