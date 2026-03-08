from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0008_brand_youtube_oauth_per_brand"),
    ]

    operations = [
        migrations.CreateModel(
            name="BrandYouTubeCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(blank=True, default="", max_length=120)),
                ("order_index", models.PositiveIntegerField(default=1)),
                ("is_active", models.BooleanField(default=True)),
                ("client_id", models.CharField(blank=True, default="", max_length=255)),
                ("client_secret", models.TextField(blank=True, default="")),
                ("redirect_uri", models.CharField(blank=True, default="", max_length=500)),
                ("channel_id", models.CharField(blank=True, default="", max_length=64)),
                ("account_name", models.CharField(blank=True, default="", max_length=120)),
                ("access_token", models.TextField(blank=True, default="")),
                ("refresh_token", models.TextField(blank=True, default="")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("quota_exceeded_until", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("brand", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="youtube_credentials", to="brands.brand")),
            ],
            options={
                "verbose_name": "Credencial YouTube da brand",
                "verbose_name_plural": "Credenciais YouTube da brand",
                "ordering": ["order_index", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="brandyoutubecredential",
            constraint=models.UniqueConstraint(fields=("brand", "order_index"), name="uniq_brand_youtube_credential_order"),
        ),
    ]
