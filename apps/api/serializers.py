from django.contrib.auth import get_user_model
from rest_framework import serializers
from apps.brands.models import Brand, BrandAsset

User = get_user_model()
from apps.mediahub.models import SourceVideo
from apps.cuts.models import Cut
from apps.jobs.models import Job, JobCut, RenderOutput, ScheduledPost


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = ["id", "name", "slug"]
        extra_kwargs = {"slug": {"required": False}}

    def create(self, validated_data):
        if not validated_data.get("slug"):
            from django.utils.text import slugify
            validated_data["slug"] = slugify(validated_data["name"])
        return super().create(validated_data)


class BrandAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandAsset
        fields = ["id", "brand", "asset_type", "label", "file"]


class SourceVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceVideo
        fields = ["id", "brand", "title", "file", "created_at"]
        read_only_fields = ["created_at"]


class CutSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Cut
        fields = ["id", "source", "name", "start_tc", "end_tc", "format", "duration", "file", "file_url", "created_at"]
        read_only_fields = ["created_at", "file"]

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class CutBulkCreateSerializer(serializers.Serializer):
    """Cria múltiplos cortes de uma vez."""
    source = serializers.PrimaryKeyRelatedField(queryset=SourceVideo.objects.all())
    cuts = serializers.ListField(
        child=serializers.DictField(child=serializers.CharField()),
        min_length=1,
    )

    def validate_source(self, value):
        request = self.context.get("request")
        if request and request.user and value.user_id and value.user_id != request.user.id:
            raise serializers.ValidationError("Source não pertence ao usuário.")
        return value

    def validate_cuts(self, value):
        for i, c in enumerate(value):
            if "start_tc" not in c or "end_tc" not in c:
                raise serializers.ValidationError(
                    f"Corte {i}: start_tc e end_tc são obrigatórios."
                )
        return value

    def create(self, validated_data):
        source = validated_data["source"]
        cuts_data = validated_data["cuts"]
        created = []
        for c in cuts_data:
            cut = Cut.objects.create(
                source=source,
                brand=source.brand,
                name=c.get("name", ""),
                start_tc=c["start_tc"],
                end_tc=c["end_tc"],
            )
            created.append(cut)
        return created


class JobCutInlineSerializer(serializers.ModelSerializer):
    cut_id = serializers.PrimaryKeyRelatedField(
        queryset=Cut.objects.all(), source="cut"
    )

    class Meta:
        model = JobCut
        fields = ["cut_id"]

    def to_internal_value(self, data):
        if isinstance(data, int):
            return {"cut_id": data}
        return super().to_internal_value(data)


class JobSerializer(serializers.ModelSerializer):
    cut_ids = serializers.ListField(
        child=serializers.IntegerField(),
        write_only=True,
        required=True,
    )
    output_url = serializers.SerializerMethodField(read_only=True)
    scheduled_summary = serializers.SerializerMethodField(read_only=True)
    can_delete = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Job
        fields = [
            "id",
            "name",
            "archived",
            "cut_ids",
            "target_platforms",
            "make_vertical",
            "intro_asset",
            "outro_asset",
            "transition",
            "transition_duration",
            "status",
            "progress",
            "output_url",
            "error",
            "created_at",
            "started_at",
            "finished_at",
            "scheduled_summary",
            "can_delete",
            "subtitle_status",
            "subtitle_segments",
            "subtitle_style",
            "subtitle_error",
        ]
        read_only_fields = [
            "status", "progress", "error", "archived",
            "created_at", "started_at", "finished_at",
        ]

    def get_output_url(self, obj):
        try:
            out = obj.output
            if out and out.file:
                request = self.context.get("request")
                if request:
                    return request.build_absolute_uri(out.file.url)
                return out.file.url
        except (RenderOutput.DoesNotExist, AttributeError):
            pass
        return None

    def get_scheduled_summary(self, obj):
        posts = obj.scheduled_posts.all()
        if not posts:
            return None
        done = sum(1 for p in posts if p.status == "DONE")
        pending = sum(1 for p in posts if p.status in ("PENDING", "POSTING"))
        return {"total": len(posts), "posted": done, "pending": pending}

    def get_can_delete(self, obj):
        from apps.jobs.services.job_actions import has_pending_scheduled_posts
        return not has_pending_scheduled_posts(obj)

    def create(self, validated_data):
        cut_ids = validated_data.pop("cut_ids")
        request = self.context.get("request")
        if request and request.user:
            validated_data["user"] = request.user

        job = Job.objects.create(**validated_data)
        for order, cut_id in enumerate(cut_ids):
            JobCut.objects.create(job=job, cut_id=cut_id, order=order)
        return job


class JobRunSerializer(serializers.Serializer):
    """Apenas para validação do endpoint run."""
    pass


class UserRegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["username", "email", "password"]
        extra_kwargs = {"email": {"required": False}}

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user


class ScheduledPostSerializer(serializers.ModelSerializer):
    job = serializers.PrimaryKeyRelatedField(queryset=Job.objects.all())
    job_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ScheduledPost
        fields = [
            "id",
            "job",
            "job_name",
            "platforms",
            "scheduled_at",
            "status",
            "error",
            "created_at",
            "posted_at",
        ]
        read_only_fields = ["status", "error", "created_at", "posted_at"]

    def get_job_name(self, obj):
        return obj.job.name or f"Job #{obj.job.id}"
