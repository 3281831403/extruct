# mypy: disallow_untyped_defs=False
import unittest

import extruct
from tests import get_testdata


class TestUniformProductPage(unittest.TestCase):
    """Acceptance tests for the uniform-mode product page normalization.

    The fixture page declares a ``<base href>`` and embeds the same product in
    microdata, JSON-LD (with @graph/@value/absolute @type) and OpenGraph,
    several of them using relative image and link URLs.
    """

    maxDiff = None

    base_url = "http://www.example.com/catalog/item.html"

    def setUp(self):
        self.body = get_testdata("misc", "uniform_product.html")

    def test_jsonld_graph_is_split_and_normalized(self):
        data = extruct.extract(
            self.body,
            base_url=self.base_url,
            syntaxes=["json-ld"],
            uniform=True,
        )
        items = data["json-ld"]
        # the @graph is split into two independent items, no @graph key remains
        self.assertEqual(len(items), 2)
        self.assertNotIn("@graph", items[0])

        product, breadcrumb = items
        # every item carries @context + @type with aligned short type names
        for item in items:
            self.assertIn("@context", item)
            self.assertIn("@type", item)
            self.assertTrue(item["@type"])
        self.assertEqual(product["@context"], "http://schema.org")
        self.assertEqual(product["@type"], "Product")
        self.assertEqual(breadcrumb["@type"], "BreadcrumbList")

        # @value is unpacked into the bare scalar
        self.assertEqual(product["sku"], "ANV-001")
        self.assertNotIn("@value", product)

        # nested objects are flattened with aligned types
        self.assertEqual(product["offers"]["@type"], "Offer")
        self.assertEqual(product["offers"]["availability"], "http://schema.org/InStock")
        self.assertEqual(
            product["offers"]["url"],
            "https://cdn.example.com/store/offers/anvil.html",
        )
        self.assertEqual(breadcrumb["itemListElement"][0]["@type"], "ListItem")

    def test_relative_urls_resolve_against_base_href(self):
        data = extruct.extract(
            self.body,
            base_url=self.base_url,
            syntaxes=["json-ld", "opengraph", "microdata"],
            uniform=True,
        )
        expected_image = "https://cdn.example.com/store/images/anvil_executive.jpg"
        expected_url = "https://cdn.example.com/products/anvil"

        product = next(
            item for item in data["json-ld"] if item.get("@type") == "Product"
        )
        self.assertEqual(product["image"], expected_image)
        self.assertEqual(product["url"], expected_url)

        og = data["opengraph"][0]
        self.assertEqual(og["og:image"], expected_image)
        self.assertEqual(og["og:url"], expected_url)

        microdata = data["microdata"][0]
        self.assertEqual(microdata["@type"], "Product")
        self.assertEqual(microdata["image"], expected_image)
        self.assertEqual(microdata["url"], expected_url)

        # all three syntaxes now describe the same image with one absolute URL
        self.assertEqual(
            {product["image"], og["og:image"], microdata["image"]},
            {expected_image},
        )

    def test_microdata_without_itemtype_is_flat(self):
        data = extruct.extract(
            self.body,
            base_url=self.base_url,
            syntaxes=["microdata"],
            uniform=True,
        )
        no_type = next(item for item in data["microdata"] if "@type" not in item)
        self.assertEqual(no_type["@context"], "http://schema.org")
        # no raw "properties"/"value" wrappers survive the uniform output
        self.assertNotIn("properties", no_type)
        self.assertNotIn("value", no_type)
        self.assertEqual(no_type["department"], "Hardware")
        # URL resolution still applies to the typeless item
        self.assertEqual(
            no_type["photo"],
            "https://cdn.example.com/store/images/department.jpg",
        )

    def test_base_url_used_when_no_base_href(self):
        body = get_testdata("misc", "product_microdata.html")
        data = extruct.extract(
            body,
            base_url="http://some-example.com/catalog/",
            syntaxes=["microdata", "json-ld", "opengraph"],
            uniform=True,
        )
        # no <base> in the document: base_url drives the resolution, and the
        # existing output shape is otherwise unchanged
        self.assertEqual(
            data["microdata"][0]["image"],
            "http://some-example.com/catalog/anvil_executive.jpg",
        )
        self.assertEqual(data["microdata"][0]["@type"], "Product")
        self.assertEqual(data["microdata"][0]["@context"], "http://schema.org")

    def test_raw_mode_is_unchanged(self):
        raw = extruct.extract(
            self.body, base_url=self.base_url, syntaxes=["json-ld", "microdata"]
        )
        # raw JSON-LD keeps the @graph wrapper untouched
        self.assertIn("@graph", raw["json-ld"][0])
        # raw microdata keeps its type/properties structure
        self.assertIn("type", raw["microdata"][0])
        self.assertIn("properties", raw["microdata"][0])
        # raw microdata URLs are resolved against base_url, not <base href>
        self.assertEqual(
            raw["microdata"][0]["properties"]["image"],
            "http://www.example.com/catalog/images/anvil_executive.jpg",
        )
