<h3 align="center">CARLA 1.0 Leaderboard, carla_ws, and Hermes Agent</h3>
<p align="center">
  <em>Scenario generation, validation, ROS 2 control, and route evaluation</em>
</p>

## Table of Contents

- [Summary](#summary)
- [Leaderboard Conda Environment](#leaderboard-conda-environment)
- [Hermes Scenario Workflow](#hermes-scenario-workflow)
- [Run Leaderboard](#run-leaderboard)
- [CARLA Validator](#carla-validator)
- [References](#references)

## Summary

This checkout combines the CARLA 1.0 Leaderboard (`ue58-dev`) with the ROS 2 Humble workspace in `~/carla_ws`. Leaderboard runs on the host in the `carla_1.0` Conda environment. CARLA and the ROS 2 services are separate processes; `carla_ws` runs its ROS 2 services in Docker and connects to the CARLA server over the host network.

Hermes Agent can author route scenarios in a generated XML copy. The configured validator checks the output before it is used for simulation. A successful validation is a configuration and route-geometry gate; it does not simulate the scenario or produce a Leaderboard score.

## Leaderboard Conda Environment

The CARLA 1.0 Python wheel in this installation is built for Linux x86_64 and CPython 3.10. Use the matching wheel with the Leaderboard environment. Installing leaderboard after finishing setting up the carla_ws, including fetching the dependencies and docker build. 

```bash
chmod +x scripts/export.sh 
./scripts/export.sh # Export necessary environment variables inside bashrc so leaderboard could find the components to launch

conda create -n carla_1.0 python=3.10 pip -y
conda activate carla_1.0

cd "$LEADERBOARD_ROOT" # Go to the root of your cloned leaderboard (should be ~/leaderboard)
python -m pip install -r requirements.txt
python -m pip install ".../CARLA_1.0/.../PythonAPI/carla/dist/carla-1.0-cp310-cp310-linux_x86_64.whl" # Go to where you download the Carla Simulator
```

The CARLA wheel command installs the CARLA Python API extension. `PYTHONPATH` also exposes the CARLA `agents` package, this Leaderboard checkout, and the matching `ue58-dev` ScenarioRunner checkout. The `~/carla_ws/dependencies/scenario_runner` checkout may be on a different branch; do not substitute it without confirming compatibility.

The repository's `requirements.txt` contains the Leaderboard Python dependencies, including PyYAML for the validator configuration. These commands create/install the host Python environment only; they do not install Docker, build ROS packages, or start CARLA.

## Hermes Scenario Workflow

Hermes is used here as a scenario authoring and repair agent, not as the driving agent and not as the CARLA server. Follow the repository's [Hermes project instructions](.hermes.md), which adapt the installed CARLA skills to this host-Conda/Docker workflow and skip their setup, interactive placement, and simulator-launch steps.

1. Point Hermes at the source route file and route ID, and describe the requested scenario behavior and constraints.
2. Hermes inspects the route and available Leaderboard scenario classes, then writes a sibling XML under `data/generated/`. The source route is preserved; scenarios are added to the selected route ID in the generated copy.
3. Validate that generated route against the configured plugins:

   ```bash
   ./scripts/validation.sh data/generated/<route-file>.xml <route-id>
   ```

   The route-geometry plugin connects to the already-running CARLA server and requires the route's town to be active. It does not start CARLA, load a map, tick the world, spawn actors, or run the evaluator.

4. If validation fails, give the complete validator output to Hermes. Hermes uses the diagnostics to revise the generated copy and reruns validation. The configured workflow allows up to five generation/validation passes; it must stop and report remaining errors if the route still fails.
5. Only after every enabled plugin passes should the route be used in a Leaderboard evaluation.

This repair loop is Hermes reflecting on validator feedback. The validator itself does not modify the XML or train/update the Hermes model. A passing validator confirms the checks it implements; it does not certify scenario behavior or guarantee that a driving agent will finish the route.

## Run Leaderboard

`run_leaderboard.sh` is the direct host-side entry point. Before starting, make sure the CARLA server is already running with the town needed by the selected route. From a terminal where the CARLA and ScenarioRunner paths are configured:

```bash
conda activate carla_1.0

cd "$LEADERBOARD_ROOT" # Go to the root of your leaderboard
./run_leaderboard.sh
```

Review `run_leaderboard.sh` before each run. Its `ROUTES`, `ROUTES_SUBSET`, and `TEAM_AGENT` values select the route XML, route ID/subset, and driving agent. The current script selects `leaderboard/autoagents/human_agent.py`, which uses keyboard input. To use ROS 2 control, select a concrete ROS-compatible agent that implements the Leaderboard agent entry point; the `ros2_agent.py` in this checkout is an interface/base, not by itself a configured driving policy.

When evaluation begins, Leaderboard spawns the ego vehicle at the first route waypoint, builds the route, loads the route's configured scenarios, runs the selected driving agent, and records the evaluation results. The scenario classes and route XML define the Leaderboard scenarios; `carla_ws` does not replace the evaluator.

### Use the `carla_ws` Shortcuts

If the aliases from `~/carla_ws/scripts/alias.sh` are installed and loaded in the shell, `up-carla` is the convenience deployment path. It runs `~/carla_ws/scripts/deploy.sh`, which creates the tmux layout, launches the Leaderboard panel in host Conda, and starts the configured Docker services such as G29 control, monitoring, CARLA extensions, Foxglove, and rosbag recording.

```bash
up-carla
```

The Compose file also defines a `traffic` service for NPC traffic, but the current `deploy.sh` does not start that service automatically. Start traffic separately from `~/carla_ws` if it is needed and the CARLA server is ready.

```bash
cd ~/carla_ws
docker compose up traffic
```

`down-carla` runs `docker compose down`; it stops the Compose services, but does not necessarily terminate the host-side Leaderboard process in tmux. Stop the Leaderboard pane/session separately when required. For debugging, validation, or a run without the combined deployment, use `./run_leaderboard.sh` directly as described above.

G29 commands are published by `carla_ws` to the ROS 2 vehicle-control topic. They control the evaluated ego only when the selected Leaderboard agent is connected to that ROS 2 control path. With the current `HumanAgent`, use its keyboard controls instead. Do not run multiple manual controllers for the same ego role at once.

## CARLA Validator

The validator lives in `carla_validator/`. It validates one route selected by XML path and route ID. `carla_config.yaml` selects and configures plugins; each enabled plugin receives the same `ValidationContext` and returns a `ValidationOutput`. The controller runs all enabled plugins and aggregates their results.

The default plugins are:

| Plugin | Checks |
| --- | --- |
| `weather` | Weather entry attributes, numeric values, route percentages, and ordering. |
| `scenario_parameters` | Scenario type schemas, required and optional parameter names, XML attribute shapes, numeric constraints, and duplicate scenario names. |
| `scenario_at_route` | Connects to the active CARLA map, interpolates the selected route, checks scenario-class registration, and calls Leaderboard's `RouteParser.is_scenario_at_route`. Rejections include nearest-route distance, elevation and yaw errors, and the corresponding route transform. |
| `passthrough` | Example plugin that always passes; disabled in the default configuration. |

Run the test suite without connecting to CARLA:

```bash
conda run -n carla_1.0 python -m unittest discover -s carla_validator/tests -p 'test_*.py' -v
```

The integrated script sets the CARLA/ScenarioRunner import paths and runs the validator in the existing Conda environment:

```bash
./scripts/validation.sh data/generated/<route-file>.xml <route-id>
```

Exit code `0` and `VALIDATION PASSED` mean the enabled checks passed. Exit code `1` means a plugin rejected the route; exit code `2` means input/configuration setup failed. The `scenario_at_route` plugin requires an already-running CARLA server on the configured host/port and the route's town to be active. It never loads or changes the map. A server connection failure is a validation failure, not permission to start CARLA automatically.

### Write a Validation Plugin

1. Add a module under `carla_validator/src/plugins/` and subclass `ValidationBase`.
2. Implement `validate(context: ValidationContext) -> ValidationOutput`. Use `context.route_file`, `context.route_id`, and `context.route_element` to inspect the selected route; use `self.cfg` for plugin-specific YAML settings.
3. Return a result with a boolean, a list of human-readable error strings, and optional structured details:

   ```python
   from .validation_base import ValidationBase
   from .validation_helper import ValidationContext, ValidationOutput


   class ExampleValidation(ValidationBase):
	   def validate(self, context: ValidationContext) -> ValidationOutput:
		   errors = []
		   if context.route_element.get("town") is None:
			   errors.append("route has no town")
		   return ValidationOutput(
			   valid=not errors,
			   errors=errors,
			   details={"route_id": context.route_id},
		   )
   ```

4. Import the class in both import branches in `carla_validator/src/carla_validator.py` (module and direct-script execution), then add its name and class to `PLUGIN_REGISTRY`.
5. Add a `validation.<plugin_name>` block to `carla_config.yaml` with `enabled: true` and its settings. Disabled plugins remain registered but are skipped.
6. Add offline tests under `carla_validator/tests/`. Keep tests independent of CARLA where possible; use a focused integration check against the active map only when that plugin needs live route geometry.

## References

- [Hermes Agent repository](https://github.com/NousResearch/hermes-agent)
- [Hermes Agent documentation](https://hermes-agent.nousresearch.com/docs/)
- [CARLA 1.0 release](https://github.com/carla-simulator/carla/releases/tag/1.0)
- [CARLA 1.0 documentation](https://carla-ue5.readthedocs.io/en/latest/)
- [CARLA Leaderboard documentation](https://carla-ue5.readthedocs.io/en/latest/leaderboard/)
- [CARLA ROS 2 documentation](https://carla-ue5.readthedocs.io/en/latest/ros2_native/)