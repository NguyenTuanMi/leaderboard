import argparse
import json
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from scenario_generator.scenario_generator import (
    DenseRoute,
    GenerationError,
    _append_generated_routes,
    _parse_model_json,
    _validate_route,
    call_model,
    generate_routes,
    interactive_loop,
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
        with patch(
            "scenario_generator.scenario_generator._open_model_request",
            return_value=FakeResponse(response),
        ):
            result = call_model("description", "http://localhost:8000/v1", "model", None, 1)
        self.assertEqual(result, {"routes": []})

    def _route(self):
        # 101 points, 1 m hop, heading east (yaw 0), then 50 points heading north (yaw 90)
        poses = [(float(i), 0.0, 0.0, 0.0) for i in range(101)]
        poses += [(100.0, float(i), 0.0, 90.0) for i in range(1, 51)]
        return DenseRoute.from_poses(poses)

    def _validate(self, scenarios, **kwargs):
        return _validate_route(
            {"weather": [{"route_percentage": 0}], "scenarios": scenarios},
            "Town12", self._route(), 10, **kwargs,
        )

    def test_validation_rejects_unknown_scenario(self):
        with self.assertRaisesRegex(GenerationError, "Unknown scenario type"):
            self._validate([{"type": "NotACarlaScenario", "route_index": 5}])

    def test_trigger_is_derived_from_route_index(self):
        _, scenarios = self._validate(
            [{"type": "ControlLoss", "route_index": 120, "parameters": {}}]
        )
        self.assertEqual(scenarios[0]["trigger_point"],
                         {"x": 100.0, "y": 20.0, "z": 0.0, "yaw": 90.0})

    def test_rejects_bad_route_index(self):
        for value in (151, -1, "5", 2.5, True, None):
            with self.assertRaisesRegex(GenerationError, "route_index"):
                self._validate([{"type": "ControlLoss", "route_index": value, "parameters": {}}])

    def test_rejects_model_supplied_coordinates_only(self):
        with self.assertRaisesRegex(GenerationError, "integer route_index"):
            self._validate([{"type": "ControlLoss", "parameters": {},
                             "trigger_point": {"x": 1, "y": 2, "z": 3, "yaw": 0}}])

    def test_rejects_close_scenarios_and_sorts_valid_ones(self):
        with self.assertRaisesRegex(GenerationError, "along the route"):
            self._validate([
                {"type": "ControlLoss", "route_index": 40, "parameters": {}},
                {"type": "Accident", "route_index": 45,
                 "parameters": {"distance": 50, "direction": "left", "speed": 60}},
            ])
        _, scenarios = self._validate([
            {"type": "ControlLoss", "route_index": 90, "parameters": {}},
            {"type": "HardBreakRoute", "route_index": 10, "parameters": {}},
        ])
        self.assertEqual([s["route_index"] for s in scenarios], [10, 90])

    def test_parameter_values_are_checked(self):
        with self.assertRaisesRegex(GenerationError, "direction"):
            self._validate([{"type": "ParkedObstacle", "route_index": 10,
                             "parameters": {"distance": 50, "direction": "up", "speed": 60}}])
        with self.assertRaisesRegex(GenerationError, "positive"):
            self._validate([{"type": "ParkedObstacle", "route_index": 10,
                             "parameters": {"distance": -5, "direction": "left", "speed": 60}}])

    def test_trigger_errors_report_nearest_point(self):
        route = self._route()
        self.assertEqual(route.trigger_errors({"x": 50, "y": 0.5, "z": 0, "yaw": 5}), [])
        off_route = route.trigger_errors({"x": 50, "y": 8, "z": 0, "yaw": 0})
        self.assertIn("nearest route point is index 50", off_route[0])
        wrong_yaw = route.trigger_errors({"x": 50, "y": 0, "z": 0, "yaw": 180})
        self.assertIn("dyaw", wrong_yaw[0])

    def test_sample_indices_cover_start_and_end(self):
        indices = self._route().sample_indices(20.0)
        self.assertEqual((indices[0], indices[-1]), (0, 150))

    def test_append_writes_trigger_from_dense_route(self):
        root = ET.fromstring(
            "<routes><route id='0' town='Town12'>"
            "<weathers><weather route_percentage='0' cloudiness='5'/></weathers>"
            "<waypoints><position x='1' y='2' z='3'/><position x='4' y='5' z='6'/></waypoints>"
            "<scenarios/></route></routes>"
        )
        template = root.find("route")
        generated = self._validate([{"type": "ControlLoss", "route_index": 30, "parameters": {}}])
        _append_generated_routes(root, template, [generated], 1)
        route = root.findall("route")[1]
        trigger = route.find("./scenarios/scenario/trigger_point")
        self.assertEqual(route.attrib["id"], "1")
        self.assertEqual((trigger.attrib["x"], trigger.attrib["yaw"]), ("30.0", "0.0"))

    def _args(self, **kwargs):
        values = dict(backend="llm", model="m", api_key=None, endpoint="", timeout=1,
                      max_completion_tokens=100, count=1, max_scenarios=10, min_spacing=10.0,
                      sample_step=20.0, max_requests=3, yes=True, no_backup=True,
                      template_route="0")
        values.update(kwargs)
        return argparse.Namespace(**values)

    def test_generate_routes_retries_with_feedback(self):
        bad = {"routes": [{"weather": [{"route_percentage": 0}],
                           "scenarios": [{"type": "ControlLoss", "route_index": 999, "parameters": {}}]}]}
        good = {"routes": [{"weather": [{"route_percentage": 0}],
                            "scenarios": [{"type": "ControlLoss", "route_index": 5, "parameters": {}}]}]}
        prompts = []

        def fake(args, prompt):
            prompts.append(prompt)
            return bad if len(prompts) == 1 else good

        with patch("scenario_generator.scenario_generator._ask_model", side_effect=fake):
            result = generate_routes(self._args(), "x", "Town12", self._route())
        self.assertEqual(result[0][1][0]["route_index"], 5)
        self.assertIn("VALIDATION RETRY", prompts[1])
        self.assertIn("999", prompts[1])

    def test_interactive_loop_survives_errors_and_writes(self):
        root = ET.fromstring(
            "<routes><route id='0' town='Town12'><weathers/>"
            "<waypoints><position x='1' y='2' z='3'/></waypoints><scenarios/></route></routes>"
        )
        good = {"routes": [{"weather": [{"route_percentage": 0}],
                            "scenarios": [{"type": "ControlLoss", "route_index": 5, "parameters": {}}]}]}
        replies = [{"routes": []}, good]
        args = self._args(max_requests=1)
        with patch("builtins.input", side_effect=["bad prompt", "/count 1", "good prompt", "/quit"]), \
             patch("scenario_generator.scenario_generator._ask_model", side_effect=replies), \
             patch("scenario_generator.scenario_generator.write_xml") as write:
            interactive_loop(args, root, root.find("route"), None, "Town12", self._route())
        self.assertEqual(len(root.findall("route")), 2)
        write.assert_called_once()

    def test_user_prompt_mentions_json(self):
        # OpenAI JSON mode requires "json" in the user input, not only the system prompt.
        from scenario_generator.scenario_generator import _build_prompt
        prompt = _build_prompt(self._args(), "Town12", self._route(), "desc")
        self.assertIn("JSON", prompt)

    def test_parse_model_json_strips_code_fences(self):
        self.assertEqual(_parse_model_json('```json\n{"a": 1}\n```'), {"a": 1})

if __name__ == "__main__":
    unittest.main()
