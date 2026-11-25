from __future__ import annotations

import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from carla_validator.src.carla_validator import CarlaValidator, PLUGIN_REGISTRY
from carla_validator.src.plugins.scenario_at_route_validation import _matches_trigger, _trigger_metrics
from carla_validator.src.plugins.scenario_parameter_validation import ParameterValidation
from carla_validator.src.plugins.validation_helper import ValidationContext
from carla_validator.src.plugins.weather_validation import WeatherValidation


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "carla_validator" / "config" / "carla_config.yaml"
ROUTE_PATH = REPO_ROOT / "data" / "generated" / "routes_devtest_pedcrossing.xml"


def load_route_context(route_id: str) -> ValidationContext:
    root = ET.parse(ROUTE_PATH).getroot()
    route = next(route for route in root.findall("route") if route.get("id") == route_id)
    return ValidationContext(str(ROUTE_PATH), route_id, route)


class ValidatorPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            cls.config = json.load(config_file)["validation"]

    def test_registry_matches_enabled_configuration(self) -> None:
        configured = {
            name for name, options in self.config.items()
            if options.get("enabled", True)
        }
        self.assertEqual(configured, set(PLUGIN_REGISTRY))

    def test_weather_plugin_accepts_existing_route(self) -> None:
        result = WeatherValidation("weather", self.config["weather"]).validate(load_route_context("2"))
        self.assertTrue(result.valid, result.errors)

    def test_parameter_plugin_reports_duplicate_names_in_existing_route(self) -> None:
        result = ParameterValidation(
            "scenario_parameters",
            self.config["scenario_parameters"],
        ).validate(load_route_context("2"))
        self.assertFalse(result.valid)
        duplicates = [error for error in result.errors if "duplicate scenario name" in error]
        self.assertCountEqual(
            duplicates,
            [
                "AccidentTwoWays_2 (AccidentTwoWays): duplicate scenario name",
                "ConstructionObstacle_2 (ConstructionObstacle): duplicate scenario name",
                "HardBreakRoute_4 (HardBreakRoute): duplicate scenario name",
            ],
        )
        self.assertEqual(len(result.errors), 3)

    def test_controller_aggregates_offline_plugins(self) -> None:
        config = copy.deepcopy({"validation": self.config})
        config["validation"]["scenario_at_route"]["enabled"] = False
        route_xml = """<routes><route id="2" town="Town12">
          <weathers><weather route_percentage="0" cloudiness="5"/></weathers>
          <waypoints><position x="0" y="0" z="0"/><position x="1" y="0" z="0"/></waypoints>
          <scenarios><scenario name="ControlLoss_1" type="ControlLoss">
            <trigger_point x="0" y="0" z="0" yaw="0"/>
          </scenario></scenarios>
        </route></routes>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.yaml"
            route_path = Path(temp_dir) / "route.xml"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            route_path.write_text(route_xml, encoding="utf-8")
            results = CarlaValidator(config_path).validate_route(route_path, "2")
        self.assertEqual(set(results), {"weather", "scenario_parameters"})
        self.assertTrue(all(result.valid for result in results.values()))

    def test_trigger_geometry_uses_wrapped_yaw_and_absolute_height(self) -> None:
        trigger = SimpleNamespace(
            location=SimpleNamespace(x=1.0, y=0.0, z=2.0),
            rotation=SimpleNamespace(yaw=359.0),
        )
        route_transform = SimpleNamespace(
            location=SimpleNamespace(x=0.5, y=0.0, z=2.5),
            rotation=SimpleNamespace(yaw=1.0),
        )
        self.assertTrue(_matches_trigger(trigger, route_transform, 2.0, 10.0))
        self.assertEqual(_trigger_metrics(trigger, route_transform, 7), (0.5, 0.5, 2.0, 7))


if __name__ == "__main__":
    unittest.main()