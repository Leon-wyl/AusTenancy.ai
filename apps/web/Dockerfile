# syntax=docker/dockerfile:1
# Stage: development (Node 22, Next.js dev server)
FROM node:22-alpine AS development

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY tsconfig.json next.config.ts ./
COPY app/ ./app/
COPY public/ ./public/

EXPOSE 3000

ENV NEXT_TELEMETRY_DISABLED=1

CMD ["npm", "run", "dev"]
