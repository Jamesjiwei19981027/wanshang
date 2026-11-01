from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import NewsArticle


class NewsListingSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.regular = []
        for index in range(13):
            cls.regular.append(NewsArticle.objects.create(
                title=f"Recent article {index}",
                slug=f"recent-{index}",
                summary="Published news",
                status=NewsArticle.Status.PUBLISHED,
                published_at=now - timedelta(days=index + 1),
            ))
        cls.featured = []
        for index in range(4):
            cls.featured.append(NewsArticle.objects.create(
                title=f"Featured article {index}",
                slug=f"featured-{index}",
                summary="Featured news",
                status=NewsArticle.Status.PUBLISHED,
                is_featured=True,
                published_at=now - timedelta(days=index + 30),
            ))
        NewsArticle.objects.create(
            title="Scheduled featured article", slug="scheduled-featured",
            summary="Not published yet", status=NewsArticle.Status.PUBLISHED,
            is_featured=True, published_at=now + timedelta(days=1),
        )
        NewsArticle.objects.create(
            title="Draft featured article", slug="draft-featured",
            summary="Not published", status=NewsArticle.Status.DRAFT,
            is_featured=True, published_at=now - timedelta(hours=1),
        )

    def setUp(self):
        self.client = APIClient()

    def test_default_listing_keeps_date_order_and_page_size(self):
        response = self.client.get("/api/news/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 17)
        self.assertEqual(len(response.data["results"]), 12)
        self.assertEqual(response.data["results"][0]["slug"], self.regular[0].slug)

    def test_homepage_selects_three_featured_articles_before_newer_regular_news(self):
        response = self.client.get("/api/news/", {"featured_first": "true", "page_size": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [article["slug"] for article in response.data["results"]],
            [article.slug for article in self.featured[:3]],
        )
        self.assertIn("featured_first=true", response.data["next"])
        self.assertIn("page_size=3", response.data["next"])

    def test_page_size_is_capped_and_regular_news_fill_after_featured(self):
        response = self.client.get("/api/news/", {"featured_first": "true", "page_size": 9999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 12)
        self.assertEqual(
            [article["slug"] for article in response.data["results"][:5]],
            [article.slug for article in self.featured] + [self.regular[0].slug],
        )

    def test_invalid_featured_order_is_reported(self):
        response = self.client.get("/api/news/", {"featured_first": "yes", "page_size": 3})
        self.assertEqual(response.status_code, 400)
        self.assertIn("featured_first", response.data)
