from __future__ import annotations

from typing import Any

from scenario_generator.config.inference_params import *  # noqa: F401,F403
from scenario_generator.scenario_generation.scenario_helper import DenseRoute, GenerationError, _number


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


def validate_route(
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

