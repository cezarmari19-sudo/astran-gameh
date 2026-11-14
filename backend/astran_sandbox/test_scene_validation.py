"""python -m unittest astran_sandbox.test_scene_validation"""
import unittest
from astran_sandbox.scene_validation import clean_scene, MAX_SCENE_OBJECTS


class SceneValidationTests(unittest.TestCase):
    def test_legit_scene_roundtrips(self):
        s = {"objects": [{"id": "o1", "type": "cube", "x": 1, "y": 2, "z": 3, "color": "#ff0000", "scale": 2,
                          "rx": 10, "visible": False, "solid": True, "name": "Box"},
                         {"id": "sp", "type": "spawn", "spawnKind": "checkpoint", "initial": True, "enabled": True}],
             "sky": "#112233", "ground": "#445566", "groundWidth": 100, "groundDepth": 50}
        c = clean_scene(s)
        self.assertEqual(c["objects"][0]["x"], 1.0)
        self.assertEqual(c["objects"][0]["color"], "#ff0000")
        self.assertEqual(c["objects"][1]["spawnKind"], "checkpoint")
        self.assertEqual((c["groundWidth"], c["groundDepth"]), (100.0, 50.0))

    def test_abusive_structure_rejected(self):
        for bad in ([], "x", None, {"objects": "no"}, {"objects": [{"id": "a", "type": "cube"}] * (MAX_SCENE_OBJECTS + 1)}):
            with self.assertRaises(ValueError):
                clean_scene(bad)

    def test_dirty_values_are_cleaned_not_rejected(self):
        c = clean_scene({"objects": [
            {"id": "a", "type": "cube", "x": float("nan"), "y": 1e30, "color": "javascript:1", "scale": -5, "evil": {"deep": 1}},
            {"id": "bad id!", "type": "cube"}, {"type": "cube"}, "str", None],
            "sky": "red", "groundWidth": 10 ** 9, "extra": 1})
        self.assertEqual(len(c["objects"]), 1)
        o = c["objects"][0]
        self.assertEqual((o["x"], o["y"], o["color"], o["scale"]), (0.0, 1_000_000.0, "#a3a3a3", 0.0))
        self.assertNotIn("evil", o)
        self.assertEqual(c["sky"], "#0F1012")
        self.assertEqual(c["groundWidth"], 2000.0)
        self.assertNotIn("extra", c)

    def test_empty_scene_ok(self):
        self.assertEqual(clean_scene({})["objects"], [])


    def test_shop_model_object_keeps_real_asset_id(self):
        c = clean_scene({"objects": [
            {"id": "m1", "type": "model", "assetId": "itm_abc123", "x": 2, "y": 0, "z": -3, "ry": 90,
             "scale": 1.5, "ex": 2, "ey": 3, "ez": 1, "color": "#ffffff", "evil": 1},
        ]})
        o = c["objects"][0]
        self.assertEqual((o["type"], o["assetId"]), ("model", "itm_abc123"))
        self.assertEqual((o["x"], o["z"], o["ry"], o["scale"]), (2.0, -3.0, 90.0, 1.5))
        self.assertEqual((o["ex"], o["ey"], o["ez"]), (2.0, 3.0, 1.0))
        self.assertNotIn("evil", o)

    def test_shop_model_without_valid_asset_id_is_dropped(self):
        c = clean_scene({"objects": [
            {"id": "m1", "type": "model"},
            {"id": "m2", "type": "model", "assetId": "bad id!"},
            {"id": "m3", "type": "model", "assetId": 5},
            {"id": "ok", "type": "cube"}]})
        self.assertEqual([o["id"] for o in c["objects"]], ["ok"])

    def test_asset_id_not_kept_on_other_types(self):
        c = clean_scene({"objects": [{"id": "a", "type": "cube", "assetId": "itm_x"}]})
        self.assertNotIn("assetId", c["objects"][0])

if __name__ == "__main__":
    unittest.main()