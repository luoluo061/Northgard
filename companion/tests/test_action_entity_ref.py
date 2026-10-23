"""Regression: a movement target's kind need not be a Haxe String."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'research/telemetry'))
from action_capture import ActionProbe
from runtime_types import LayoutError


class FakeTypes:
    def __init__(self):
        self.rows = {100: ('ent.Zone', {'__uid': 8, 'id': 22, 'kind': {'kind': 'Land', 'args': []}}),
                     200: ('ent.Unit', {'__uid': 9, 'zone': 100, 'kind': 'Villager'}),
                     300: ('ent.Prop', {'__uid': 10, 'zone': 0})}

    def object_type(self, ptr):
        return SimpleNamespace(name=self.rows[ptr][0], address=ptr)

    def fields(self, ptr):
        return [SimpleNamespace(name=k) for k in self.rows[ptr][1]]

    def field(self, ptr, key):
        return self.rows[ptr][1][key]


class TargetKindTests(unittest.TestCase):
    def probe(self):
        probe = ActionProbe.__new__(ActionProbe)
        probe.h = FakeTypes()
        probe.value = lambda ptr, key, depth=0: probe.h.field(ptr, key)
        return probe

    def test_zone_enum_target_keeps_structure(self):
        value = self.probe().entity_ref(100)
        self.assertEqual(value, {'entity_type': 'ent.Zone', 'uid': 8, 'zone_id': 22,
                                 'kind': {'kind': 'Land', 'args': []}})

    def test_unit_string_target_and_zone_are_preserved(self):
        value = self.probe().entity_ref(200)
        self.assertEqual(value['kind'], 'Villager')
        self.assertEqual(value['zone_id'], 22)

    def test_optional_kind_and_null_target(self):
        probe = self.probe()
        self.assertIsNone(probe.entity_ref(0))
        self.assertEqual(probe.entity_ref(300), {'entity_type': 'ent.Prop', 'uid': 10, 'zone_id': None})

    def test_recursive_reference_depth_fails_explicitly(self):
        with self.assertRaises(LayoutError):
            self.probe().entity_ref(100, depth=6)


if __name__ == '__main__':
    unittest.main()
