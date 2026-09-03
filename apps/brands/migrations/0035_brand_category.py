from django.db import migrations, models


DEFAULT_CATEGORIES = [
    ("BUSINESS_MONEY", "Negócios / Dinheiro"),
    ("PSYCHOLOGY_RELATIONSHIPS", "Psicologia / Relacionamentos"),
    ("STORIES_CURIOSITIES", "Histórias e Curiosidades"),
    ("CONTROVERSIES_DEBATE", "Polêmicas / Debate"),
    ("COMEDY_HUMOR", "Comédia / Humor"),
]


def seed_default_categories(apps, schema_editor):
    Factory = apps.get_model("brands", "Factory")
    BrandCategory = apps.get_model("brands", "BrandCategory")
    Brand = apps.get_model("brands", "Brand")

    for factory in Factory.objects.all():
        used_codes = set(
            Brand.objects.filter(factory=factory)
            .exclude(theme_category="")
            .values_list("theme_category", flat=True)
        )
        for code, label in DEFAULT_CATEGORIES:
            BrandCategory.objects.update_or_create(
                factory=factory,
                code=code,
                defaults={"label": label, "is_active": True},
            )
        # Brands podem estar usando codes legacy fora dos 5 defaults (importação, scripts).
        # Garante que todo code em uso exista como categoria ativa.
        for extra_code in used_codes - {c for c, _ in DEFAULT_CATEGORIES}:
            BrandCategory.objects.update_or_create(
                factory=factory,
                code=extra_code,
                defaults={"label": extra_code.title(), "is_active": True},
            )


def remove_seeded_categories(apps, schema_editor):
    BrandCategory = apps.get_model("brands", "BrandCategory")
    BrandCategory.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("brands", "0034_alter_brand_long_slot_times"),
    ]

    operations = [
        migrations.AlterField(
            model_name="brand",
            name="theme_category",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Code de BrandCategory (por factory). 1:1 por factory.",
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="BrandCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(help_text="Identificador estável usado em Brand.theme_category e sugestões. Imutável.", max_length=40)),
                ("label", models.CharField(help_text="Nome exibido ao usuário. Pode ser renomeado sem afetar histórico.", max_length=120)),
                ("is_active", models.BooleanField(default=True, help_text="Soft-delete: categorias inativas não aparecem no frontend nem são enviadas à LLM.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("factory", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="categories", to="brands.factory")),
            ],
            options={
                "verbose_name": "Categoria da factory",
                "verbose_name_plural": "Categorias da factory",
                "ordering": ["factory_id", "label"],
            },
        ),
        migrations.AddConstraint(
            model_name="brandcategory",
            constraint=models.UniqueConstraint(fields=("factory", "code"), name="uniq_brand_category_code_per_factory"),
        ),
        migrations.AddConstraint(
            model_name="brandcategory",
            constraint=models.UniqueConstraint(fields=("factory", "label"), name="uniq_brand_category_label_per_factory"),
        ),
        migrations.RunPython(seed_default_categories, remove_seeded_categories),
    ]
