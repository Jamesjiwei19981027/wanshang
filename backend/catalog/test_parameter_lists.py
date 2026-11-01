from decimal import Decimal
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings

from assistant.models import AssistantSetting, QuickPrompt

from .models import Category, Product, ProductSpecValue, SpecField
from .tasks import extract_pdf_text
from .units import convert, find_quantities, format_quantity, parse_value_with_unit


class UnitConversionTests(TestCase):
    def test_conversions_round_trip(self):
        self.assertEqual(convert(Decimal("1"), "length", "in", "mm"), Decimal("25.4"))
        self.assertAlmostEqual(float(convert(Decimal("3500"), "mass", "lb", "kg")), 1587.573295, places=5)
        self.assertAlmostEqual(float(convert(Decimal("212"), "temperature", "f", "c")), 100.0, places=6)
        self.assertAlmostEqual(float(convert(Decimal("100"), "pressure", "psi", "bar")), 6.894757, places=5)
        self.assertEqual(format_quantity(Decimal("3500"), "mass", "lb"), "3,500 lb")
        self.assertEqual(format_quantity(Decimal("20"), "temperature", "c"), "20°C")

    def test_typed_quantities_are_found_in_free_text(self):
        mentions = find_quantities('Need 3,500 lbs axle with 95" hub face and 2413mm track')
        found = {(mention.quantity, mention.unit, mention.value) for mention in mentions}
        self.assertIn(("mass", "lb", Decimal("3500")), found)
        self.assertIn(("length", "in", Decimal("95")), found)
        self.assertIn(("length", "mm", Decimal("2413")), found)
        self.assertEqual(find_quantities("model 5in1x"), [])
        self.assertEqual(len(find_quantities("1588 kg")), 1)

    def test_value_parser_accepts_only_the_parameter_quantity(self):
        self.assertEqual(parse_value_with_unit("95", "length", "in"), (Decimal("95"), "in"))
        self.assertEqual(parse_value_with_unit("2,413 mm", "length", "in"), (Decimal("2413"), "mm"))
        with self.assertRaises(ValueError):
            parse_value_with_unit("95 kg", "length", "in")


class ParameterListModelTests(TestCase):
    def test_parameter_definitions_are_validated(self):
        category = Category.objects.create(name="Pumps", slug="pumps")
        for kwargs in (
            {"data_type": SpecField.DataType.NUMBER, "quantity": "speed-of-light"},
            {"data_type": SpecField.DataType.NUMBER, "quantity": "mass", "metric_unit": "mm"},
            {"data_type": SpecField.DataType.TEXT, "quantity": "mass"},
            {"data_type": SpecField.DataType.CHOICE, "choices": []},
            {"data_type": SpecField.DataType.CHOICE, "choices": ["A", "a"]},
            {"data_type": SpecField.DataType.TEXT, "choices": ["A"]},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                SpecField(category=category, key="flow", label="Flow", **kwargs).full_clean()
        field = SpecField(category=category, key="power", label="Power", data_type=SpecField.DataType.NUMBER, quantity="power")
        field.full_clean()

    def test_number_values_store_a_comparable_base_value(self):
        category = Category.objects.create(name="Pumps", slug="pumps")
        field = SpecField.objects.create(category=category, key="power", label="Power", data_type=SpecField.DataType.NUMBER, quantity="power")
        product = Product.objects.create(category=category, title="Pump", slug="pump", model="P1", sku="P1", short_description="Pump")
        value = ProductSpecValue.objects.create(product=product, field=field, number_value=Decimal("10"), entered_unit="hp")
        self.assertAlmostEqual(float(value.base_value), 7.457, places=3)
        with self.assertRaises(ValidationError):
            ProductSpecValue(product=product, field=field, number_value=Decimal("1"), entered_unit="kg").full_clean()


class WanshangParameterConfigurationTests(TestCase):
    def test_configuration_creates_axle_parameters_and_assistant_defaults_once(self):
        call_command("configure_wanshang", stdout=StringIO())
        axles = Category.objects.get(slug="trailer-axles")
        keys = list(axles.spec_fields.values_list("key", flat=True))
        for key in ("rated_capacity", "brake_type", "bolt_pattern", "hub_face", "spring_center", "trailer_weight"):
            self.assertIn(key, keys)
        self.assertFalse(Category.objects.get(slug="landing-gear").spec_fields.exists())
        self.assertTrue(AssistantSetting.objects.filter(pk=1).exists())
        self.assertTrue(QuickPrompt.objects.exists())

        field = axles.spec_fields.get(key="rated_capacity")
        field.label = "Load rating"
        field.save()
        QuickPrompt.objects.all().delete()
        QuickPrompt.objects.create(text="Staff prompt")
        call_command("configure_wanshang", stdout=StringIO())
        self.assertEqual(axles.spec_fields.get(key="rated_capacity").label, "Load rating")
        self.assertEqual(list(QuickPrompt.objects.values_list("text", flat=True)), ["Staff prompt"])

    @override_settings(DJANGO_ENV="development")
    def test_demo_catalogue_stays_in_its_own_category(self):
        call_command("configure_wanshang", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        demo = Category.objects.get(slug="demo-trailer-axles")
        self.assertEqual(Product.objects.filter(category=demo).count(), 3)
        self.assertFalse(Product.objects.exclude(category=demo).exists())
        metric = Product.objects.get(slug="demo-ta-7000-hd")
        capacity = metric.spec_values.get(field__key="rated_capacity")
        self.assertEqual(capacity.entered_unit, "kg")


class ProductAdminParameterFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="admin-parameters", email="a@example.com", password="x")
        self.client.force_login(self.user)
        self.category = Category.objects.create(name="Landing Gear", slug="landing-gear")
        SpecField.objects.create(category=self.category, key="lifting_capacity", label="Lifting capacity", data_type=SpecField.DataType.NUMBER, quantity="mass", required=True, position=1)
        SpecField.objects.create(category=self.category, key="gear_type", label="Gear type", data_type=SpecField.DataType.CHOICE, choices=["Two-speed", "One-speed"], position=2)
        SpecField.objects.create(category=self.category, key="buyer_only", label="Buyer only", data_type=SpecField.DataType.TEXT, ask_in_rfq=True, used_by_products=False, position=3)

    def test_add_form_shows_the_category_parameter_list(self):
        response = self.client.get("/admin/catalog/product/add/", {"category": self.category.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Lifting capacity")
        self.assertContains(response, 'name="spec__lifting_capacity__unit"')
        self.assertContains(response, "Two-speed")
        self.assertNotContains(response, "Buyer only")
        empty = self.client.get("/admin/catalog/product/add/")
        self.assertContains(empty, "Choose a category")

    def test_saving_stores_parameter_values_and_search_text(self):
        payload = {
            "title": "Landing leg", "slug": "landing-leg", "model": "LG-24", "sku": "LG-24", "category": self.category.pk,
            "status": "published", "position": 0, "short_description": "Two-speed landing gear",
            "spec__lifting_capacity": "24000", "spec__lifting_capacity__unit": "kg", "spec__gear_type": "Two-speed",
            "translations": "{}", "is_customizable": "on",
            "images-TOTAL_FORMS": 0, "images-INITIAL_FORMS": 0, "documents-TOTAL_FORMS": 0, "documents-INITIAL_FORMS": 0,
            "_save": "Save",
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(f"/admin/catalog/product/add/?category={self.category.pk}", payload)
        self.assertEqual(response.status_code, 302, response.content[:2000])
        product = Product.objects.get(slug="landing-leg")
        values = {value.field.key: value for value in product.spec_values.select_related("field")}
        self.assertEqual(values["lifting_capacity"].number_value, Decimal("24000"))
        self.assertEqual(values["lifting_capacity"].entered_unit, "kg")
        self.assertEqual(values["gear_type"].text_value, "Two-speed")
        product.refresh_from_db()
        self.assertIn("52,911 lb", product.search_document)

        missing_required = dict(payload, slug="landing-leg-2", sku="LG-25", spec__lifting_capacity="")
        response = self.client.post(f"/admin/catalog/product/add/?category={self.category.pk}", missing_required)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(slug="landing-leg-2").exists())


class DocumentTextExtractionTests(TestCase):
    def test_pdf_text_layer_is_extracted(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        page = writer.add_blank_page(width=200, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 20 100 Td (Torque 150 Nm) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        buffer = BytesIO()
        writer.write(buffer)
        buffer.seek(0)
        self.assertIn("Torque 150 Nm", extract_pdf_text(buffer))

    def test_new_pdf_documents_queue_extraction(self):
        category = Category.objects.create(name="Hubs", slug="hubs")
        product = Product.objects.create(category=category, title="Hub", slug="hub", model="H1", sku="H1", short_description="Hub")
        from django.core.files.uploadedfile import SimpleUploadedFile

        from .models import ProductDocument
        from .tests import isolated_product_document_storage

        with isolated_product_document_storage(), patch("catalog.tasks.extract_document_text_task.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                document = ProductDocument.objects.create(
                    product=product, title="Manual", document_type=ProductDocument.DocumentType.INSTALLATION,
                    file=SimpleUploadedFile("manual.pdf", b"%PDF-1.4\n%%EOF\n", content_type="application/pdf"),
                )
        delay.assert_called_once_with(document.pk)
