#!/usr/bin/env python3
"""Generate CARLA leaderboard routes from a natural-language scenario description.

The model supplies scenario choices and weather, while route geometry is copied
from an existing route. This keeps generated coordinates valid for the selected
CARLA town and makes the tool usable with local or remote OpenAI-compatible APIs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_OUTPUT = "data/routes_devtest.xml"
DEFAULT_ENDPOINT = "http://localhost:9000/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
MAX_SCENARIOS = 10
DEFAULT_CARLA_HOST = "localhost"
DEFAULT_CARLA_PORT = 2000
DEFAULT_MAX_COMPLETION_TOKENS = 4096
DEFAULT_MIN_SPACING = 10.0      # metres along the route between two triggers
DEFAULT_SAMPLE_STEP = 20.0      # metres between route points listed in the prompt
# Same thresholds as leaderboard/utils/route_parser.py
DIST_THRESHOLD = 2.0
ANGLE_THRESHOLD = 10.0
REPO_ROOT = Path(__file__).resolve().parent.parent

PARAMETER_ENUMS = {"direction": {"left", "right"}}
POSITIVE_PARAMETERS = {
    "distance", "speed", "flow_speed", "frequency", "bicycle_speed",
    "bicycle_drive_distance", "front_vehicle_distance", "behind_vehicle_distance",
}

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
The caller supplies the dense route that Leaderboard will actually drive: a list
of route points, each with an integer index, x, y, z, yaw and the distance
travelled along the route in metres (s). The list is downsampled, but every
integer index from 0 to the last index is a valid choice. Never invent
coordinates: you choose WHERE a scenario happens by giving a route_index, and the
caller derives the exact trigger_point (x, y, z, yaw) from that route point.
Return only one JSON object with this shape:
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
          "route_index": 120,
          "parameters": {}
        }
      ]
    }
  ]
}
Rules:
1. Use only scenario types from the supplied allowlist and the exact parameter
   names from the scenario schema. Intervals are objects with numeric "from"
   and "to". Generate no more than the requested route and scenario counts.
2. Every scenario MUST contain an integer route_index within the valid range.
   Do not output x, y, z, yaw, trigger_point, trigger_reference, waypoint_index
   or yaw_offset.
3. Scenarios on one route must be far apart along the route (at least the
   supplied minimum spacing in metres, measured with s) and must not repeat
   the same route_index. List them in increasing route_index order.
4. If validation feedback is supplied, correct every listed scenario and
   return the complete corrected JSON object.

For reference only, this is how a finished Leaderboard route looks in XML. The
caller writes the XML; your response must still be JSON in the shape above.
Each scenario's trigger_point is a point of the route, and the scenario's
parameters are child elements:

<route id="0" town="Town12">
  <weathers>
    <weather route_percentage="0" cloudiness="5.0"
             precipitation="0.0" sun_altitude_angle="90.0"/>
    <weather route_percentage="100" cloudiness="5.0"
             precipitation="0.0" sun_altitude_angle="15.0"/>
  </weathers>
  <waypoints>
    <position x="983.5" y="5382.2" z="371"/>
    <position x="824.2" y="5575.3" z="371"/>
  </waypoints>
  <scenarios>
    <scenario name="ParkingExit_1" type="ParkingExit">
      <trigger_point x="983.5" y="5382.2" z="371" yaw="90"/>
      <direction value="right"/>
      <front_vehicle_distance value="9"/>
      <behind_vehicle_distance value="9"/>
    </scenario>
    <scenario name="Accident_1" type="Accident">
      <trigger_point x="427.2" y="6086.8" z="359" yaw="144"/>
    </scenario>
  </scenarios>
</route>
"""


class GenerationError(ValueError):
    """Raised when model output cannot be safely converted to route XML."""


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


@dataclass(frozen=True)
class RoutePoint:
    """One point of the dense route Leaderboard drives (1 m hop)."""
    x: float
    y: float
    z: float
    yaw: float
    distance: float  # metres travelled along the route

    def trigger(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z, "yaw": self.yaw}


class DenseRoute:
    """The interpolated route, plus the real Leaderboard trigger check.

    ``carla_route`` is the raw ``[(carla.Transform, RoadOption)]`` list returned
    by ``interpolate_trajectory``. When present, ``RouteParser.is_scenario_at_route``
    is also called on it so the generator and the evaluator cannot disagree.
    """

    def __init__(self, points: list[RoutePoint], carla_route: Any = None) -> None:
        if not points:
            raise GenerationError("The interpolated route is empty")
        self.points = points
        self.carla_route = carla_route

    def __len__(self) -> int:
        return len(self.points)

    @classmethod
    def from_poses(cls, poses: list[tuple[float, float, float, float]], carla_route: Any = None) -> "DenseRoute":
        points, travelled = [], 0.0
        for number, (x, y, z, yaw) in enumerate(poses):
            if number:
                previous = points[-1]
                travelled += math.hypot(x - previous.x, y - previous.y)
            points.append(RoutePoint(x, y, z, yaw, travelled))
        return cls(points, carla_route)

    def sample_indices(self, step: float) -> list[int]:
        indices, last_distance = [0], 0.0
        for number, point in enumerate(self.points[1:], start=1):
            if point.distance - last_distance >= step:
                indices.append(number)
                last_distance = point.distance
        if indices[-1] != len(self.points) - 1:
            indices.append(len(self.points) - 1)
        return indices

    def describe(self, step: float) -> str:
        lines = ["index x y z yaw s"]
        for number in self.sample_indices(step):
            p = self.points[number]
            lines.append(f"{number} {p.x:.1f} {p.y:.1f} {p.z:.1f} {p.yaw:.0f} {p.distance:.0f}")
        return "\n".join(lines)

    def trigger_errors(self, trigger: dict[str, float]) -> list[str]:
        """Mirror of RouteParser.is_scenario_at_route (with abs() on dz)."""
        nearest, nearest_distance = None, math.inf
        for number, p in enumerate(self.points):
            horizontal = math.hypot(trigger["x"] - p.x, trigger["y"] - p.y)
            vertical = abs(trigger["z"] - p.z)
            yaw_error = _angle_difference(trigger["yaw"], p.yaw)
            if horizontal < DIST_THRESHOLD and vertical < DIST_THRESHOLD and yaw_error < ANGLE_THRESHOLD:
                return self._carla_check(trigger)
            if horizontal < nearest_distance:
                nearest, nearest_distance = (number, horizontal, vertical, yaw_error), horizontal
        number, horizontal, vertical, yaw_error = nearest
        return [
            f"nearest route point is index {number} (dxy={horizontal:.2f}m, "
            f"dz={vertical:.2f}m, dyaw={yaw_error:.1f}deg); limits are "
            f"{DIST_THRESHOLD:g}m, {DIST_THRESHOLD:g}m, {ANGLE_THRESHOLD:g}deg"
        ]

    def _carla_check(self, trigger: dict[str, float]) -> list[str]:
        if self.carla_route is None:
            return []
        import carla
        from leaderboard.utils.route_parser import RouteParser
        transform = carla.Transform(
            carla.Location(trigger["x"], trigger["y"], trigger["z"]),
            carla.Rotation(roll=0.0, pitch=0.0, yaw=trigger["yaw"]),
        )
        if RouteParser.is_scenario_at_route(transform, self.carla_route):
            return []
        return ["rejected by Leaderboard RouteParser.is_scenario_at_route"]


def build_carla_route(
    waypoints: list[ET.Element], town: str, host: str, port: int, timeout: float
) -> DenseRoute:
    """Interpolate the template exactly as Leaderboard does, using a live CARLA server."""
    for path in (str(REPO_ROOT),):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        import carla
        from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
        from leaderboard.utils.route_manipulation import interpolate_trajectory
    except ImportError as error:
        raise GenerationError(
            "CARLA validation needs the carla Python API, scenario_runner and the "
            f"leaderboard package on PYTHONPATH (same environment as run_leaderboard.sh): {error}"
        ) from error
    try:
        client = carla.Client(host, port)
        client.set_timeout(timeout)
        world = client.load_world(town)
        CarlaDataProvider.set_client(client)
        CarlaDataProvider.set_world(world)
        keypoints = [
            carla.Location(*(float(point.attrib[name]) for name in ("x", "y", "z")))
            for point in waypoints
        ]
        _, route = interpolate_trajectory(keypoints)
    except RuntimeError as error:
        raise GenerationError(f"CARLA server {host}:{port} failed while building the route: {error}") from error
    poses = [
        (t.location.x, t.location.y, t.location.z, t.rotation.yaw) for t, _ in route
    ]
    return DenseRoute.from_poses(poses, carla_route=route)


def _endpoint_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _open_model_request(request: urllib.request.Request, timeout: float):
    """Open local model requests without inherited HTTP(S) proxy settings.

    Remote endpoints retain the environment proxy policy. This mirrors the
    networking behavior used by the supplied OpenAI/httpx client.
    """
    host = urlparse(request.full_url).hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


def call_model(
    prompt: str,
    endpoint: str,
    model: str,
    api_key: str | None,
    timeout: float,
    max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_completion_tokens,
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
        with _open_model_request(request, timeout) as response:
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
    return _parse_model_json(content)


def _parse_model_json(content: Any) -> dict[str, Any]:
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


def call_model_llm(prompt: str, model_name: str, api_key: str | None) -> dict[str, Any]:
    """Call a model through Simon Willison's `llm` library (https://github.com/simonw/llm).

    Models, aliases and stored keys (`llm keys set openai`) are shared with the
    `llm` command line tool; plugins add local and other providers.
    """
    try:
        import llm
    except ImportError as error:
        raise GenerationError("The llm backend needs the package: pip install llm") from error
    try:
        model = llm.get_model(model_name)
    except llm.UnknownModelError as error:
        raise GenerationError(f"llm does not know model '{model_name}' (see `llm models`)") from error
    if api_key:
        model.key = api_key
    options = {"json_object": True} if "json_object" in model.Options.model_fields else {}
    try:
        text = model.prompt(prompt, system=SYSTEM_PROMPT, stream=False, options=options).text()
    except llm.ModelError as error:
        raise GenerationError(f"llm model call failed: {error}") from error
    return _parse_model_json(text)


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


def _validate_parameters(scenario_type: str, parameters: dict[str, Any]) -> None:
    allowed = set(SCENARIO_PARAMETERS[scenario_type])
    unknown = set(parameters) - allowed
    missing = allowed - set(parameters)
    if unknown:
        raise GenerationError(f"{scenario_type} has unknown parameters: {sorted(unknown)}")
    if missing:
        raise GenerationError(f"{scenario_type} is missing parameters: {sorted(missing)}")
    for name, value in parameters.items():
        if name in PARAMETER_ENUMS and value not in PARAMETER_ENUMS[name]:
            raise GenerationError(
                f"{scenario_type}.{name} must be one of {sorted(PARAMETER_ENUMS[name])}, got {value!r}"
            )
        if name in POSITIVE_PARAMETERS and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
        ):
            raise GenerationError(f"{scenario_type}.{name} must be a positive number, got {value!r}")
        if isinstance(value, dict) and set(value) == {"from", "to"}:
            low, high = value["from"], value["to"]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (low, high)) or low > high:
                raise GenerationError(f"{scenario_type}.{name} needs numeric from <= to")


def _validate_route(
    route: Any,
    town: str,
    dense_route: DenseRoute,
    max_scenarios: int,
    min_spacing: float = DEFAULT_MIN_SPACING,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Validate one model route. Triggers are derived from the chosen route index."""
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
        route_index = scenario.get("route_index")
        if isinstance(route_index, bool) or not isinstance(route_index, int):
            raise GenerationError(
                f"Scenario {index} ({scenario_type}) must contain an integer route_index "
                f"between 0 and {len(dense_route) - 1}"
            )
        if not 0 <= route_index < len(dense_route):
            raise GenerationError(
                f"Scenario {index} ({scenario_type}) route_index {route_index} is outside "
                f"the valid range 0..{len(dense_route) - 1}"
            )
        point = dense_route.points[route_index]
        # Safety net: run the real Leaderboard acceptance check on the derived trigger.
        errors = dense_route.trigger_errors(point.trigger())
        if errors:
            raise GenerationError(
                f"Scenario {index} ({scenario_type}) route_index {route_index} gives an "
                f"invalid trigger_point: {'; '.join(errors)}"
            )
        parameters = scenario.get("parameters", {})
        if not isinstance(parameters, dict):
            raise GenerationError(f"Scenario {index} parameters must be an object")
        _validate_parameters(scenario_type, parameters)
        for other_index, other in enumerate(validated_scenarios):
            gap = abs(point.distance - dense_route.points[other["route_index"]].distance)
            if gap < min_spacing:
                raise GenerationError(
                    f"Scenario {index} ({scenario_type}, route_index {route_index}) is only "
                    f"{gap:.1f}m along the route from scenario {other_index} "
                    f"({other['type']}, route_index {other['route_index']}); keep at least "
                    f"{min_spacing:g}m apart and use different route_index values"
                )
        validated_scenarios.append({
            "type": scenario_type,
            "route_index": route_index,
            "trigger_point": point.trigger(),
            "parameters": parameters,
        })
    validated_scenarios.sort(key=lambda item: item["route_index"])
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
        for number, scenario in enumerate(scenarios, start=1):
            scenario_type = scenario["type"]
            scenario_element = ET.SubElement(
                scenarios_element,
                "scenario",
                {"name": f"{scenario_type}_{starting_id + offset}_{number}", "type": scenario_type},
            )
            trigger = ET.SubElement(scenario_element, "trigger_point")
            for name, value in scenario["trigger_point"].items():
                trigger.set(name, str(value))
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
    parser.add_argument("--description",
                        help="Natural-language scenario description. Omit it to start an interactive prompt")
    parser.add_argument("--backend", choices=("http", "llm"), default=os.getenv("SCENARIO_LLM_BACKEND", "http"),
                        help="http: OpenAI-compatible endpoint; llm: the `llm` library (pip install llm)")
    parser.add_argument("--output", default=os.getenv("SCENARIO_OUTPUT", DEFAULT_OUTPUT))
    parser.add_argument("--template-route", required=True, help="Existing route id used for valid waypoints")
    parser.add_argument("--count", type=int, default=1, help="Number of route variants to append")
    parser.add_argument("--max-scenarios", type=int, default=MAX_SCENARIOS)
    parser.add_argument("--endpoint", default=os.getenv("SCENARIO_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.getenv("SCENARIO_LLM_MODEL", DEFAULT_MODEL),
                        help="Model name; with --backend llm, any name or alias from `llm models`")
    parser.add_argument("--api-key", default=os.getenv("SCENARIO_LLM_API_KEY"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-completion-tokens", type=int, default=DEFAULT_MAX_COMPLETION_TOKENS)
    parser.add_argument("--carla-host", default=os.getenv("CARLA_HOST", DEFAULT_CARLA_HOST))
    parser.add_argument("--carla-port", type=int, default=int(os.getenv("CARLA_PORT", DEFAULT_CARLA_PORT)))
    parser.add_argument("--carla-timeout", type=float, default=120.0,
                        help="Seconds to wait for CARLA (loading a large town can be slow)")
    parser.add_argument("--min-spacing", type=float, default=DEFAULT_MIN_SPACING,
                        help="Minimum metres along the route between two scenario triggers")
    parser.add_argument("--sample-step", type=float, default=DEFAULT_SAMPLE_STEP,
                        help="Metres between route points listed to the model")
    parser.add_argument("--max-requests", type=int, default=3,
                        help="Maximum model requests, including validation retries")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Skip the final confirmation")
    return parser.parse_args()


def _build_prompt(args: argparse.Namespace, town: str, dense_route: DenseRoute, description: str) -> str:
    return (
        f"Template town: {town}\n"
        f"Route length: {dense_route.points[-1].distance:.0f} m; valid route_index values: "
        f"0..{len(dense_route) - 1} (listed below every ~{args.sample_step:g} m)\n"
        f"Minimum spacing between scenarios on one route: {args.min_spacing:g} m\n"
        f"Route points:\n{dense_route.describe(args.sample_step)}\n"
        f"Requested route variants: {args.count}\n"
        f"Maximum scenarios per route: {args.max_scenarios}\n"
        f"Allowed scenario parameter schema: {json.dumps(SCENARIO_PARAMETERS, sort_keys=True)}\n"
        f"Allowed weather attributes: {json.dumps(sorted(WEATHER_ATTRIBUTES))}\n"
        f"User description: {description}"
    )


def _ask_model(args: argparse.Namespace, prompt: str) -> dict[str, Any]:
    if args.backend == "llm":
        return call_model_llm(prompt, args.model, args.api_key)
    return call_model(
        prompt, args.endpoint, args.model, args.api_key, args.timeout, args.max_completion_tokens
    )


def generate_routes(
    args: argparse.Namespace, description: str, town: str, dense_route: DenseRoute
) -> list[tuple[list[dict[str, str]], list[dict[str, Any]]]]:
    """Ask the model and validate, feeding validation errors back until max_requests."""
    base_prompt = _build_prompt(args, town, dense_route, description)
    feedback = ""
    for attempt in range(1, args.max_requests + 1):
        try:
            routes = _ask_model(args, base_prompt + feedback).get("routes")
            if not isinstance(routes, list) or len(routes) != args.count:
                raise GenerationError(f"Model must return exactly {args.count} route objects")
            return [
                _validate_route(route, town, dense_route, args.max_scenarios, args.min_spacing)
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


def _confirm_and_write(
    args: argparse.Namespace,
    root: ET.Element,
    template: ET.Element,
    output: Path,
    generated: list[tuple[list[dict[str, str]], list[dict[str, Any]]]],
) -> None:
    town = template.attrib["town"]
    next_id = _next_route_id(root)
    print(f"Generated {len(generated)} route(s) for {town} using template {args.template_route}.")
    for index, (_, scenarios) in enumerate(generated):
        print(f"  route {next_id + index}: {len(scenarios)} scenario(s)")
        for scenario in scenarios:
            trigger = scenario["trigger_point"]
            print(f"    {scenario['type']} at route_index {scenario['route_index']} "
                  f"({trigger['x']:.1f}, {trigger['y']:.1f}, yaw {trigger['yaw']:.0f})")
    if not args.yes and input("Type YES to append and write the XML: ").strip() != "YES":
        print("No changes written.")
        return
    _append_generated_routes(root, template, generated, next_id)
    write_xml(root, output, backup=not args.no_backup)
    print(f"Wrote {output}")


INTERACTIVE_HELP = """Type a scenario description and press Enter.
  /count N   number of route variants per request (now {count})
  /help      show this help
  /quit      leave (Ctrl-D also works)"""


def interactive_loop(args, root, template, output, town, dense_route) -> None:
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
            generated = generate_routes(args, line, town, dense_route)
            _confirm_and_write(args, root, template, output, generated)
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
        template = _copy_template(root, args.template_route)
        town = template.attrib.get("town")
        waypoints = template.find("waypoints")
        if not town or waypoints is None:
            raise GenerationError("Template route must contain a town and waypoints")
        # Built once: loading the town and routing is the slow part.
        dense_route = build_carla_route(
            list(waypoints), town, args.carla_host, args.carla_port, args.carla_timeout
        )
        if interactive:
            interactive_loop(args, root, template, output, town, dense_route)
        else:
            generated = generate_routes(args, args.description, town, dense_route)
            _confirm_and_write(args, root, template, output, generated)
        return 0
    except (ET.ParseError, OSError) as error:
        raise GenerationError(f"Could not read or write {output}: {error}") from error


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GenerationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
