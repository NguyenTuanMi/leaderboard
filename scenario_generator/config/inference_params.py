import argparse
import os

DEFAULT_OUTPUT = "data/routes_devtest.xml"
DEFAULT_ENDPOINT = "http://localhost:9000/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
MAX_SCENARIOS = 10
DEFAULT_CARLA_HOST = "localhost"
DEFAULT_CARLA_PORT = 2000
DEFAULT_MAX_COMPLETION_TOKENS = 4096
DEFAULT_MIN_SPACING = 10.0      # metres along the route between two triggers
DEFAULT_SAMPLE_STEP = 20.0      # metres between route points listed in the prompt
# Same thresholds as leaderboard/utils/route_parser.py
DIST_THRESHOLD = 2.0
ANGLE_THRESHOLD = 10.0

PARAMETER_ENUMS = {"direction": {"left", "right"}}
POSITIVE_PARAMETERS = {
    "distance", "speed", "flow_speed", "frequency", "bicycle_speed",
    "bicycle_drive_distance", "front_vehicle_distance", "behind_vehicle_distance",
}

SCENARIO_PARAMETERS: dict[str, tuple[str, ...]] = {
    "SignalizedJunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "SignalizedJunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "OppositeVehicleRunningRedLight": ("direction",),
    "NonSignalizedJunctionLeftTurn": ("flow_speed", "source_dist_interval"),
    "NonSignalizedJunctionRightTurn": ("flow_speed", "source_dist_interval"),
    "OppositeVehicleTakingPriority": ("direction",),
    "DynamicObjectCrossing": ("distance", "direction", "blocker_model", "crossing_angle"),
    "ParkingCrossingPedestrian": ("distance", "direction", "crossing_angle"),
    "PedestrianCrossing": (),
    "VehicleTurningRoute": (),
    "VehicleTurningRoutePedestrian": (),
    "BlockedIntersection": (),
    "EnterActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "EnterActorFlowV2": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InterurbanActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "InterurbanAdvancedActorFlow": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "HighwayExit": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "MergerIntoSlowTraffic": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "MergerIntoSlowTrafficV2": ("start_actor_flow", "end_actor_flow", "flow_speed", "source_dist_interval"),
    "CrossingBicycleFlow": ("start_actor_flow", "flow_speed", "source_dist_interval"),
    "ConstructionObstacle": ("distance", "direction", "speed"),
    "ConstructionObstacleTwoWays": ("distance", "frequency"),
    "Accident": ("distance", "direction", "speed"),
    "AccidentTwoWays": ("distance", "frequency"),
    "ParkedObstacle": ("distance", "direction", "speed"),
    "ParkedObstacleTwoWays": ("distance", "frequency"),
    "VehicleOpensDoorTwoWays": ("distance", "frequency"),
    "HazardAtSideLane": ("distance", "speed", "bicycle_drive_distance", "bicycle_speed"),
    "HazardAtSideLaneTwoWays": ("distance", "frequency", "bicycle_drive_distance", "bicycle_speed"),
    "InvadingTurn": ("distance", "offset"),
    "HighwayCutIn": ("other_actor_location",),
    "ParkingCutIn": ("direction",),
    "StaticCutIn": ("distance", "direction"),
    "ControlLoss": (),
    "HardBreakRoute": (),
    "ParkingExit": ("direction", "front_vehicle_distance", "behind_vehicle_distance"),
    "YieldToEmergencyVehicle": ("distance",),
    "BackgroundActivityParametrizer": (
        "num_front_vehicles", "num_back_vehicles", "road_spawn_dist",
        "opposite_source_dist", "opposite_max_actors", "opposite_spawn_dist",
        "opposite_active", "junction_source_dist", "junction_max_actors",
        "junction_spawn_dist", "junction_source_perc",
    ),
    "PriorityAtJunction": (),
}

WEATHER_ATTRIBUTES = {
    "cloudiness", "precipitation", "precipitation_deposits", "wetness",
    "wind_intensity", "sun_azimuth_angle", "sun_altitude_angle", "fog_density",
    "fog_distance", "wetness", "scatter_intensity", "mie_scattering_scale",
    "rayleigh_scattering_scale", "dust_storm", "fog_falloff",
}

SYSTEM_PROMPT = """You generate CARLA leaderboard route XML data.
The caller supplies the dense route that Leaderboard will actually drive: a list
of route points, each with an integer index, x, y, z, yaw and the distance
travelled along the route in metres (s). The list is downsampled, but every
integer index from 0 to the last index is a valid choice. Never invent
coordinates: you choose WHERE a scenario happens by giving a route_index, and the
caller derives the exact trigger_point (x, y, z, yaw) from that route point.
Return only one JSON object with this shape:
{
  "routes": [
    {
      "town": "the template town",
      "weather": [
        {"route_percentage": 0, "cloudiness": 5, "sun_altitude_angle": 70}
      ],
      "scenarios": [
        {
          "type": "ControlLoss",
          "route_index": 120,
          "parameters": {}
        }
      ]
    }
  ]
}
Rules:
1. Use only scenario types from the supplied allowlist and the exact parameter
   names from the scenario schema. Intervals are objects with numeric "from"
   and "to". Generate no more than the requested route and scenario counts.
2. Every scenario MUST contain an integer route_index within the valid range.
   Do not output x, y, z, yaw, trigger_point, trigger_reference, waypoint_index
   or yaw_offset.
3. Scenarios on one route must be far apart along the route (at least the
   supplied minimum spacing in metres, measured with s) and must not repeat
   the same route_index. List them in increasing route_index order.
4. If validation feedback is supplied, correct every listed scenario and
   return the complete corrected JSON object.

For reference only, this is how a finished Leaderboard route looks in XML. The
caller writes the XML; your response must still be JSON in the shape above.
Each scenario's trigger_point is a point of the route, and the scenario's
parameters are child elements:

<route id="0" town="Town12">
  <weathers>
    <weather route_percentage="0" cloudiness="5.0"
             precipitation="0.0" sun_altitude_angle="90.0"/>
    <weather route_percentage="100" cloudiness="5.0"
             precipitation="0.0" sun_altitude_angle="15.0"/>
  </weathers>
  <waypoints>
    <position x="983.5" y="5382.2" z="371"/>
    <position x="824.2" y="5575.3" z="371"/>
  </waypoints>
  <scenarios>
    <scenario name="ParkingExit_1" type="ParkingExit">
      <trigger_point x="983.5" y="5382.2" z="371" yaw="90"/>
      <direction value="right"/>
      <front_vehicle_distance value="9"/>
      <behind_vehicle_distance value="9"/>
    </scenario>
    <scenario name="Accident_1" type="Accident">
      <trigger_point x="427.2" y="6086.8" z="359" yaw="144"/>
    </scenario>
  </scenarios>
</route>
"""

INTERACTIVE_HELP = """Type a scenario description and press Enter.
  /count N   number of route variants per request (now {count})
  /help      show this help
  /quit      leave (Ctrl-D also works)"""

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate CARLA leaderboard routes from a natural-language scenario description.")
    parser.add_argument("--description",
                        help="Natural-language scenario description. Omit it to start an interactive prompt")
    parser.add_argument("--backend", choices=("http", "llm"), default=os.getenv("SCENARIO_LLM_BACKEND", "http"),
                        help="http: OpenAI-compatible endpoint; llm: the `llm` library (pip install llm)")
    parser.add_argument("--output", default=os.getenv("SCENARIO_OUTPUT", DEFAULT_OUTPUT))
    parser.add_argument("--template-route", required=True, help="Existing route id used for valid waypoints")
    parser.add_argument("--count", type=int, default=1, help="Number of route variants to append")
    parser.add_argument("--max-scenarios", type=int, default=MAX_SCENARIOS)
    parser.add_argument("--endpoint", default=os.getenv("SCENARIO_LLM_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--model", default=os.getenv("SCENARIO_LLM_MODEL", DEFAULT_MODEL),
                        help="Model name; with --backend llm, any name or alias from `llm models`")
    parser.add_argument("--api-key", default=os.getenv("SCENARIO_LLM_API_KEY"))
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--max-completion-tokens", type=int, default=DEFAULT_MAX_COMPLETION_TOKENS)
    parser.add_argument("--carla-host", default=os.getenv("CARLA_HOST", DEFAULT_CARLA_HOST))
    parser.add_argument("--carla-port", type=int, default=int(os.getenv("CARLA_PORT", DEFAULT_CARLA_PORT)))
    parser.add_argument("--carla-timeout", type=float, default=120.0,
                        help="Seconds to wait for CARLA (loading a large town can be slow)")
    parser.add_argument("--min-spacing", type=float, default=DEFAULT_MIN_SPACING,
                        help="Minimum metres along the route between two scenario triggers")
    parser.add_argument("--sample-step", type=float, default=DEFAULT_SAMPLE_STEP,
                        help="Metres between route points listed to the model")
    parser.add_argument("--max-requests", type=int, default=3,
                        help="Maximum model requests, including validation retries")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Skip the final confirmation")
    return parser.parse_args()