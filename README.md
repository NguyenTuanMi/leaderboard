The main goal of the CARLA Autonomous Driving Leaderboard is to evaluate the driving proficiency of autonomous agents in realistic traffic situations. The leaderboard serves as an open platform for the community to perform fair and reproducible evaluations, simplifying the comparison between different approaches.

Autonomous agents have to drive through a set of predefined routes. For each route, agents are initialized at a starting point and have to drive to a destination point. The agents will be provided with a description of the route. Routes will happen in a variety of areas, including freeways, urban scenes, and residential districts.

Agents will face multiple traffic situations based in the NHTSA typology, such as:

* Lane merging
* Lane changing
* Negotiations at traffic intersections
* Negotiations at roundabouts
* Handling traffic lights and traffic signs
* Coping with pedestrians, cyclists and other elements

The user can change the weather of the simulation, allowing the evaluation of the agent in a variety of weather conditions, including daylight scenes, sunset, rain, fog, and night, among others.

More information can be found [here](https://leaderboard.carla.org/)

## LLM scenario route generator

`scenario_generator/scenario_generator.py` can turn a natural-language
description into one or more validated route entries in
`data/routes_devtest.xml`. It copies waypoints from an existing route and needs
a running CARLA server (same Python environment as `run_leaderboard.sh`, so
`carla`, `scenario_runner` and this repo are importable). The generator loads the
route's town and builds the same dense 1 m route that Leaderboard drives. The
model only picks a `route_index` for each scenario; the trigger point (x, y, z,
yaw) is taken from that route point and checked with Leaderboard's own
`RouteParser.is_scenario_at_route`. Validation errors (bad index, scenarios too
close along the route, invalid parameter values) are sent back to the model for a
retry:

```bash
python3 scenario_generator/scenario_generator.py \
  --template-route 0 \
  --description "Add a pedestrian crossing and a control-loss event near the middle of the route" \
  --carla-host localhost --carla-port 2000
```

Use `--min-spacing` (metres between triggers, default 10), `--sample-step`
(metres between route points shown to the model, default 20) and `--max-completion-tokens`
to tune it. Do not point it at a CARLA server that is running an evaluation, since
the town is reloaded.

The command previews the generated routes and requires `YES` before writing.
It creates a timestamped backup by default. Use `--count 2` for two variants,
`--max-scenarios 10` to cap generated scenarios, and `--yes` only for scripted
use. The model endpoint is OpenAI-compatible and can be configured without
changing code:

```bash
export SCENARIO_LLM_ENDPOINT=http://localhost:9000/v1
export SCENARIO_LLM_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
export SCENARIO_LLM_API_KEY=not-needed
```

For local endpoints (`localhost`, `127.0.0.1`, or `::1`), the generator
intentionally bypasses inherited `HTTP_PROXY`/`HTTPS_PROXY` settings so those
requests do not get sent to an external proxy. Remote model endpoints still
use the server's normal proxy environment.

The generator rejects unknown scenario types, missing parameters, invalid
weather, invalid trigger positions/headings, and malformed model responses
before modifying the XML. Trigger points are generated explicitly by the
model and checked against the template route geometry. Failed generations are
sent back to the model with validation feedback; `--max-requests` limits the
number of attempts. It uses only Python's standard library.