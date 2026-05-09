from django.db import models
from django.db.models import F
from django.utils import timezone


class FormProcessingStat(models.Model):
    """
    Stores cumulative counts of how many documents the forms app
    successfully sorted vs. left unsorted.
    """

    key = models.CharField(max_length=64, unique=True, default="global")
    sorted_count = models.PositiveIntegerField(default=0)
    unsorted_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Form Processing Stat"
        verbose_name_plural = "Form Processing Stats"

    @classmethod
    def get_default(cls):
        """Return the singleton stats row (create it if missing)."""
        stats, _ = cls.objects.get_or_create(key="global")
        return stats

    @classmethod
    def increment(cls, sorted_delta=0, unsorted_delta=0):
        """Atomically increment totals and return the updated stats."""
        if sorted_delta == 0 and unsorted_delta == 0:
            return cls.get_default()

        stats = cls.get_default()
        cls.objects.filter(pk=stats.pk).update(
            sorted_count=F("sorted_count") + sorted_delta,
            unsorted_count=F("unsorted_count") + unsorted_delta,
            updated_at=timezone.now(),
        )
        stats.refresh_from_db()
        return stats
