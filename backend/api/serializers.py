from __future__ import annotations

from rest_framework import serializers

from jobs.models import Job, UploadedFile


class UploadedFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadedFile
        fields = [
            "id",
            "original_name",
            "kind",
            "size_bytes",
            "columns",
            "created_at",
        ]
        read_only_fields = fields


class UploadedFileBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadedFile
        fields = ["id", "original_name", "columns"]


class JobSerializer(serializers.ModelSerializer):
    uploaded_file = UploadedFileBriefSerializer(read_only=True)

    class Meta:
        model = Job
        fields = [
            "id",
            "uploaded_file",
            "nl_prompt",
            "replacement_value",
            "target_columns",
            "status",
            "progress",
            "stage",
            "regex_pattern",
            "regex_source",
            "regex_explanation",
            "total_rows",
            "matched_rows",
            "result_columns",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class JobCreateSerializer(serializers.Serializer):
    uploaded_file = serializers.PrimaryKeyRelatedField(
        queryset=UploadedFile.objects.all()
    )
    nl_prompt = serializers.CharField(max_length=4000)
    replacement_value = serializers.CharField(
        max_length=4000, allow_blank=True, default=""
    )
    target_columns = serializers.ListField(
        child=serializers.CharField(max_length=512), allow_empty=False
    )

    def validate(self, attrs):
        uploaded_file: UploadedFile = attrs["uploaded_file"]
        available = set(uploaded_file.columns)
        unknown = [c for c in attrs["target_columns"] if c not in available]
        if unknown:
            raise serializers.ValidationError(
                {
                    "target_columns": (
                        f"Column(s) not in the uploaded file: "
                        f"{', '.join(unknown)}. Available: "
                        f"{', '.join(sorted(available))}"
                    )
                }
            )
        return attrs

    def create(self, validated_data) -> Job:
        return Job.objects.create(
            uploaded_file=validated_data["uploaded_file"],
            nl_prompt=validated_data["nl_prompt"],
            replacement_value=validated_data.get("replacement_value", ""),
            target_columns=validated_data["target_columns"],
            status=Job.Status.QUEUED,
            stage="queued",
        )
