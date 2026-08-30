FROM python:3.12-slim

RUN python -m pip install --no-cache-dir \
    pytest==9.1.1 \
    pydantic==2.5.3 \
    numpy==2.2.6 \
    pandas==2.3.2 \
    scikit-learn==1.7.1 \
    pyyaml==6.0.2

WORKDIR /workspace
