# syntax=docker/dockerfile:1.7
# Build from frontend/: docker build -t expense-frontend .   (backend/compose.yaml does this as the "frontend" service)
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Same-origin by default: nginx below proxies /api to the API service.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
# The API's address on the compose network. nginx's image substitutes it into the template at start.
ENV API_UPSTREAM=http://api:8000
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
