# Single image for both the web (gunicorn) and worker (celery) processes.
# Includes a JRE because the Celery worker runs PySpark.
# Pin bookworm: it ships OpenJDK 17 (Spark 3.5's supported JVM); the rolling
# `slim` tag moved to trixie, which dropped the openjdk-17 packages.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# openjdk-17 for Spark; procps for `ps`; curl for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless procps curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# S3A support for Spark (only exercised when STORAGE_BACKEND=s3). The versions
# MUST match the Hadoop that PySpark 3.5.1 bundles (Hadoop 3.3.4); the matching
# aws-java-sdk-bundle provides the DefaultAWSCredentialsProviderChain the app
# relies on (env keys locally, IAM role when deployed).
ARG HADOOP_AWS_VERSION=3.3.4
ARG AWS_SDK_BUNDLE_VERSION=1.12.262
RUN SPARK_JARS="$(python -c 'import os, pyspark; print(os.path.join(os.path.dirname(pyspark.__file__), "jars"))')" \
    && curl -fsSL -o "$SPARK_JARS/hadoop-aws-${HADOOP_AWS_VERSION}.jar" \
        "https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/${HADOOP_AWS_VERSION}/hadoop-aws-${HADOOP_AWS_VERSION}.jar" \
    && curl -fsSL -o "$SPARK_JARS/aws-java-sdk-bundle-${AWS_SDK_BUNDLE_VERSION}.jar" \
        "https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/${AWS_SDK_BUNDLE_VERSION}/aws-java-sdk-bundle-${AWS_SDK_BUNDLE_VERSION}.jar"

COPY . .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
