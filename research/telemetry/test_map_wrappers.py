"""MapData / virtual / map guards on invented memory only."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'runtime'))
from runtime_decode import checked_map_backing


class Heap:
    def __init__(self):
        self.types = {10: ('hxbit.MapData', 11), 20: ('virtual', 15),
                      30: ('haxe.ds.IntMap', 11), 40: ('haxe.ds.StringMap', 11),
                      50: ('ForeignObject', 11)}
        self.fields = {(10, 'map'): 20, (30, 'h'): 3000, (40, 'h'): 4000}
        self.pointers = {28: 30}

    def object_type(self, address):
        name, kind = self.types[address]
        return SimpleNamespace(name=name, kind=kind)

    def field(self, address, name):
        return self.fields[address, name]

    def ptr(self, address):
        return self.pointers[address]


class CheckedMapTests(unittest.TestCase):
    def test_direct_concrete_and_nested_virtual_backings(self):
        h = Heap()
        with checked_map_backing(h, 40, 'haxe.ds.StringMap') as actual:
            self.assertEqual(actual, 40)
        with checked_map_backing(h, 10, 'haxe.ds.IntMap') as actual:
            self.assertEqual(actual, 30)
        h.fields[10, 'map'] = 40
        with checked_map_backing(h, 10, 'haxe.ds.StringMap') as actual:
            self.assertEqual(actual, 40)

    def test_null_wrappers_or_storage_do_not_become_empty_maps(self):
        for mutation in ('root', 'map', 'virtual', 'storage'):
            with self.subTest(mutation=mutation):
                h = Heap()
                root = 10
                if mutation == 'root': root = 0
                elif mutation == 'map': h.fields[10, 'map'] = 0
                elif mutation == 'virtual': h.pointers[28] = 0
                else: h.fields[30, 'h'] = 0
                with self.assertRaises(ValueError):
                    with checked_map_backing(h, root, 'haxe.ds.IntMap'):
                        self.fail('Null backing cannot be yielded')

    def test_wrong_map_or_foreign_object_type_cannot_be_unwrapped(self):
        for target in (40, 50):
            with self.subTest(target=target):
                h = Heap(); h.pointers[28] = target
                with self.assertRaises(ValueError):
                    with checked_map_backing(h, 10, 'haxe.ds.IntMap'):
                        self.fail('Wrong backing type cannot be yielded')

    def test_cycles_and_wrapper_depth_are_rejected(self):
        h = Heap(); h.pointers[28] = 10
        with self.assertRaises(ValueError):
            with checked_map_backing(h, 10, 'haxe.ds.IntMap'):
                pass
        with self.assertRaises(ValueError):
            with checked_map_backing(Heap(), 10, 'haxe.ds.IntMap', limit=2):
                pass

    def test_wrapper_storage_or_type_mutation_rejected_after_read(self):
        for mutation in ('map', 'virtual', 'storage', 'type'):
            with self.subTest(mutation=mutation):
                h = Heap()
                with self.assertRaises(ValueError):
                    with checked_map_backing(h, 10, 'haxe.ds.IntMap'):
                        if mutation == 'map': h.fields[10, 'map'] = 40
                        elif mutation == 'virtual': h.pointers[28] = 40
                        elif mutation == 'storage': h.fields[30, 'h'] = 4000
                        else: h.types[30] = ('ForeignObject', 11)


if __name__ == '__main__':
    unittest.main()
