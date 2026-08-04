FROM python:3.12-slim

WORKDIR /app

RUN pip install uv

# CPU-only torch wheels: no GPU in the container, and CUDA wheels triple the image size
ENV UV_TORCH_BACKEND=cpu

# install deps from the manifest first so source edits don't invalidate this layer
COPY pyproject.toml .
RUN uv pip install --system -r pyproject.toml --extra dev

COPY . .
RUN uv pip install --system --no-deps -e .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
