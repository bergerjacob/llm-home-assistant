#!/usr/bin/env python3
"""Whitelist filter testing."""
import sys, os, json, argparse, re

sys.path.insert(0, "/home/llm-ha/homeassistant/custom_components/llm_home_assistant")

# Stubs
import types
for n in ["homeassistant.core", "homeassistant.const", "homeassistant.helpers",
          "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.template",
          "homeassistant.helpers.service", "homeassistant.helpers.config_validation",
          "homeassistant.helpers.discovery", "homeassistant.components"]:
    m = types.ModuleType(n)
    sys.modules[n] = m
sys.modules["homeassistant.core"].HomeAssistant = type("HomeAssistant", (), {})
sys.modules["homeassistant.const"].ATTR_ENTITY_ID = "entity_id"
class T:
    def __init__(self,*a,**k): pass
    def async_render(self,*a,**k): return "{}"
sys.modules["homeassistant.helpers"].Template = T
sys.modules["homeassistant.helpers.service"].async_get_all_descriptions = lambda: {}
sys.modules["homeassistant.helpers.config_validation"].string = str
for n in ["homeassistant.components.input_text", "homeassistant.components.input_select",
          "homeassistant.components.http", "homeassistant.components.lovelace",
          "homeassistant.components.lovelace.const", "homeassistant.components.frontend"]:
    sys.modules[n] = types.ModuleType(n)
sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda *a,**k: None
vol = types.ModuleType("voluptuous")
sys.modules["voluptuous"] = vol
vol.Schema = lambda *a,**k: None
vol.Required = lambda *a,**k: a[0] if a else None
vol.Optional = lambda *a,**k: a[0] if a else None
vol.UNDEFINED = object()
vol_sb = types.ModuleType("voluptuous.schema_builder")
sys.modules["voluptuous.schema_builder"] = vol_sb
vol_sb.Marker = type("Marker", (), {})
openai = types.ModuleType("openai")
sys.modules["openai"] = openai
openai.OpenAI = type("OpenAI", (), {})
pyd = types.ModuleType("pydantic")
sys.modules["pydantic"] = pyd
class BM:
    def __init_subclass__(cls,**kw): pass
    @classmethod
    def model_validate(cls,d): return cls()
    @classmethod
    def model_validate_json(cls,s): return cls()
    def model_dump(self): return {}
pyd.BaseModel = BM
pyd.Field = lambda **k: None


# Stub fetch_entity_areas to avoid template errors
import types
original_template = sys.modules.get("homeassistant.helpers", types.ModuleType("homeassistant.helpers")).Template
class FakeTemplate:
    def __init__(self, *args, **kwargs): pass
    def async_render(self, *args, **kwargs): return '{"light.kitchen": "Kitchen", "light.living_room": "Living Room"}'
sys.modules["homeassistant.helpers"].Template = FakeTemplate

from device_info import build_compact_context

# Patch fetch_entity_areas to avoid template errors
def _fake_fetch_entity_areas(hass):
    return {}
import device_info
device_info.fetch_entity_areas = _fake_fetch_entity_areas
device_info._cfg_hash = lambda x: "fake"

class States:
    def __init__(self, states): self._states = states
    def async_all(self): return self._states

class HA:
    def __init__(self):
        self._states = [
            State("light.kitchen", "on", {"brightness": 200, "friendly_name": "Kitchen Light"}),
            State("light.living_room", "on", {"brightness": 150, "friendly_name": "Living Room Light"}),
            State("light.bedroom", "off", {"brightness": 100, "friendly_name": "Bedroom Light"}),
            State("light.dining_room", "off", {"brightness": 180, "friendly_name": "Dining Room Light"}),
            State("light.garage", "off", {"brightness": 50, "friendly_name": "Garage Light"}),
            State("switch.plasma", "on", {"friendly_name": "Plasma TV"}),
            State("switch.soundbar", "off", {"friendly_name": "Soundbar"}),
            State("switch.desk_lamp", "on", {"friendly_name": "Desk Lamp"}),
            State("switch.camera_power", "on", {"friendly_name": "Camera Power"}),
            State("sensor.temperature_living", "22.5", {"unit_of_measurement": "C", "friendly_name": "Living Temp"}),
            State("sensor.humidity_bedroom", "45", {"unit_of_measurement": "%", "friendly_name": "Bedroom Humidity"}),
            State("sensor.power_kitchen", "150", {"unit_of_measurement": "W", "friendly_name": "Kitchen Power"}),
            State("sensor.motion_garage", "active", {"device_class": "motion", "friendly_name": "Garage Motion"}),
            State("climate.living_room", "heat", {"current_temperature": 21, "temperature": 22, "friendly_name": "Living AC"}),
            State("cover.garage_door", "closed", {"current_position": 0, "friendly_name": "Garage Door"}),
            State("cover.living_window", "open", {"current_position": 50, "friendly_name": "Living Window"}),
            State("media_player.living_sonos", "playing", {"volume_level": 0.3, "friendly_name": "Living Sonos"}),
            State("lock.front_door", "locked", {"friendly_name": "Front Door Lock"}),
            State("sun.sun", "above_horizon", {}),
            State("zone.home", "zoning", {}),
            State("device_tracker.phone1", "home", {})]
        self.states = States(self._states)
    def async_all(self): return self._states

class State:
    def __init__(self, eid, state, attrs):
        self.entity_id = eid
        self.state = state
        self.attributes = attrs or {}
    def as_dict(self): return {"entity_id": self.entity_id, "state": self.state, "attributes": self.attributes}

def load_ha_config():
    path = "/home/llm-ha/homeassistant/configuration.yaml"
    if not os.path.exists(path):
        print("No config")
        return {"domains": [], "services": [], "entities": []}
    print(f"Loading: {path}")
    with open(path) as f:
        content = f.read()
    match = re.search(r'llm_home_assistant:\s*\n((?:  .*\n)*)', content)
    if not match:
        return {"domains": [], "services": [], "entities": []}
    section = match.group(1)
    cfg = {"domains": [], "services": [], "entities": []}
    m = re.search(r'domains:\s*\[([^\]]*)\]', section)
    if m:
        cfg["domains"] = [x.strip().strip('"').strip("'") for x in m.group(1).split(',') if x.strip()]
    m = re.search(r'services:\s*\n((?:      - .*\n)*)', section)
    if m:
        cfg["services"] = [x.strip().strip('- ').strip('"').strip("'") for x in m.group(1).strip().split('\n') if x.strip()]
    m = re.search(r'entities:\s*\[([^\]]*)\]', section)
    if m:
        cfg["entities"] = [x.strip().strip('"').strip("'") for x in m.group(1).split(',') if x.strip()]
    print(f"  {len(cfg['domains'])} domains, {len(cfg['services'])} services")
    return cfg

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", "-p", required=True)
    parser.add_argument("--load-allowcfg", action="store_true")
    parser.add_argument("--cfg", type=str)
    parser.add_argument("--cfg-key", type=str)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    
    cfg = {"domains": [], "services": [], "entities": []}
    if args.load_allowcfg:
        cfg = load_ha_config()
    elif args.cfg:
        cfg = json.loads(args.cfg)
    
    if args.cfg_key:
        print(f"{args.cfg_key}: {cfg.get(args.cfg_key)}")
        return
    
    hass = HA()
    ctx_str = build_compact_context(hass, cfg)
    ctx = json.loads(ctx_str)
    
    print("=" * 60)
    print(f"  PROMPT: {args.prompt}")
    print(f"  FILTERED: {len(ctx['entities'])}/{21} entities")
    print(f"  SIZE: {len(ctx_str)} chars")
    print("=" * 60)
    
    if ctx.get("domains"):
        print(f"  Domains: {cfg['domains']}")
    if ctx.get("services"):
        print(f"  Services: {cfg['services']}")
    print()
    
    for e in ctx["entities"]:
        print(f"  {e['e']}: {e['s']} ({e['d']})")
    
    if args.verbose:
        print("\nFull JSON:")
        print(json.dumps(ctx, indent=2))

if __name__ == "__main__":
    main()
