#!/usr/bin/env python3
"""Generate CARLA leaderboard routes from a natural-language scenario description.

The model supplies scenario choices and weather, while route geometry is copied
from an existing route. This keeps generated coordinates valid for the selected
CARLA town and makes the tool usable with local or remote OpenAI-compatible APIs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = "data/routes_devtest.xml"
DEFAULT_ENDPOINT = "http://localhost:8000/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
MAX_SCENARIOS = 10

SCENARIO_PARAMETERS: dict[str, tuple[str, ...]] = {
    "SignalizedJunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "SignalizedJunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "OppositeVehicleRunningRedLight": ("direction",),
    "NonSignalizedJunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "NonSignalizedJunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "OppositeVehicleTakingPriority": ("direction",),
    "DynamicObjectCrossing": ("distance", "direction", "blocker_model", "crossing_angle"),
    "ParkingCrossingPedestrian": ("distance", "direction", "crossing_angle"),
    "PedestrianCrossing": (),
    "VehicleTurningRoute": (),
    "VehicleTurningRoutePedestrian": (),
    "BlockedIntersection": (),
    "EnterActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "EnterActorFlowV2": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InterurbanActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InterurbanAdvancedActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "HighwayExit": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "MergerIntoSlowTraffic": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "MergerIntoSlowTrafficV2": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "CrossingBicycleFlow": ("start_actor_flow", "flow_speed", "source_dist_interval"),
    "ConstructionObstacle": ("distance", "direction", "speed"),
    "ConstructionObstacleTwoWays": ("distance", "frequency"),
    "Accident": ("distance", "direction", "speed"),
    "AccidentTwoWays": ("distance", "frequency"),
    "ParkedObstacle": ("distance", "direction", "speed"),
    "ParkedObstacleTwoWays": ("distance", "frequency"),
    "VehicleOpensDoorTwoWays": ("distance", "frequency"),
    "HazardAtSideLane": ("distance", "speed", "bicycle_drive_distance", "bicycle_speed"),
    "HazardAtSideLaneTwoWays": ("distance", "frequency", "bicycle_drive_distance", "bicycle_speed"),
    "InvadingTurn": ("distance", "offset"),
    "HighwayCutIn": ("other_actor_location",),
    "ParkingCutIn": ("direction",),
    "StaticCutIn": ("distance", "direction"),
    "ControlLoss": (),
    "HardBreakRoute": (),
    "ParkingExit": ("direction", "front_vehicle_distance", "behind_vehicle_distance"),
    "YieldToEmergencyVehicle": ("distance",),
    "BackgroundActivityParametrizer": (
        "num_front_vehicles", "num_back_vehicles", "road_spawn_dist",
        "opposite_source_dist", "opposite_max_actors", "opposite_spawn_dist",
        "opposite_active", "junction_source_dist", "junction_max_actors",
        "junction_spawn_dist", "junction_source_perc",
    ),
    "PriorityAtJunction": (),
}

WEATHER_ATTRIBUTES = {
    "cloudiness", "precipitation", "precipitation_deposits", "wetness",
    "wind_intensity", "sun_azimuth_angle", "sun_altitude_angle", "fog_density",
    "fog_distance", "wetness", "scatter_intensity", "mie_scattering_scale",
    "rayleigh_scattering_scale", "dust_storm", "fog_falloff",
}

SYSTEM_PROMPT = """You generate CARLA leaderboard route XML data.
The caller supplies an existing route as a geometry template. Never invent
waypoint coordinates. Return only one JSON object with this shape:
{
  "routes": [
    {
      "town": "the template town",
      "weather": [
        {"route_percentage": 0, "cloudiness": 5, "sun_altitude_angle": 70}
      ],
      "scenarios": [
        {
          "type": "ControlLoss",
          "waypoint_index": 3,
          "yaw": 0,
          "parameters": {}
        }
      ]
    }
  ]
}
Use only scenario types from the supplied allowlist. waypoint_index is a
zero-based index into the supplied template waypoints. Keep it away from the
first and last waypoint when practical. Parameters must use the exact names
from the scenario schema; intervals are objects with numeric "from" and "to".
Generate no more than the requested route and scenario counts.
"""


class GenerationError(ValueError):
    """Raised when model output cannot be safely converted to route XML."""


def _endpoint_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def call_model(
    prompt: str,
    endpoint: str,
    model: str,
    api_key: str | None,
    timeout: float,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        _endpoint_url(endpoint),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (api_key if api_key else "not-needed"),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise GenerationError(f"Model request failed with HTTP {error.code}: {body}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise GenerationError(f"Could not reach model endpoint {_endpoint_url(endpoint)}: {error}") from error
    except json.JSONDecodeError as error:
        raise GenerationError("Model endpoint returned invalid JSON") from error

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise GenerationError("Model response has no choices[0].message.content") from error
    if not isinstance(content, str):
        raise GenerationError("Model message content must be a JSON string")
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise GenerationError(f"Model content is not valid JSON: {error}") from error
    if not isinstance(result, dict):
        raise GenerationError("Model JSON root must be an object")
    return result


def _number(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenerationError(f"{label} must be numeric")
    return str(value)


def _parameter_element(parent: ET.Element, name: str, value: Any) -> None:
    element = ET.SubElement(parent, name)
    if isinstance(value, dict):
        if set(value) == {"from", "to"}:
            element.set("from", _number(value["from"], f"{name}.from"))
            element.set("to", _number(value["to"], f"{name}.to"))
        elif {"x", "y", "z"} <= set(value):
            for coordinate in ("x", "y", "z"):
                element.set(coordinate, _number(value[coordinate], f"{name}.{coordinate}"))
            if "p" in value:
                element.set("p", _number(value["p"], f"{name}.p"))
        else:
            raise GenerationError(
                f"Parameter '{name}' must contain from/to or x/y/z coordinates"
            )
    elif isinstance(value, bool):
        element.set("value", str(value).lower())
    elif isinstance(value, (str, int, float)):
        if isinstance(value, str) and not value.strip():
            raise GenerationError(f"Parameter '{name}' cannot be empty")
        element.set("value", str(value))
    else:
        raise GenerationError(f"Parameter '{name}' has an unsupported value")


def _validate_weather(weather: Any) -> list[dict[str, str]]:
    if not isinstance(weather, list) or not weather:
        raise GenerationError("Each route must contain a non-empty weather list")
    validated = []
    previous_percentage = -1.0
    for index, item in enumerate(weather):
        if not isinstance(item, dict):
            raise GenerationError(f"Weather entry {index} must be an object")
        percentage = item.get("route_percentage")
        if isinstance(percentage, bool) or not isinstance(percentage, (int, float)) or not 0 <= percentage <= 100:
            raise GenerationError(f"Weather entry {index} has an invalid route_percentage")
        if percentage < previous_percentage:
            raise GenerationError("Weather route_percentage values must be sorted")
        previous_percentage = float(percentage)
        attributes = {"route_percentage": str(percentage)}
        for name, value in item.items():
            if name == "route_percentage":
                continue
            if name not in WEATHER_ATTRIBUTES:
                raise GenerationError(f"Unknown weather attribute '{name}'")
            attributes[name] = _number(value, f"weather.{name}")
        validated.append(attributes)
    return validated


def _validate_route(
    route: Any,
    town: str,
    waypoint_count: int,
    max_scenarios: int,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if not isinstance(route, dict):
        raise GenerationError("Each generated route must be an object")
    if route.get("town") not in (None, town):
        raise GenerationError(f"Generated town must match template town '{town}'")
    scenarios = route.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) > max_scenarios:
        raise GenerationError(f"scenarios must be a list of at most {max_scenarios} entries")
    validated_scenarios = []
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise GenerationError(f"Scenario {index} must be an object")
        scenario_type = scenario.get("type")
        if scenario_type not in SCENARIO_PARAMETERS:
            raise GenerationError(f"Unknown scenario type '{scenario_type}'")
        waypoint_index = scenario.get("waypoint_index")
        if isinstance(waypoint_index, bool) or not isinstance(waypoint_index, int):
            raise GenerationError(f"Scenario {index} waypoint_index must be an integer")
        if not 0 <= waypoint_index < waypoint_count:
            raise GenerationError(f"Scenario {index} waypoint_index is outside the template route")
        parameters = scenario.get("parameters", {})
        if not isinstance(parameters, dict):
            raise GenerationError(f"Scenario {index} parameters must be an object")
        allowed = set(SCENARIO_PARAMETERS[scenario_type])
        unknown = set(parameters) - allowed
        missing = allowed - set(parameters)
        if unknown:
            raise GenerationError(f"{scenario_type} has unknown parameters: {sorted(unknown)}")
        if missing:
            raise GenerationError(f"{scenario_type} is missing parameters: {sorted(missing)}")
        yaw = scenario.get("yaw", 0)
        _number(yaw, f"scenario {index}.yaw")
        validated_scenarios.append({
            "type": scenario_type,
            "waypoint_index": waypoint_index,
            "yaw": yaw,
            "parameters": parameters,
        })
    return _validate_weather(route.get("weather")), validated_scenarios


def _next_route_id(root: ET.Element) -> int:
    ids = []
    for route in root.findall("route"):
        try:
            ids.append(int(route.attrib["id"]))
        except (KeyError, ValueError):
            continue
    return max(ids, default=-1) + 1


def _copy_template(root: ET.Element, route_id: str) -> ET.Element:
    for route in root.findall("route"):
        if route.attrib.get("id") == route_id:
            return route
    raise GenerationError(f"Template route id '{route_id}' was not found")


def _append_generated_routes(
    root: ET.Element,
    template: ET.Element,
    generated: list[tuple[list[dict[str, str]], list[dict[str, Any]]]],
    starting_id: int,
) -> None:
    town = template.attrib["town"]
    template_waypoints = template.find("waypoints")
    if template_waypoints is None or not list(template_waypoints):
        raise GenerationError("Template route has no waypoints")
    for offset, (weather, scenarios) in enumerate(generated):
        route = ET.SubElement(root, "route", {"id": str(starting_id + offset), "town": town})
        weather_element = ET.SubElement(route, "weathers")
        for attributes in weather:
            ET.SubElement(weather_element, "weather", attributes)
        route.append(ET.fromstring(ET.tostring(template_waypoints, encoding="unicode")))
        scenarios_element = ET.SubElement(route, "scenarios")
        points = list(template_waypoints)
        for number, scenario in enumerate(scenarios, start=1):
            scenario_type = scenario["type"]
            scenario_element = ET.SubElement(
                scenarios_element,
                "scenario",
                {"name": f"{scenario_type}_{starting_id + offset}_{number}", "type": scenario_type},
            )
            point = points[scenario["waypoint_index"]]
            trigger = ET.SubElement(scenario_element, "trigger_point")
            for name in ("x", "y", "z"):
                trigger.set(name, point.attrib[name])
            trigger.set("yaw", str(scenario["yaw"]))
            for name, value in scenario["parameters"].items():
                _parameter_element(scenario_element, name, value)


def _indent(root: ET.Element) -> None:
    if hasattr(ET, "indent"):
        ET.indent(root, space="   ")


def write_xml(root: ET.Element, output: Path, backup: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if backup and output.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = output.with_name(f"{output.name}.{timestamp}.bak")
        shutil.copy2(output, backup_path)
        print(f"Backup written to {backup_path}")
    _indent(root)
    with tempfile.NamedTemporaryFile("wb", dir=output.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
    temporary_path.replace(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--description", help="Natural-language scenario description")
    parser.add_argument("--output", default=os.getenv("SCENARIO_OUTPUT", DEFAULT_OUTPUT))
    parser.add_argument("--template-route", required=True, help="Existing route id used for valid waypoints")
    parser.add_argument("--count", type=int, default=1, help="Number of route variants to append")
    parser.add_argument("--max-scenarios", type=int, default=MAX_SCENARIOS)
    parser.add_argument("--endpoint", default=os.getenv("SCENARIO_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.getenv("SCENARIO_LLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--api-key", default=os.getenv("SCENARIO_LLM_API_KEY"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Skip the final confirmation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.count < 1 or args.max_scenarios < 0:
        raise GenerationError("--count must be positive and --max-scenarios cannot be negative")
    description = args.description or input("Describe the CARLA scenario(s): ").strip()
    if not description:
        raise GenerationError("A non-empty scenario description is required")

    output = Path(args.output)
    try:
        tree = ET.parse(output)
        root = tree.getroot()
        template = _copy_template(root, args.template_route)
        town = template.attrib.get("town")
        waypoints = template.find("waypoints")
        if not town or waypoints is None:
            raise GenerationError("Template route must contain a town and waypoints")
        prompt = (
            f"Template town: {town}\n"
            f"Template waypoint count: {len(list(waypoints))}\n"
            f"Requested route variants: {args.count}\n"
            f"Maximum scenarios per route: {args.max_scenarios}\n"
            f"Allowed scenario parameter schema: {json.dumps(SCENARIO_PARAMETERS, sort_keys=True)}\n"
            f"Allowed weather attributes: {json.dumps(sorted(WEATHER_ATTRIBUTES))}\n"
            f"User description: {description}"
        )
        response = call_model(prompt, args.endpoint, args.model, args.api_key, args.timeout)
        routes = response.get("routes")
        if not isinstance(routes, list) or len(routes) != args.count:
            raise GenerationError(f"Model must return exactly {args.count} route objects")
        generated = [
            _validate_route(route, town, len(list(waypoints)), args.max_scenarios)
            for route in routes
        ]
        next_id = _next_route_id(root)
        print(f"Generated {len(generated)} route(s) for {town} using template {args.template_route}.")
        for index, (_, scenarios) in enumerate(generated):
            print(f"  route {next_id + index}: {len(scenarios)} scenario(s)")
        if not args.yes and input("Type YES to append and write the XML: ").strip() != "YES":
            print("No changes written.")
            return 0
        _append_generated_routes(root, template, generated, next_id)
        write_xml(root, output, backup=not args.no_backup)
        print(f"Wrote {output}")
        return 0
    except (ET.ParseError, OSError) as error:
        raise GenerationError(f"Could not read or write {output}: {error}") from error


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GenerationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
