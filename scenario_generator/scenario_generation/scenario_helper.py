from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from scenario_generator.config.inference_params import *  # noqa: F401,F403

REPO_ROOT = Path(__file__).resolve().parents[2]

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
        # Imported lazily so the module works without the CARLA API installed.
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

def build_prompt(args: argparse.Namespace, town: str, dense_route: DenseRoute, description: str) -> str:
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
        f"User description: {description}\n"
        "Respond with a single JSON object only (no prose, no markdown), in the JSON shape "
        "described in the system prompt."
    )

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

def _next_route_id(root: ET.Element) -> int:
    ids = []
    for route in root.findall("route"):
        try:
            ids.append(int(route.attrib["id"]))
        except (KeyError, ValueError):
            continue
    return max(ids, default=-1) + 1


def copy_template(root: ET.Element, route_id: str) -> ET.Element:
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

def confirm_and_write(
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