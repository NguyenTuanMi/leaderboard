from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if __package__:
    from .plugins.scenario_at_route_validation import ScenarioAtRouteValidation
    from .plugins.scenario_parameter_validation import ParameterValidation
    from .plugins.validation_base import ValidationBase
    from .plugins.validation_helper import ValidationContext, ValidationOutput
    from .plugins.weather_validation import WeatherValidation
else:
    from plugins.scenario_at_route_validation import ScenarioAtRouteValidation
    from plugins.scenario_parameter_validation import ParameterValidation
    from plugins.validation_base import ValidationBase
    from plugins.validation_helper import ValidationContext, ValidationOutput
    from plugins.weather_validation import WeatherValidation


PLUGIN_REGISTRY: dict[str, type[ValidationBase]] = {
    "weather": WeatherValidation,
    "scenario_parameters": ParameterValidation,
    "scenario_at_route": ScenarioAtRouteValidation,
}


class ValidatorConfigurationError(ValueError):
    """Raised when validator configuration or route selection is invalid."""


class CarlaValidator:
    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if config is None:
            config = {}
        if not isinstance(config, dict):
            raise ValidatorConfigurationError("validator config must be a YAML mapping")
        self.validation_config = config.get("validation")
        if not isinstance(self.validation_config, dict):
            raise ValidatorConfigurationError("config must contain a 'validation' mapping")

    def validate_route(self, route_file: str | Path, route_id: str) -> dict[str, ValidationOutput]:
        route_path = Path(route_file).expanduser().resolve()
        root = ET.parse(route_path).getroot()
        matches = [route for route in root.findall("route") if route.get("id") == str(route_id)]
        if len(matches) != 1:
            raise ValidatorConfigurationError(
                f"expected exactly one route id {route_id!r} in {route_path}, found {len(matches)}"
            )
        route_element = matches[0]
        if route_element.find("scenarios") is None:
            raise ValidatorConfigurationError(f"route {route_id!r} has no <scenarios> element")
        context = ValidationContext(str(route_path), str(route_id), route_element)

        results = {}
        for plugin_name, plugin_config in self.validation_config.items():
            plugin_class = PLUGIN_REGISTRY.get(plugin_name)
            if plugin_class is None:
                raise ValidatorConfigurationError(f"unknown validation plugin {plugin_name!r}")
            if not isinstance(plugin_config, Mapping):
                raise ValidatorConfigurationError(
                    f"validation.{plugin_name} must be a mapping with an enabled flag and options"
                )
            if plugin_config.get("enabled", True) is False:
                continue

            plugin = plugin_class(plugin_name, plugin_config)
            try:
                result = plugin.validate(context)
                if not isinstance(result, ValidationOutput):
                    raise TypeError("plugin validate() must return ValidationOutput")
            except Exception as error:
                result = ValidationOutput(
                    valid=False,
                    errors=[f"plugin raised {type(error).__name__}: {error}"],
                )
            results[plugin_name] = result

        if not results:
            raise ValidatorConfigurationError("no validation plugins are enabled")
        return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate one route XML file with configured plugins")
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "carla_validator" / "config" / "carla_config.yaml",
        help="YAML file selecting validation plugins and their schemas",
    )
    parser.add_argument("--route-file", required=True, type=Path)
    parser.add_argument("--route-id", required=True)
    args = parser.parse_args(argv)

    try:
        validator = CarlaValidator(args.config)
        results = validator.validate_route(args.route_file, args.route_id)
    except (OSError, ET.ParseError, ValidatorConfigurationError, json.JSONDecodeError) as error:
        print(f"VALIDATION ERROR: {error}", file=sys.stderr)
        return 2

    all_valid = True
    for plugin_name, result in results.items():
        status = "PASS" if result.valid else "FAIL"
        print(f"[{status}] {plugin_name}")
        for key, value in result.details.items():
            print(f"  {key}: {value}")
        for error in result.errors:
            print(f"  ERROR: {error}")
        all_valid = all_valid and result.valid

    if all_valid:
        print(f"VALIDATION PASSED: {args.route_file}, route {args.route_id}")
        return 0
    print(f"VALIDATION FAILED: {args.route_file}, route {args.route_id}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
