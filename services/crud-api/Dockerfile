# syntax=docker/dockerfile:1
# Stage: development (Node 22, NestJS dev server)
FROM node:22-alpine AS development

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY tsconfig.json nest-cli.json ./
COPY src/ ./src/
COPY prisma/ ./prisma/

RUN npx prisma generate

EXPOSE 3001

CMD ["npm", "run", "start:dev"]
