from django.test import SimpleTestCase

from invoice_templates.v3.registry import DEFAULT_TEMPLATE_CODE, REGISTRY, get_definition
from invoice_templates.v3.registry.base import TemplateDefinition
from invoice_templates.v3.registry.fields import (
    COLOR_KEYS, FONT_CHOICES, font_options, validate_color, validate_font,
)
from invoice_templates.v3.services.configuration import (
    ConfigurationError, effective_configuration, migrate_configuration, validate_configuration,
)


class RegistryTests(SimpleTestCase):
    def test_initial_templates_registered(self):
        self.assertEqual(set(REGISTRY), {"standard", "classic", "simple"})
        self.assertEqual(DEFAULT_TEMPLATE_CODE, "standard")
        self.assertIsNone(get_definition("does-not-exist"))

    def test_every_template_offers_the_same_six_options(self):
        for code, definition in REGISTRY.items():
            defaults = definition.defaults
            self.assertEqual(set(defaults), {"colors", "font"}, code)
            self.assertEqual(tuple(defaults["colors"]), COLOR_KEYS, code)
            self.assertIn(defaults["font"], FONT_CHOICES, code)
            self.assertTrue(callable(definition.renderer_class))

    def test_defaults(self):
        self.assertEqual(REGISTRY["standard"].defaults, {
            "colors": {"background": "#FFFFFF", "primary": "#6C5CE7", "text": "#374151",
                       "border": "#D9D9D9", "cancelled": "#EF4444"},
            "font": "inter",
        })
        self.assertEqual(REGISTRY["classic"].defaults["colors"]["background"], "#FDF5EC")
        self.assertEqual(REGISTRY["classic"].defaults["font"], "inter")
        self.assertEqual(REGISTRY["simple"].defaults, {
            "colors": {"background": "#FFFFFF", "primary": "#7C3AED", "text": "#1F2937",
                       "border": "#E5E7EB", "cancelled": "#EF4444"},
            "font": "inter",
        })

    def test_defaults_are_independent_copies(self):
        REGISTRY["classic"].defaults["colors"]["primary"] = "#000000"
        self.assertEqual(REGISTRY["classic"].defaults["colors"]["primary"], "#2B2B2B")

    def test_font_options(self):
        self.assertEqual(font_options()[0], {"value": "inter", "label": "Inter"})
        for missing in ("atteron", "zawi_29lt"):  # no font files supplied
            self.assertNotIn(missing, [f["value"] for f in font_options()])

    def test_invalid_definition_fails_at_import_time(self):
        good = REGISTRY["classic"]
        with self.assertRaises(ValueError):
            TemplateDefinition(code="x", name="x", description="", version=1,
                               default_colors={"background": "#FFFFFF"}, default_font="inter",
                               renderer_path=good.renderer_path)
        with self.assertRaises(ValueError):
            TemplateDefinition(code="x", name="x", description="", version=1,
                               default_colors=good.default_colors, default_font="comic_sans",
                               renderer_path=good.renderer_path)


class ValueValidationTests(SimpleTestCase):
    def test_color(self):
        self.assertEqual(validate_color("primary", " #a1b2c3 "), ("#A1B2C3", None))
        for bad in ("red", "#abc", "#GGGGGG", "#12345678", 123, None, "#123456; color: red"):
            self.assertEqual(validate_color("primary", bad)[1], "'colors.primary' must be a color in #RRGGBB format.")

    def test_font(self):
        self.assertEqual(validate_font("dejavu_sans"), ("dejavu_sans", None))
        for bad in ("atteron", "zawi_29lt", "Comic Sans", None, 1):
            self.assertIsNotNone(validate_font(bad)[1], bad)


class ConfigurationServiceTests(SimpleTestCase):
    definition = REGISTRY["classic"]

    def test_partial_input_returns_complete_configuration(self):
        config = validate_configuration(self.definition, {"colors": {"primary": "#abcdef"}})
        expected = self.definition.defaults
        expected["colors"]["primary"] = "#ABCDEF"
        self.assertEqual(config, expected)
        self.assertEqual(validate_configuration(self.definition, {}), self.definition.defaults)

    def test_partial_input_merges_onto_base(self):
        base = validate_configuration(self.definition, {"colors": {"primary": "#111111"}, "font": "inter"})
        config = validate_configuration(self.definition, {"colors": {"text": "#222222"}}, base=base)
        self.assertEqual(
            (config["colors"]["primary"], config["colors"]["text"], config["font"]),
            ("#111111", "#222222", "inter"),
        )

    def test_strict_validation_collects_every_error(self):
        with self.assertRaises(ConfigurationError) as ctx:
            validate_configuration(self.definition, {
                "colors": {"primary": "blue", "accent": "#000000", "text": "#123456"},
                "font": "comic_sans",
                "size.title": 40,
            })
        self.assertEqual(set(ctx.exception.errors), {"colors.primary", "colors.accent", "font", "size.title"})
        self.assertEqual(ctx.exception.errors["colors.accent"], ["'colors.accent' is not a supported setting."])

    def test_strict_validation_rejects_bad_shapes(self):
        for bad in (None, [], "x"):
            with self.assertRaises(ConfigurationError):
                validate_configuration(self.definition, bad)
        with self.assertRaises(ConfigurationError) as ctx:
            validate_configuration(self.definition, {"colors": "red"})
        self.assertEqual(ctx.exception.errors, {"colors": ["'colors' must be an object."]})

    def test_effective_configuration_is_tolerant(self):
        config = effective_configuration(self.definition, {
            "colors": {"primary": "#123456", "text": "not-a-color", "accent": "#000000"},
            "font": "comic_sans", "size.title": 40,
        })
        self.assertEqual(config["colors"]["primary"], "#123456")
        self.assertEqual(config["colors"]["text"], self.definition.defaults["colors"]["text"])
        self.assertEqual(config["font"], self.definition.default_font)
        self.assertEqual(set(config), {"colors", "font"})
        self.assertEqual(effective_configuration(self.definition, None), self.definition.defaults)

    def test_version_migrations_applied_in_order(self):
        definition = TemplateDefinition(
            code="t", name="t", description="", version=3,
            default_colors=self.definition.default_colors, default_font="inter",
            renderer_path=self.definition.renderer_path,
            migrations={
                1: lambda c: {**c, "font": c.pop("old_font", "inter")},
                2: lambda c: {**c, "font": "dejavu_sans" if c["font"] == "dejavu" else c["font"]},
            },
        )
        self.assertEqual(migrate_configuration(definition, {"old_font": "dejavu"}, 1)["font"], "dejavu_sans")
        self.assertEqual(migrate_configuration(definition, {"font": "inter"}, 3)["font"], "inter")
