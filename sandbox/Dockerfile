# Build only from an explicit base and a standard, inspected Linux wheel.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY runtime.txt /tmp/runtime.txt
# Install the pinned aitest-kit wheel and envd dependencies in one resolver pass.
ARG AITEST_WHEEL_NAME
COPY aitest.whl /tmp/${AITEST_WHEEL_NAME}
RUN python -m pip install --no-cache-dir -r /tmp/runtime.txt /tmp/${AITEST_WHEEL_NAME} && python -m pip check \
    && rm /tmp/${AITEST_WHEEL_NAME}
COPY envd.whl /tmp/envd.whl
ARG WHEEL_NAME
RUN mv /tmp/envd.whl /tmp/${WHEEL_NAME} && python -m pip install --no-deps /tmp/${WHEEL_NAME} && rm /tmp/${WHEEL_NAME} \
    && mkdir -p /work/workspace /work/.envd-stage /state \
    && chmod 0755 /work && chmod 0700 /work/.envd-stage /state \
    && chown 1000:1000 /work/workspace && ln -s /work/workspace /workspace
USER 0:0
WORKDIR /workspace
ENTRYPOINT ["python", "-I", "-m", "naiyue_test_agent.envd", "serve"]
