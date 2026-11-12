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
`data/routes_devtest.xml`. It copies waypoints from an existing route, so the
local or remote model does not have to invent map coordinates:

```bash
python3 scenario_generator/scenario_generator.py \
  --template-route 0 \
  --description "Add a pedestrian crossing and a control-loss event near the middle of the route"
```

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
weather, invalid waypoint indices, and malformed model responses before
modifying the XML. It uses only Python's standard library.