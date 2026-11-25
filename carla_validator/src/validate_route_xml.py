#!/usr/bin/env python3
"""Validate generated route XML against the active CARLA world and evaluator."""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


DIST_THRESHOLD = 2.0
ANGLE_THRESHOLD = 10.0
DEFAULT_MIN_SPACING = 10.0

SCENARIO_PARAMETERS = {
    "Accident": ("distance", "direction", "speed"),
    "AccidentTwoWays": ("distance", "frequency"),
    "BaseVehicleTurning": (),
    "BlockedIntersection": (),
    "ConstructionObstacle": ("distance", "direction", "speed"),
    "ConstructionObstacleTwoWays": ("distance", "frequency"),
    "ControlLoss": (),
    "CrossingBicycleFlow": ("start_actor_flow", "flow_speed", "source_dist_interval"),
    "DynamicObjectCrossing": ("distance", "direction", "blocker_model", "crossing_angle"),
    "EnterActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "EnterActorFlowV2": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "HardBreakRoute": (),
    "HazardAtSideLane": ("distance", "speed", "bicycle_drive_distance", "bicycle_speed"),
    "HazardAtSideLaneTwoWays": ("distance", "frequency", "bicycle_drive_distance", "bicycle_speed"),
    "HighwayCutIn": ("other_actor_location",),
    "HighwayExit": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InterurbanActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InterurbanAdvancedActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InvadingTurn": ("distance", "offset"),
    "JunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "JunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "MergerIntoSlowTraffic": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "MergerIntoSlowTrafficV2": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "NonSignalizedJunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "NonSignalizedJunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "OppositeVehicleJunction": ("direction",),
    "OppositeVehicleRunningRedLight": ("direction",),
    "OppositeVehicleTakingPriority": ("direction",),
    "ParkedObstacle": ("distance", "direction", "speed"),
    "ParkedObstacleTwoWays": ("distance", "frequency"),
    "ParkingCrossingPedestrian": ("distance", "direction", "crossing_angle"),
    "ParkingCutIn": ("direction",),
    "ParkingExit": ("direction", "front_vehicle_distance", "behind_vehicle_distance"),
    "PedestrianCrossing": (),
    "PriorityAtJunction": (),
    "SignalizedJunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "SignalizedJunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "StaticCutIn": ("distance", "direction"),
    "VehicleOpensDoorTwoWays": ("distance", "frequency"),
    "VehicleTurningRoute": (),
    "VehicleTurningRoutePedestrian": (),
    "YieldToEmergencyVehicle": ("distance",),
}

DIRECTION_VALUES = {"left", "right"}
POSITIVE_VALUES = {
    "distance", "speed", "flow_speed", "frequency", "bicycle_speed",
    "bicycle_drive_distance", "front_vehicle_distance", "behind_vehicle_distance",
}
WEATHER_ATTRIBUTES = {
    "cloudiness", "precipitation", "precipitation_deposits", "wetness",
    "wind_intensity", "sun_azimuth_angle", "sun_altitude_angle", "fog_density",
    "fog_distance", "scatter_intensity", "mie_scattering_scale",
    "rayleigh_scattering_scale", "dust_storm", "fog_falloff",
}


class ValidationFailure(Exception):
    pass


def _finite_number(value: str, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValidationFailure(f"{label} must be numeric, got {value!r}") from error
    if not math.isfinite(number):
        raise ValidationFailure(f"{label} must be finite, got {value!r}")
    return number


def _angle_error(first: float, second: float) -> float:
    return abs((first - second + 180.0) % 360.0 - 180.0)


def _route_distances(route: list) -> list[float]:
    travelled = [0.0]
    for (previous, _), (current, _) in zip(route, route[1:]):
        travelled.append(
            travelled[-1]
            + math.hypot(
                current.location.x - previous.location.x,
                current.location.y - previous.location.y,
            )
        )
    return travelled


def _validate_weather(route_element: ET.Element, carla_module) -> list[str]:
    errors = []
    weather_group = route_element.find("weathers")
    if weather_group is None or not list(weather_group):
        return errors  # RouteParser supplies its default weather when omitted.

    valid_attributes = {
        name for name in WEATHER_ATTRIBUTES
        if hasattr(carla_module.WeatherParameters, name)
    }
    previous_percentage = -1.0
    for index, weather in enumerate(weather_group.findall("weather")):
        percentage = _finite_number(
            weather.get("route_percentage", ""),
            f"weather[{index}].route_percentage",
        )
        if not 0.0 <= percentage <= 100.0:
            errors.append(f"weather[{index}].route_percentage must be within 0..100")
        if percentage < previous_percentage:
            errors.append("weather route_percentage values must be nondecreasing")
        previous_percentage = percentage
        for name, value in weather.attrib.items():
            if name == "route_percentage":
                continue
            if name not in valid_attributes:
                errors.append(f"weather[{index}] has unsupported attribute {name!r}")
                continue
            _finite_number(value, f"weather[{index}].{name}")
    return errors


def _validate_parameters(scenario_config) -> list[str]:
    scenario_type = scenario_config.type
    schema = SCENARIO_PARAMETERS.get(scenario_type)
    if schema is None:
        return [f"no parameter schema is defined for scenario type {scenario_type!r}"]

    parameters = scenario_config.other_parameters
    errors = []
    expected = set(schema)
    supplied = set(parameters)
    for name in sorted(expected - supplied):
        errors.append(f"{scenario_type} is missing required parameter {name!r}")
    for name in sorted(supplied - expected):
        errors.append(f"{scenario_type} has unsupported parameter {name!r}")

    for name in sorted(expected & supplied):
        attributes = parameters[name]
        if name == "direction":
            if attributes.get("value") not in DIRECTION_VALUES:
                errors.append(f"{scenario_type}.{name} must be 'left' or 'right'")
            continue
        if {"from", "to"} <= set(attributes):
            low = _finite_number(attributes["from"], f"{scenario_type}.{name}.from")
            high = _finite_number(attributes["to"], f"{scenario_type}.{name}.to")
            if low > high:
                errors.append(f"{scenario_type}.{name} requires from <= to")
            if name in POSITIVE_VALUES and low <= 0.0:
                errors.append(f"{scenario_type}.{name}.from must be positive")
            if name in POSITIVE_VALUES and high <= 0.0:
                errors.append(f"{scenario_type}.{name}.to must be positive")
        elif {"x", "y", "z"} <= set(attributes):
            for coordinate in ("x", "y", "z"):
                _finite_number(attributes[coordinate], f"{scenario_type}.{name}.{coordinate}")
        elif "value" in attributes:
            value = attributes["value"]
            if name in POSITIVE_VALUES:
                if _finite_number(value, f"{scenario_type}.{name}") <= 0.0:
                    errors.append(f"{scenario_type}.{name} must be positive")
            elif name != "blocker_model":
                _finite_number(value, f"{scenario_type}.{name}")
        else:
            errors.append(
                f"{scenario_type}.{name} must use a value, from/to, or x/y/z attribute"
            )
    return errors


def validate_route(route_config, route: list, route_element: ET.Element, scenario_classes: dict,
                   route_parser, carla_module, min_spacing: float,
                   max_scenarios: int | None) -> list[str]:
    errors = []
    scenarios = route_config.scenario_configs
    if not scenarios:
        errors.append(f"route {route_config.name} has no scenarios")
    if max_scenarios is not None and len(scenarios) > max_scenarios:
        errors.append(
            f"route {route_config.name} has {len(scenarios)} scenarios; maximum is {max_scenarios}"
        )
    errors.extend(_validate_weather(route_element, carla_module))

    if not route:
        return errors + [f"route {route_config.name} interpolated to no points"]
    distances = _route_distances(route)
    seen_names = set()
    accepted_positions = []

    for index, scenario in enumerate(scenarios):
        label = f"scenario[{index}] {scenario.name or '<unnamed>'} ({scenario.type})"
        if not scenario.name:
            errors.append(f"{label}: name is required")
        elif scenario.name in seen_names:
            errors.append(f"{label}: duplicate scenario name")
        seen_names.add(scenario.name)

        if scenario.type not in scenario_classes:
            errors.append(f"{label}: class is not registered by this ue58 checkout")
        errors.extend(f"{label}: {error}" for error in _validate_parameters(scenario))

        if len(scenario.trigger_points) != 1:
            errors.append(f"{label}: exactly one trigger_point is required")
            continue

        trigger = scenario.trigger_points[0]
        matched = []
        nearest = None
        for point_index, (route_transform, _) in enumerate(route):
            dx = trigger.location.x - route_transform.location.x
            dy = trigger.location.y - route_transform.location.y
            dxy = math.hypot(dx, dy)
            dz = abs(trigger.location.z - route_transform.location.z)
            dyaw = _angle_error(trigger.rotation.yaw, route_transform.rotation.yaw)
            metrics = (dxy, dz, dyaw, point_index)
            if nearest is None or metrics[:3] < nearest[:3]:
                nearest = metrics
            if dxy < DIST_THRESHOLD and dz < DIST_THRESHOLD and dyaw < ANGLE_THRESHOLD:
                matched.append(metrics)

        evaluator_accepts = (
            scenario.type in scenario_classes
            and route_parser.is_scenario_at_route(trigger, route)
        )
        if not matched or not evaluator_accepts:
            if nearest is None:
                errors.append(f"{label}: no route transforms to compare with")
            else:
                dxy, dz, dyaw, point_index = nearest
                errors.append(
                    f"{label}: trigger rejected; nearest dense route point {point_index}: "
                    f"dxy={dxy:.2f}m (must be < {DIST_THRESHOLD:g}), "
                    f"|dz|={dz:.2f}m (must be < {DIST_THRESHOLD:g}), "
                    f"yaw error={dyaw:.2f}deg (must be < {ANGLE_THRESHOLD:g})"
                )
            continue

        best = min(matched, key=lambda item: (item[0], item[1], item[2]))
        point_index = best[3]
        route_s = distances[point_index]
        equivalent = [item for item in matched if abs(distances[item[3]] - route_s) >= min_spacing]
        if equivalent:
            errors.append(
                f"{label}: trigger ambiguously matches multiple route positions; "
                "this route overlaps itself near the trigger, so along-route spacing cannot be certified"
            )
            continue
        accepted_positions.append((index, scenario.name, scenario.type, point_index, route_s))

    for previous, current in zip(accepted_positions, accepted_positions[1:]):
        gap = current[4] - previous[4]
        if gap < -1e-6:
            errors.append(
                f"scenario order is not increasing along route: {current[1]} occurs before {previous[1]}"
            )

    for first_index, first in enumerate(accepted_positions):
        for second in accepted_positions[first_index + 1:]:
            gap = abs(second[4] - first[4])
            if gap < min_spacing:
                errors.append(
                    f"scenarios {first[1]} and {second[1]} are only {gap:.2f}m apart along route; "
                    f"minimum spacing is {min_spacing:.2f}m"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate scenario triggers in route XML against the active CARLA world."
    )
    parser.add_argument("--route-file", required=True, type=Path)
    parser.add_argument("--route-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument("--timeout", default=10.0, type=float)
    parser.add_argument("--min-spacing", default=DEFAULT_MIN_SPACING, type=float)
    parser.add_argument(
        "--max-scenarios",
        default=None,
        type=int,
        help="optional cap for generated routes; by default existing route totals are not capped",
    )
    args = parser.parse_args()

    if args.min_spacing < 0 or (args.max_scenarios is not None and args.max_scenarios < 1):
        print("ERROR: min-spacing must be nonnegative and max-scenarios must be positive", file=sys.stderr)
        return 2

    try:
        import carla
        from leaderboard.scenariomanager.simulation_data import SimulationData
        from leaderboard.scenarios.route_scenario import RouteScenario
        from leaderboard.utils.route_manipulation import interpolate_trajectory
        from leaderboard.utils.route_parser import RouteParser

        xml_root = ET.parse(args.route_file).getroot()
        matches = [
            route for route in xml_root.findall("route")
            if route.get("id") == str(args.route_id)
        ]
        if len(matches) != 1:
            raise ValidationFailure(
                f"expected exactly one route id {args.route_id!r}, found {len(matches)}"
            )
        route_element = matches[0]
        town = route_element.get("town")
        if not town:
            raise ValidationFailure(f"route id {args.route_id!r} has no town")

        route_configs = RouteParser.parse_routes_file(str(args.route_file))
        route_config = next(
            (item for item in route_configs if item.name == f"RouteScenario_{args.route_id}"),
            None,
        )
        if route_config is None:
            raise ValidationFailure(f"RouteParser could not parse route id {args.route_id!r}")
        if len(route_config.keypoints) < 2:
            raise ValidationFailure("route requires at least two waypoints")

        client = carla.Client(args.host, args.port)
        client.set_timeout(args.timeout)
        world = client.get_world()
        current_town = world.get_map().name.rstrip("/").split("/")[-1]
        if current_town != town:
            raise ValidationFailure(
                f"active CARLA map is {current_town!r}, but route requires {town!r}; "
                "validator will not reload or change the running world"
            )

        SimulationData.set_client(client)
        SimulationData.prepare_world(world)
        _, dense_route = interpolate_trajectory(route_config.keypoints)
        scenario_probe = object.__new__(RouteScenario)
        scenario_classes = scenario_probe.get_all_scenario_classes()
        errors = validate_route(
            route_config,
            dense_route,
            route_element,
            scenario_classes,
            RouteParser,
            carla,
            args.min_spacing,
            args.max_scenarios,
        )
        if errors:
            for error in errors:
                print(f"ERROR: {error}")
            return 1

        print(
            f"VALIDATION PASSED: {args.route_file}, route {args.route_id}, town {town}; "
            f"{len(route_config.scenario_configs)} scenarios; dense route {len(dense_route)} points; "
            f"spacing >= {args.min_spacing:.2f}m"
        )
        print("No world reload, tick, spawn, or evaluator run was performed.")
        return 0
    except (OSError, ET.ParseError, RuntimeError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
