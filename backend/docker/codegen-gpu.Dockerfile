FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

LABEL org.opencontainers.image.title="FAROS reproducible GPU experiment runtime"

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
    psutil==7.0.0 \
    pyyaml==6.0.2 \
    transformers==4.55.2 \
    accelerate==1.10.0

WORKDIR /workspace
