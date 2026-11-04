FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY kg_agent ./kg_agent
COPY backend ./backend
RUN python -m pip install --no-cache-dir . \
    && python -m pip install --no-cache-dir -r backend/requirements-pdf.txt -r backend/requirements-rapidocr.txt
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
EXPOSE 10000
CMD ["python", "-m", "backend.app.review.server"]
