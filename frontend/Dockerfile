# syntax=docker/dockerfile:1
FROM node:20-slim AS base

WORKDIR /app

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY . .
ARG NEXT_PUBLIC_OAUTH_ENABLED=false
ENV NEXT_PUBLIC_OAUTH_ENABLED=$NEXT_PUBLIC_OAUTH_ENABLED
RUN npm run build

EXPOSE 3000
CMD ["npm", "start"]
