#!/usr/bin/env python3
"""Test runner that installs HA stubs before pytest collection.

Usage:  python3 tests/run_tests.py [-v] [pytest args...]

This script MUST be run before pytest discovers any modules in the repo,
because the repo root __init__.py imports homeassistant.* at module level.
"""
import sys
import types

# ---- Install stubs into sys.modules FIRST ----

def _make(name: str) -> types.ModuleType:
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m


# homeassistant core
_make("homeassistant")
ha_core = _make("homeassistant.core")
ha_core.HomeAssistant = type("HomeAssistant", (), {})
ha_core.ServiceCall = type("ServiceCall", (), {})

ha_const = _make("homeassistant.const")
ha_const.ATTR_ENTITY_ID = "entity_id"

# homeassistant.helpers.*
_make("homeassistant.helpers")
ha_aio = _make("homeassistant.helpers.aiohttp_client")
ha_aio.async_get_clientsession = lambda *a, **kw: None

ha_tmpl = _make("homeassistant.helpers.template")
ha_tmpl.Template = type("Template", (), {"async_render": lambda *a, **kw: "{}"})

ha_svc = _make("homeassistant.helpers.service")
ha_svc.async_get_all_descriptions = lambda *a, **kw: {}
ha_cv = _make("homeassistant.helpers.config_validation")
ha_cv.string = str
_make("homeassistant.helpers.discovery")

# homeassistant.components.*
_make("homeassistant.components")
_make("homeassistant.components.input_text")
_make("homeassistant.components.input_select")
_make("homeassistant.components.http")
_make("homeassistant.components.lovelace")
_make("homeassistant.components.lovelace.const")
_make("homeassistant.components.frontend")

# voluptuous
vol = _make("voluptuous")
vol.Schema = lambda *a, **kw: None
vol.Required = lambda *a, **kw: a[0] if a else None
vol.Optional = lambda *a, **kw: a[0] if a else None
vol.UNDEFINED = object()
vol_sb = _make("voluptuous.schema_builder")
vol_sb.Marker = type("Marker", (), {})

# openai / aiohttp / pydantic
openai_mod = _make("openai")
openai_mod.OpenAI = type("OpenAI", (), {})
_make("aiohttp")

pydantic = _make("pydantic")

class _FakeBaseModel:
    def __init_subclass__(cls, **kw): pass
    @classmethod
    def model_validate_json(cls, s): return cls()
    def model_dump(self): return {}

pydantic.BaseModel = _FakeBaseModel
pydantic.Field = lambda **kw: None

# ---- Now it's safe to import pytest and run ----

import os
import pytest

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tests_dir = os.path.join(repo_root, "tests")

sys.exit(pytest.main([tests_dir, "--rootdir", tests_dir, "-v"] + sys.argv[1:]))
