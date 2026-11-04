import json
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from scenario_generator.scenario_generator import (
    GenerationError,
    _append_generated_routes,
    _validate_route,
    call_model,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class ScenarioGeneratorTests(unittest.TestCase):
    def test_call_model_reads_openai_compatible_json(self):
        response = {"choices": [{"message": {"content": '{"routes": []}'}}]}
        with patch("urllib.request.urlopen", return_value=FakeResponse(response)):
            result = call_model("description", "http://localhost:8000/v1", "model", None, 1)
        self.assertEqual(result, {"routes": []})

    def test_validation_rejects_unknown_scenario(self):
        with self.assertRaisesRegex(GenerationError, "Unknown scenario type"):
            _validate_route(
                {
                    "weather": [{"route_percentage": 0}],
                    "scenarios": [{"type": "NotACarlaScenario", "waypoint_index": 1}],
                },
                "Town12",
                3,
                10,
            )

    def test_append_uses_template_waypoints_and_trigger_index(self):
        root = ET.fromstring(
            "<routes><route id='0' town='Town12'>"
            "<weathers><weather route_percentage='0' cloudiness='5'/></weathers>"
            "<waypoints><position x='1' y='2' z='3'/><position x='4' y='5' z='6'/></waypoints>"
            "<scenarios /></route></routes>"
        )
        template = root.find("route")
        generated = _validate_route(
            {
                "weather": [{"route_percentage": 0, "cloudiness": 5}],
                "scenarios": [{"type": "ControlLoss", "waypoint_index": 1, "yaw": 90, "parameters": {}}],
            },
            "Town12",
            2,
            10,
        )
        _append_generated_routes(root, template, [generated], 1)
        route = root.findall("route")[1]
        trigger = route.find("./scenarios/scenario/trigger_point")
        self.assertEqual(route.attrib["id"], "1")
        self.assertEqual(trigger.attrib["x"], "4")
        self.assertEqual(trigger.attrib["yaw"], "90")


if __name__ == "__main__":
    unittest.main()
