FROM python:3.12-slim

LABEL org.opencontainers.image.title="FAROS reproducible CPU experiment runtime"

RUN python -m pip install --no-cache-dir \
    pytest==9.1.1 \
    flake8==7.3.0 \
    pydantic==2.5.3 \
    numpy==2.2.6 \
    pandas==2.3.2 \
    scikit-learn==1.7.1 \
    scipy==1.16.1 \
    matplotlib==3.10.5 \
    seaborn==0.13.2 \
    statsmodels==0.14.5 \
    psutil==7.0.0 \
    pyyaml==6.0.2

WORKDIR /workspace
