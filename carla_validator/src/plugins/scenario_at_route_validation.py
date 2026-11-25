from __future__ import annotations

import math

from .validation_base import ValidationBase
from .validation_helper import ValidationContext, ValidationOutput


class ScenarioAtRouteValidation(ValidationBase):
    """Check scenario triggers against the active map's interpolated Leaderboard route."""

    def validate(self, context: ValidationContext) -> ValidationOutput:
        try:
            import carla
            from leaderboard.scenariomanager.simulation_data import SimulationData
            from leaderboard.scenarios.route_scenario import RouteScenario
            from leaderboard.utils.route_manipulation import interpolate_trajectory
            from leaderboard.utils.route_parser import ANGLE_THRESHOLD, DIST_THRESHOLD, RouteParser

            town = context.route_element.get("town")
            if not town:
                return ValidationOutput(valid=False, errors=["selected route has no town attribute"])

            client = carla.Client(
                str(self.cfg.get("host", "127.0.0.1")),
                int(self.cfg.get("port", 2000)),
            )
            client.set_timeout(float(self.cfg.get("timeout", 10.0)))
            world = client.get_world()
            current_town = world.get_map().name.rstrip("/").split("/")[-1]
            if current_town != town:
                return ValidationOutput(
                    valid=False,
                    errors=[
                        f"active CARLA map is {current_town!r}, but route requires {town!r}; "
                        "validator will not load or change the world"
                    ],
                )

            route_configs = RouteParser.parse_routes_file(
                context.route_file,
                routes_subset=context.route_id,
            )
            route_config = next(
                (item for item in route_configs if item.name == f"RouteScenario_{context.route_id}"),
                None,
            )
            if route_config is None:
                return ValidationOutput(
                    valid=False,
                    errors=[f"Leaderboard RouteParser could not parse route id {context.route_id!r}"],
                )
            if len(route_config.keypoints) < 2:
                return ValidationOutput(valid=False, errors=["route requires at least two waypoints"])

            SimulationData.set_client(client)
            SimulationData.prepare_world(world)
            _, dense_route = interpolate_trajectory(route_config.keypoints)
            if not dense_route:
                return ValidationOutput(valid=False, errors=["route interpolated to no points"])

            scenario_probe = object.__new__(RouteScenario)
            scenario_classes = scenario_probe.get_all_scenario_classes()
            errors = []
            for scenario in route_config.scenario_configs:
                label = f"{scenario.name} ({scenario.type})"
                if scenario.type not in scenario_classes:
                    errors.append(f"{label}: scenario class is not registered by this Leaderboard checkout")
                if len(scenario.trigger_points) != 1:
                    errors.append(f"{label}: exactly one trigger_point is required")
                    continue

                trigger = scenario.trigger_points[0]
                matched = any(
                    _matches_trigger(trigger, transform, DIST_THRESHOLD, ANGLE_THRESHOLD)
                    for transform, _ in dense_route
                )
                if matched and RouteParser.is_scenario_at_route(trigger, dense_route):
                    continue

                nearest = min(
                    (
                        _trigger_metrics(trigger, transform, index)
                        for index, (transform, _) in enumerate(dense_route)
                    ),
                    key=lambda metrics: (metrics[0], metrics[1], metrics[2]),
                )
                dxy, dz, dyaw, point_index = nearest
                route_transform = dense_route[point_index][0]
                location = route_transform.location
                errors.append(
                    f"{label}: trigger rejected; nearest dense route point {point_index}: "
                    f"dxy={dxy:.2f}m (must be < {DIST_THRESHOLD:g}), "
                    f"|dz|={dz:.2f}m (must be < {DIST_THRESHOLD:g}), "
                    f"yaw error={dyaw:.2f}deg (must be < {ANGLE_THRESHOLD:g}); "
                    f"use transform x={location.x:.3f}, y={location.y:.3f}, "
                    f"z={location.z:.3f}, yaw={route_transform.rotation.yaw:.3f}"
                )

            return ValidationOutput(
                valid=not errors,
                errors=errors,
                details={"town": town, "dense_route_points": len(dense_route)},
            )
        except Exception as error:
            return ValidationOutput(
                valid=False,
                errors=[f"scenario_at_route could not validate against the active CARLA map: {error}"],
            )


def _trigger_metrics(trigger, route_transform, point_index: int) -> tuple[float, float, float, int]:
    dx = trigger.location.x - route_transform.location.x
    dy = trigger.location.y - route_transform.location.y
    dz = abs(trigger.location.z - route_transform.location.z)
    yaw_delta = (trigger.rotation.yaw - route_transform.rotation.yaw) % 360.0
    yaw_error = min(yaw_delta, 360.0 - yaw_delta)
    return math.hypot(dx, dy), dz, yaw_error, point_index


def _matches_trigger(trigger, route_transform, distance_threshold: float, angle_threshold: float) -> bool:
    dxy, dz, dyaw, _ = _trigger_metrics(trigger, route_transform, 0)
    return dxy < distance_threshold and dz < distance_threshold and dyaw < angle_threshold