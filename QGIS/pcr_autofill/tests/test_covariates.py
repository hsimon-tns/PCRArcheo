import json
import os
import tempfile
import unittest

from pcr_autofill.covariates import DEFAULT_CONFIG, ConfigError, load_covariates


class DefaultConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_target = {c.target: c for c in load_covariates()}

    def columns(self, target):
        return [(c.name, c.kind) for c in self.by_target[target].columns]

    def test_default_file_ships_with_the_plugin(self):
        self.assertTrue(os.path.exists(DEFAULT_CONFIG))
        self.assertEqual(len(self.by_target), 8)

    def test_nearest_feature_gives_distance_plus_one_column_per_field(self):
        self.assertEqual(self.columns("dist_hydrographie_km"), [
            ("dist_hydrographie_km", "double"),
            ("dist_hydrographie_km__nature", "string"),
            ("dist_hydrographie_km__fictif", "string"),
            ("dist_hydrographie_km__position_par_rapport_au_sol", "string"),
        ])

    def test_point_query_with_one_field_uses_the_bare_target_name(self):
        self.assertEqual(self.columns("type_vegetation"), [("type_vegetation", "string")])
        self.assertEqual(self.columns("alea_argile"), [("alea_argile", "string")])

    def test_point_query_with_several_fields_suffixes_each_column(self):
        # None of the shipped intersection_point covariates has several fields any more
        # (the config was trimmed to attributes DescribeFeatureType actually exposes) :
        # exercise the column-naming logic directly instead of relying on that data.
        from pcr_autofill.covariates import Covariate
        multi = Covariate("l", "l", "P", "http://x", "intersection_point", "cov",
                          ("a", "b"), 5000, "", "")
        self.assertEqual([(c.name, c.kind) for c in multi.columns],
                         [("cov__a", "string"), ("cov__b", "string")])

    def test_count_query_gives_one_integer_column(self):
        self.assertEqual(self.columns("nb_mvt_terrain_5km"), [("nb_mvt_terrain_5km", "int")])
        self.assertEqual(self.by_target["nb_mvt_terrain_5km"].radius_m, 5000)

    def test_point_query_without_target_fields_is_flagged_unusable(self):
        self.assertFalse(self.by_target["litho_substrat"].usable)
        self.assertFalse(self.by_target["type_sol_inra"].usable)
        self.assertTrue(self.by_target["alea_argile"].usable)

    def test_field_column_matches_the_column_naming(self):
        cov = self.by_target["corine_code_2018"]
        self.assertEqual([cov.field_column(f) for f in cov.fields],
                         [c.name for c in cov.columns])
        single = self.by_target["type_vegetation"]
        self.assertEqual(single.field_column("nature"), "type_vegetation")


class LoadErrorsTest(unittest.TestCase):
    def write(self, content):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        handle.write(content if isinstance(content, str) else json.dumps(content))
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_missing_file(self):
        with self.assertRaisesRegex(ConfigError, "introuvable"):
            load_covariates(os.path.join(tempfile.gettempdir(), "nope_pcr.json"))

    def test_invalid_json(self):
        with self.assertRaisesRegex(ConfigError, "JSON invalide"):
            load_covariates(self.write("{not json"))

    def test_not_a_list(self):
        with self.assertRaisesRegex(ConfigError, "liste"):
            load_covariates(self.write({"a": 1}))

    def test_missing_required_key_names_the_entry(self):
        with self.assertRaisesRegex(ConfigError, "url"):
            load_covariates(self.write([{"couche_nom": "x", "covariable_cible": "y"}]))

    def test_unknown_query_type(self):
        entry = {"couche_nom": "x", "url": "u", "covariable_cible": "y", "type_requete": "magic"}
        with self.assertRaisesRegex(ConfigError, "magic"):
            load_covariates(self.write([entry]))

    def test_duplicate_target(self):
        entry = {"couche_nom": "x", "url": "u", "covariable_cible": "y"}
        with self.assertRaisesRegex(ConfigError, "deux fois"):
            load_covariates(self.write([entry, dict(entry, couche_nom="z")]))


if __name__ == "__main__":
    unittest.main()
